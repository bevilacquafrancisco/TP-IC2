/**
 * ============================================================================
 * Brazo Robótico Pick & Place — Módulo de Autenticación (auth.js) — v5.0
 * ============================================================================
 *
 * Responsabilidad única (SRP): este módulo es la ÚNICA parte de la GUI que
 * conoce el backend FastAPI y el formato de los JWT. robot_script.js
 * NO debe importar jose, decodificar tokens, ni saber que existe un backend
 * REST — solo consume las funciones públicas expuestas al final de este
 * archivo (getToken, getOperatorName, requireSession, logout).
 *
 * Esta separación es deliberada (ver ADR-06 en planificacion.md): si el
 * día de mañana el mecanismo de autenticación cambia (ej. SSO institucional),
 * solo este archivo se reescribe — la lógica de control del brazo robótico
 * en robot_script.js queda intacta.
 *
 * FLUJO DE USO:
 *   1. login.html carga este script + llama a attemptLogin(user, pass)
 *      desde el formulario.
 *   2. Si el login es exitoso, se guarda el JWT en sessionStorage y se
 *      redirige a index.html.
 *   3. index.html carga este script ANTES que robot_script_v4-1.js, y
 *      ejecuta requireSession() de forma SÍNCRONA respecto al pintado del
 *      panel (ver ADR-07): si no hay sesión válida, redirige a login.html
 *      antes de que el panel de control se vuelva visible/interactivo.
 *
 * [SEC] El JWT se guarda en sessionStorage, NO localStorage (ver ADR-05):
 * se borra automáticamente al cerrar la pestaña/navegador, reduciendo la
 * ventana de exposición si la PC de la demo queda desatendida.
 *
 * Autor: Francisco Bevilacqua | Versión: 1.0.0
 * ============================================================================
 */

'use strict';

/* ── Configuración ──────────────────────────────────────────────────── */

/**
 * [SEC] URL del backend de autenticación. Ajustar a la IP real de la PC
 * si la GUI se sirve desde una máquina distinta a donde corre el backend
 * (en la demo típica, ambos corren en la misma PC, por lo que localhost
 * es correcto). Si se sirve la GUI desde otra máquina de la red, cambiar
 * a la IP local del backend (la misma IP que usa Mosquitto, ver Fase 1).
 */
const AUTH_CFG = {
    apiBase: 'http://localhost:8000',
    storageKeyToken:    'robot_jwt_token',
    storageKeyUsername: 'robot_jwt_username',
    storageKeyExpiry:   'robot_jwt_expiry',   // timestamp ms, calculado client-side
};

/* ── Almacenamiento de sesión (capa interna, no exportada) ───────────── */

/**
 * Guarda la sesión completa tras un login exitoso.
 * @param {string} token   - JWT recibido del backend.
 * @param {string} username - Nombre del operador autenticado.
 * @param {number} expiresInMinutes - Minutos de validez informados por el backend.
 */
function _storeSession(token, username, expiresInMinutes) {
    const expiryTimestamp = Date.now() + expiresInMinutes * 60 * 1000;
    sessionStorage.setItem(AUTH_CFG.storageKeyToken, token);
    sessionStorage.setItem(AUTH_CFG.storageKeyUsername, username);
    sessionStorage.setItem(AUTH_CFG.storageKeyExpiry, String(expiryTimestamp));
}

/** Limpia toda la sesión almacenada. */
function _clearSession() {
    sessionStorage.removeItem(AUTH_CFG.storageKeyToken);
    sessionStorage.removeItem(AUTH_CFG.storageKeyUsername);
    sessionStorage.removeItem(AUTH_CFG.storageKeyExpiry);
}

/**
 * Verifica si el JWT almacenado localmente ya venció, SIN llamar al backend.
 *
 * [SEC] Esto es una optimización de UX, no una verificación de seguridad:
 * el cliente puede tener su reloj desincronizado o estar siendo manipulado
 * desde DevTools. La verificación de seguridad REAL ocurre en el backend
 * (GET /auth/verify decodifica y valida la firma + exp del JWT con su
 * propio reloj de servidor). Este chequeo local solo evita una llamada de
 * red innecesaria cuando es obvio que el token ya expiró.
 *
 * @returns {boolean} true si el timestamp guardado ya pasó.
 */
function _isLocallyExpired() {
    const expiry = sessionStorage.getItem(AUTH_CFG.storageKeyExpiry);
    if (!expiry) return true;
    return Date.now() >= parseInt(expiry, 10);
}

/* ── API pública: Login ────────────────────────────────────────────── */

