/**
 * ============================================================================
 * login_script.js — Lógica de la página de login — v5.0
 * ============================================================================
 * Específico de login.html. Usa las funciones públicas de auth.js
 * (attemptLogin) pero no duplica su lógica de almacenamiento de sesión.
 *
 * Responsabilidades de este archivo:
 *   1. Verificar el estado del backend al cargar la página (UX preventiva).
 *   2. Manejar el submit del formulario con estados de carga.
 *   3. Mostrar errores EXACTOS del backend, sin reinterpretarlos.
 *   4. Si ya hay una sesión válida (operador refrescó login.html por error,
 *      o volvió atrás con el navegador), redirigir directo al panel.
 *
 * Autor: Francisco Bevilacqua | Versión: 1.0.0
 * ============================================================================
 */

'use strict';

function el(id) { return document.getElementById(id); }

/* ── Verificación de estado del backend (signature element visual) ─── */

/**
 * Consulta GET /health del backend y actualiza el indicador visual.
 * No requiere autenticación — es deliberadamente el endpoint más simple
 * posible, para que un backend caído se detecte rápido y sin ambigüedad
 * (si /health falla, el problema es claramente "el backend no está
 * corriendo", no "mis credenciales son malas").
 */
async function checkBackendStatus() {
    const statusBox = el('backend-status');
    const statusVal  = el('backend-status-val');

    // Se usa la misma apiBase que auth.js para no duplicar configuración.
    const apiBase = (typeof AUTH_CFG !== 'undefined') ? AUTH_CFG.apiBase : 'http://localhost:8000';

    try {
        const response = await fetch(`${apiBase}/health`, { method: 'GET' });
        if (response.ok) {
            statusBox.classList.remove('err');
            statusBox.classList.add('ok');
            statusVal.textContent = 'Disponible';
            return;
        }
        throw new Error('respuesta no OK');
    } catch {
        statusBox.classList.remove('ok');
        statusBox.classList.add('err');
        statusVal.textContent = 'No disponible';
    }
}

/* ── Manejo del formulario de login ──────────────────────────────────── */

function showError(message) {
    const errorBox = el('login-error');
    errorBox.textContent = message;
    errorBox.classList.remove('hidden');
}

function hideError() {
    el('login-error').classList.add('hidden');
}

function setLoadingState(isLoading) {
    const btn = el('btn-login');
    btn.disabled = isLoading;
    btn.classList.toggle('loading', isLoading);
    btn.textContent = isLoading ? '⏳ VERIFICANDO...' : '🔓 AUTORIZAR ACCESO';
}

async function handleLoginSubmit(event) {
    event.preventDefault(); // evita el submit nativo del <form> (recarga de página)

    hideError();

    const username = el('input-username').value.trim();
    const password = el('input-password').value; // sin trim: un espacio podría ser parte de la contraseña real

    if (!username || !password) {
        showError('Ingresá usuario y contraseña.');
        return;
    }

    setLoadingState(true);

    try {
        // attemptLogin (definida en auth.js) ya guarda la sesión en
        // sessionStorage si tiene éxito — este script no toca sessionStorage
        // directamente, respetando la separación de responsabilidades.
        await attemptLogin(username, password);
        window.location.href = 'index.html';
    } catch (err) {
        // [SEC] err.message es EXACTAMENTE lo que devolvió el backend
        // (ver auth.js, attemptLogin) — incluye el caso de rate limiting
        // (HTTP 429) con su propio mensaje, sin que este script necesite
        // distinguir códigos de estado manualmente.
        showError(err.message);
        setLoadingState(false);
    }
}

/* ── Inicialización de la página ─────────────────────────────────────── */

window.addEventListener('DOMContentLoaded', async () => {
    checkBackendStatus();

    // Si ya existe una sesión válida (ej. el operador volvió atrás con el
    // navegador desde index.html, o refrescó login.html sin haber hecho
    // logout), se lo redirige directo al panel en vez de mostrarle el
    // formulario de login innecesariamente.
    const existingUser = await verifySessionRemote();
    if (existingUser) {
        window.location.href = 'index.html';
        return;
    }

    el('login-form').addEventListener('submit', handleLoginSubmit);

    // Foco inicial en el campo de usuario, para que el operador pueda
    // empezar a tipear inmediatamente sin un clic adicional.
    el('input-username').focus();
});