/**
 * Intenta autenticar al operador contra el backend FastAPI.
 *
 * [SEC] Los errores HTTP del backend (401, 429) se propagan con su
 * mensaje EXACTO (no se reinterpretan ni se enriquecen en el frontend).
 * Esto es deliberado: el backend ya decidió cuidadosamente qué mensaje
 * mostrar para no permitir enumeración de usuarios (ver app/routers/auth.py
 * del backend) — la GUI no debe deshacer ese trabajo agregando lógica
 * adicional como "si el status es 401 y el username tiene tal formato...".
 *
 * @param {string} username - Usuario ingresado en el formulario.
 * @param {string} password - Contraseña ingresada en el formulario.
 * @returns {Promise<{username: string}>} Datos básicos de la sesión iniciada.
 * @throws {Error} Con `.message` igual al `detail` devuelto por el backend,
 *   o un mensaje de conectividad si el backend no responde.
 */
async function attemptLogin(username, password) {
    let response;
    try {
        response = await fetch(`${AUTH_CFG.apiBase}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
    } catch (networkError) {
        // [SEC] Mensaje genérico de conectividad — no se expone el error
        // crudo del navegador (que podría revelar detalles de red internos
        // en algunos casos, ej. CORS misconfigurado vs. backend caído).
        throw new Error(
            'No se pudo contactar al servidor de autenticación. ' +
            'Verificá que el backend esté corriendo (uvicorn) y que la URL ' +
            `configurada (${AUTH_CFG.apiBase}) sea correcta.`
        );
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        // Reenviar el detail del backend tal cual (401: credenciales,
        // 429: rate limit). Ver nota [SEC] en el docstring de la función.
        throw new Error(data.detail || `Error de autenticación (HTTP ${response.status}).`);
    }

    _storeSession(data.access_token, data.username, data.expires_in_minutes);
    return { username: data.username };
}

/* ── API pública: Verificación y guard de página ──────────────────── */

/**
 * Verifica la sesión actual contra el backend (no solo localmente).
 * Usado por requireSession() y disponible también para chequeos manuales.
 *
 * @returns {Promise<string|null>} Username si la sesión es válida, null si no.
 */
async function verifySessionRemote() {
    const token = sessionStorage.getItem(AUTH_CFG.storageKeyToken);
    if (!token) return null;

    try {
        const response = await fetch(`${AUTH_CFG.apiBase}/auth/verify`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) {
            _clearSession();
            return null;
        }
        const data = await response.json();
        return data.username;
    } catch {
        // Backend inalcanzable: por seguridad, se trata como sesión inválida
        // en vez de asumir que es válida y dejar pasar al operador sin
        // poder confirmar nada (fail-closed, no fail-open).
        return null;
    }
}

/**
 * Guard de página: debe llamarse al principio de index.html, ANTES de
 * que el panel de control se vuelva visible (ver ADR-07).
 *
 * Estrategia de dos pasos:
 *   1. Chequeo local instantáneo (sin red): si no hay token o ya expiró
 *      según el timestamp guardado, redirige inmediatamente sin esperar
 *      ninguna respuesta de red — UX más rápida para el caso común de
 *      "sesión vencida hace rato".
 *   2. Si el chequeo local pasa, confirma contra el backend (la fuente de
 *      verdad real) antes de considerar la sesión definitivamente válida.
 *
 * @returns {Promise<string>} Username del operador si la sesión es válida.
 *   Si no lo es, esta función redirige a login.html y NUNCA resuelve la
 *   promesa (el caller debe asumir que la ejecución se detiene aquí).
 */
async function requireSession() {
    if (_isLocallyExpired()) {
        _redirectToLogin();
        return new Promise(() => {}); // nunca resuelve; la página ya está redirigiendo
    }

    const username = await verifySessionRemote();
    if (!username) {
        _redirectToLogin();
        return new Promise(() => {});
    }

    return username;
}

function _redirectToLogin() {
    window.location.href = 'login.html';
}

/* ── API pública: Logout ──────────────────────────────────────────── */

/**
 * Cierra la sesión del operador: limpia el JWT y redirige al login.
 * Se llama desde el botón de logout del panel principal.
 */
function logout() {
    _clearSession();
    window.location.href = 'login.html';
}

/* ── API pública: Accesores síncronos (para robot_script_v4-1.js) ──── */

/**
 * Devuelve el JWT actualmente almacenado, o null si no hay sesión.
 * Usado únicamente para fines informativos en esta fase (el JWT NO se
 * envía al broker MQTT — la autenticación MQTT usa credenciales propias
 * del usuario gui_operator, ver Fase 1. El JWT identifica al OPERADOR
 * humano ante el backend; las credenciales MQTT identifican a "la GUI"
 * como aplicación ante el broker. Son dos capas independientes, ver
 * nota de diseño en PARCHE_v5_cfg_mqtt.js de la Fase 1).
 *
 * @returns {string|null}
 */
function getToken() {
    return sessionStorage.getItem(AUTH_CFG.storageKeyToken);
}

/**
 * Devuelve el nombre del operador autenticado actualmente, o null.
 * @returns {string|null}
 */
function getOperatorName() {
    return sessionStorage.getItem(AUTH_CFG.storageKeyUsername);
}
