/**
 * ============================================================================
 * Brazo Robótico Pick & Place — JavaScript v4.0
 * ============================================================================
 *
 * MEJORAS v4.0 (sobre v3.1):
 *
 * 1. QoS 1 en suscripción y comandos críticos
 *    La GUI suscribe a robot/log con QoS 1. Los comandos de acción crítica
 *    (semi_decision, pallet_clear, set_mode, move) se publican con QoS 1.
 *    Los comandos de servo se mantienen en QoS 0 (alta frecuencia).
 *
 * 2. Deduplicación via msg_id
 *    Cada comando crítico incluye un campo msg_id único (timestamp + random).
 *    El firmware descarta duplicados QoS 1 con el mismo msg_id.
 *
 * 3. Re-sincronización activa tras reconexión
 *    Al conectarse al broker, solicita estado completo del ESP32 tras 2s.
 *
 * 4. Persistencia de estado en localStorage
 *    El estado de pallets se guarda en localStorage y se restaura al recargar.
 *
 * 5. Indicador visual de "Esperando confirmación" (pending state)
 *    Botones críticos muestran ⏳ y se deshabilitan hasta recibir confirmación
 *    o que expire un timeout de seguridad de 8 segundos.
 *
 * 6. Watchdog de sesión ESP32 (silence detector)
 *    30s sin mensajes → badge naranja "Silencioso".
 *    60s sin mensajes → badge rojo "Offline".
 *
 * MEJORAS v4.1 (sobre v4.0):
 *
 * 7. Bloqueo de botón de pallet lleno en SEMI_AUTO
 *    Al recibir 'pallet_full', el botón del pallet correspondiente
 *    (btn-semi-p1 / btn-semi-p2) se deshabilita con texto indicativo
 *    hasta que el operador vacíe el pallet y se reciba 'pallet_cleared'.
 *    Esto impide enviar una decisión hacia un pallet lleno y que el
 *    ESP32 rechace el comando silenciosamente.
 *
 * MEJORAS v4.2 (sobre v4.1):
 *
 * 9. Rediseño del sistema de actividad del ESP32 — dos timers independientes
 *    El sistema anterior usaba un único timer que se reiniciaba con CADA mensaje,
 *    lo que impedía que el aviso de "inactividad" apareciera mientras hubiera
 *    cualquier tráfico (sensor, heartbeat, etc.).
 *
 *    Ahora hay dos timers con responsabilidades separadas:
 *
 *    _activityTimer: timer periódico de "heartbeat visual". Se reinicia con
 *      cualquier mensaje del ESP32. Si pasan 60s sin mensajes, loguea
 *      "ESP32 ONLINE — esperando comandos" y se repite cada minuto.
 *      Solo informa; no cambia badges.
 *
 *    _offlineDetectTimer: se arma ÚNICAMENTE al perder la conexión MQTT.
 *      Espera 3s para que llegue el evento 'offline' del firmware (con causa
 *      exacta). Si no llega en ese tiempo, loguea desconexión genérica.
 *      Esto garantiza que al cortar Thonny, el log aparece en ~1–3 segundos.
 *
 * LÓGICA PRESERVADA:
 *    Modos, comandos, logs, sliders y restricciones de seguridad
 *    funcionan exactamente igual que en v4.1.
 * ============================================================================
 */

'use strict';

/* ── Configuración ──────────────────────────────────────────────────── */
const CFG = {
    broker: '192.168.x.x', // IP local de la PC (igual que en config.py del firmware)
    port: 9001,            // Puerto WebSocket de Mosquitto (ver mosquitto.conf, listener 2)
    topicCmd: 'robot/cmd',
    topicLog: 'robot/log',
    clientId: 'Francisco_IC2_' + Math.random().toString(36).slice(2, 8).toUpperCase(),
    reconnect_ms: 4000,
    pending_timeout_ms: 18000,
    // [SEC] Credenciales del usuario gui_operator (creado en el broker
    // privado vía mosquitto_passwd). En v4.0 no existían porque el broker
    // público no requería autenticación.
    //
    // [SEC] Limitación de alcance: estas credenciales quedan visibles en
    // el código fuente de la GUI (cualquiera con acceso al navegador puede
    // ver este archivo .js). Esto es aceptable en esta fase porque la
    // autenticación REAL del operador humano se resuelve en Fase 3 con
    // JWT (login.html + FastAPI) — estas credenciales MQTT identifican
    // a "la GUI" como aplicación ante el broker, no al operador individual.
    // El control de acceso por persona vive en la capa de aplicación (JWT),
    // no en esta capa de transporte (MQTT). Documentado como decisión de
    // diseño, no como descuido.
    mqttUser: 'gui_operator',
    mqttPassword: 'gui_operator_pass',
};

const RESET_REASONS = {
    1: 'PWRON (Encendido normal)',
    2: 'HARD RESET (Botón EN presionado)',
    3: 'WDT RESET ⚠ (Watchdog / CPU Bloqueada)',
    4: 'DEEP SLEEP (Salió de suspensión)',
    5: 'SOFT RESET (Reinicio por Software / Thonny)',
    6: 'BROWNOUT ⚠ (Caída de tensión eléctrica)',
    7: 'SDIO RESET',
};

const SERVO_NAMES = { 1: 'BASE', 2: 'HOMBRO', 3: 'CODO', 4: 'PINZA' };

/* ── Estado global ──────────────────────────────────────────────────── */
let mqtt = null;
let connected = false;
let esp32Online = false;
let sentCount = 0;
let rcvdCount = 0;
let currentMode = 'MANUAL';
let armBusy = false;
let boxPending = false;

// Estado de pallets restaurado desde localStorage (o vacío si primera carga)
const palletState = loadPalletStateFromStorage();

// Timers de pending state: { key: timeoutId }
const _pendingTimers = {};

// Los timers de actividad/offline del ESP32 se declaran junto a sus funciones

/* ── Inicialización ─────────────────────────────────────────────────── */
window.onload = async () => {
    // [SEC] requireSession() (definida en auth.js, cargado ANTES de este
    // script) bloquea la
    // ejecución hasta confirmar una sesión JWT válida contra el backend.
    // Si la sesión no es válida, requireSession() ya redirigió a
    // login.html y esta promesa NUNCA resuelve — por diseño (ver
    // auth.js, docstring de requireSession). El "await" aquí es lo que
    // garantiza que connectMQTT() NUNCA se ejecute sin sesión confirmada.
    const operatorName = await requireSession();

    // Revelar el panel: quita la clase que lo mantenía oculto.
    document.body.classList.remove('auth-pending');

    // Mostrar el nombre del operador en el badge agregado al header.
    const txtOperator = el('txt-operator');
    if (txtOperator) txtOperator.textContent = operatorName;

    clog(`Panel iniciado | Operador: ${operatorName} | ClientID: ${CFG.clientId}`, 'info', 'sys');

    el('chk-debug').addEventListener('change', e => {
        el('console').classList.toggle('hide-debug', !e.target.checked);
    });
    el('console').classList.add('hide-debug');

    [1, 2, 3, 4].forEach(id => updateSliderBg(id, 90));

    const palletState = loadPalletStateFromStorage();
    updatePallet(1, palletState[1].count, palletState[1].full);
    updatePallet(2, palletState[2].count, palletState[2].full);
    if (palletState[1].count > 0 || palletState[2].count > 0) {
        clog('📦 Estado de pallets restaurado desde memoria local del navegador.', 'info', 'sys');
    }

    connectMQTT();
};

/* ── Persistencia en localStorage ──────────────────────────────────── */

/**
 * Carga el estado de los pallets desde localStorage.
 * Si no hay datos o el formato es inválido, retorna el estado vacío por defecto.
 * @returns {{ 1: {count,full}, 2: {count,full} }}
 */
function loadPalletStateFromStorage() {
    try {
        const saved = localStorage.getItem('robot_palletState');
        if (saved) {
            const p = JSON.parse(saved);
            if (p[1] && p[2] && typeof p[1].count === 'number') return p; // Validación básica de formato esperado
        }
    } catch (e) { /* datos corruptos → ignorar */ }
    return { 1: { count: 0, full: false }, 2: { count: 0, full: false } };
}

/**
 * Guarda el estado actual de palletState en localStorage.
 * Se llama automáticamente en cada updatePallet().
 */
function savePalletStateToStorage() {
    try {
        localStorage.setItem('robot_palletState', JSON.stringify(palletState));
    } catch (e) {
        clog('⚠ Error guardando estado en localStorage: ' + e.message, 'warn', 'sys');
    }
}

/* ── MQTT ───────────────────────────────────────────────────────────── */

/**
 * Crea el cliente MQTT Paho y lanza la conexión WebSocket al broker.
 * Puerto 8080 (WebSocket): requerido por los navegadores web, que no pueden
 * abrir conexiones TCP directas al puerto 1883 del broker.
 */
function connectMQTT() {
    clog('Conectando a broker MQTT...', 'info', 'sys');
    try {
        mqtt = new Paho.MQTT.Client(CFG.broker, CFG.port, CFG.clientId);
        mqtt.onConnectionLost = onConnectionLost;
        mqtt.onMessageArrived = onMessageArrived;
        mqtt.connect({
            onSuccess: onConnectSuccess,
            onFailure: onConnectFailure,
            useSSL: false,
            timeout: 10,
            keepAliveInterval: 30,
            cleanSession: true,
            userName: CFG.mqttUser,      // ← AGREGAR
            password: CFG.mqttPassword,  // ← AGREGAR
        });
    } catch (err) {
        setBadge('mqtt', 'err');
        setTimeout(connectMQTT, CFG.reconnect_ms);
    }
}

/**
 * Callback: conexión MQTT establecida exitosamente.
 * Suscribe a robot/log con QoS 1 para garantía de entrega de eventos.
 * Solicita re-sincronización de estado al ESP32 tras 2 segundos.
 */
function onConnectSuccess() {
    connected = true;
    setBadge('mqtt', 'ok');
    clog('✅ Conectado al broker MQTT exitosamente.', 'info', 'sys');

    // QoS 1 en suscripción: el broker confirma entrega de eventos del ESP32.
    mqtt.subscribe(CFG.topicLog, { qos: 1 });
    enableControls(true);

    // Re-sincronización activa: solicitar status completo tras 2s.
    // Delay necesario para que el ESP32 procese su propia reconexión.
    setTimeout(() => {
        if (connected) { // <-- CAMBIO CLAVE: Quitamos la restricción && esp32Online
            clog('🔄 Re-sincronizando estado con ESP32...', 'info', 'sys');
            publish({ cmd: 'status' }, 0); // Esto fuerza al ESP32 a responder con su estado
        }
    }, 2000);
}

/**
 * Callback: intento de conexión fallido. Programa reintento automático.
 */
function onConnectFailure(err) {
    connected = false;
    setBadge('mqtt', 'err');
    enableControls(false);
    clog(`⚠ Fallo al conectar. Reintentando en ${CFG.reconnect_ms / 1000}s...`, 'warn', 'sys');
    setTimeout(connectMQTT, CFG.reconnect_ms);
}

/**
 * Callback: conexión MQTT perdida durante una sesión activa.
 * Arma el timer de detección de desconexión (espera 3s el evento 'offline'
 * del firmware) y programa reconexión automática al broker.
 */
function onConnectionLost(resp) {
    connected = false;
    clearActivityTimer();
    // Armar detección de offline: si no llega evento 'offline' en 3s, marcar manual
    if (esp32Online) armOfflineDetectTimer();
    setBadge('mqtt', 'err');
    enableControls(false);
    clog('⚠️ Se perdió la conexión con el servidor MQTT. Reconectando...', 'warn', 'sys');
    setTimeout(connectMQTT, CFG.reconnect_ms);
}

/* ── Mensajes entrantes ─────────────────────────────────────────────── */

/**
 * Callback: llega un mensaje en robot/log.
 * Reinicia el timer de actividad (cualquier mensaje prueba que el ESP32 está vivo),
 * parsea el JSON y delega a handleEvent().
 */
function onMessageArrived(msg) {
    rcvdCount++;
    el('m-msgs').textContent = rcvdCount;
    const raw = msg.payloadString;
    clog(`Payload crudo ← ${raw}`, 'debug', 'rx');

    // Cualquier mensaje reinicia el contador de inactividad
    resetActivityTimer();

    try {
        handleEvent(JSON.parse(raw));
    } catch (_) {
        clog(`JSON inválido: ${raw}`, 'warn', 'rx');
    }
}

/**
 * Router de eventos del ESP32.
 * Todos los handlers son idempotentes: recibir el mismo evento dos veces
 * (posible con QoS 1) no produce efectos adicionales no deseados.
 */
function handleEvent(data) {
    const ev = data.event || '';

    if (ev === 'online') {
        esp32Online = true;
        cancelOfflineDetectTimer(); // cancelar detección si llegó el evento antes del timeout
        setBadge('esp', 'ok');
        const cause = RESET_REASONS[data.reset_cause] ?? `Código ${data.reset_cause}`;
        el('m-reset').textContent = cause;
        updateMetrics(data);
        resetActivityTimer(); // iniciar contador de inactividad periódico
        clog('🚀 ESP32 Online y lista para recibir comandos.', 'info', 'rx');
        const levelCause = (data.reset_cause === 3 || data.reset_cause === 6) ? 'error' : 'warn';
        clog(`Motivo del último reinicio: ${cause}`, levelCause, 'rx');
        if (data.mode) syncMode(data.mode);
        return;
    }

    if (ev === 'offline') {
        // Evento explícito del firmware: causa conocida (Thonny stop, LWT, etc.)
        // Cancelar el timer de detección porque ya tenemos la información completa.
        cancelOfflineDetectTimer();
        esp32Online = false;
        clearActivityTimer();
        setBadge('esp', 'err');
        const motivo = data.cause || 'Desconexión desconocida';
        clog(`❌ ESP32 OFFLINE — ${motivo}`, 'error', 'rx');
        evaluarBotonesSemiauto();
        return;
    }

    if (ev === 'status') {
        const wasOnline = esp32Online;
        esp32Online = true;
        cancelOfflineDetectTimer();
        setBadge('esp', 'ok');
        armBusy = data.arm_busy || false;
        // --- NUEVO: Sincronizar el estado de la caja pendiente ---
        if (data.semi_pending !== undefined) {
            boxPending = data.semi_pending;
            showSemiAlert(boxPending); // Muestra/oculta el cartel según corresponda
        }
        // ---------------------------------------------------------
        updateMetrics(data);
        resetActivityTimer();
        // Loguear "Online esperando comandos" solo la primera vez que se detecta
        if (!wasOnline) clog('🟢 ESP32 Online — sistema activo, esperando comandos.', 'info', 'rx');
        if (data.mode) syncMode(data.mode);
        if (data.pallets) {
            updatePallet(1, data.pallets['1'].count, data.pallets['1'].full);
            updatePallet(2, data.pallets['2'].count, data.pallets['2'].full);
        }
        if (data.servos) syncSliders(data.servos);
        if (data.sensor !== undefined) updateSensorBadge(data.sensor);
        evaluarBotonesSemiauto();
        return;
    }

    if (ev === 'sensor') {
        updateSensorBadge(data.detected);
        return;
    }

    if (ev === 'box_detected') {
        boxPending = true;
        showSemiAlert(true);
        evaluarBotonesSemiauto();
        clog('📦 CAJA DETECTADA — esperando decisión del usuario', 'warn', 'rx');
        return;
    }

    if (ev === 'pick_start') {
        armBusy = true;
        evaluarBotonesSemiauto();
        if (el('auto-state')) el('auto-state').textContent = `Pick → ${data.dest}`;
        return;
    }

    if (ev === 'box_collected') {
        armBusy = false;
        evaluarBotonesSemiauto();
        updatePallet(data.dest === 'P1' ? 1 : 2, data.count, data.full);
        // Resolver pending de semi_decision al recibir confirmación de pick
        resolvePending('semi-p1');
        resolvePending('semi-p2');
        resolvePending('semi-ig');
        clog(`✓ Caja depositada exitosamente en ${data.dest} (nivel ${data.level})`, 'info', 'rx');
        return;
    }

    if (ev === 'box_ignored') {
        boxPending = false;
        showSemiAlert(false);
        evaluarBotonesSemiauto();
        resolvePending('semi-ig');
        resolvePending('semi-p1');
        resolvePending('semi-p2');
        clog('Caja ignorada (Se aborta recolección)', 'info', 'rx');
        return;
    }

    if (ev === 'servo_ack') {
        clog(`✓ Servo ${SERVO_NAMES[data.id]} llegó a ${data.angle}°`, 'info', 'rx');
        syncSlider(data.id, data.angle);
        return;
    }

    if (ev === 'mode_changed') {
        syncMode(data.mode);
        resolvePendingAllModes();
        clog(`✓ ESP32 confirmó el cambio al modo: ${data.mode}`, 'info', 'rx');
        return;
    }

    if (ev === 'move_done') {
        resolvePending(`move-${data.action}`);
        return;
    }

    if (ev === 'pallet_full' || ev === 'all_pallets_full') {
        // Identificar qué pallet(s) están llenos para mensajes y bloqueos precisos
        const pallets = ev === 'all_pallets_full' ? [1, 2] : [data.pallet];

        pallets.forEach(pid => {
            if (!pid) return;
            updatePallet(pid, palletState[pid].count, true);

            // Deshabilitar el botón SEMI_AUTO del pallet lleno para que el operador
            // no pueda enviar una decisión que el ESP32 rechazará. El botón se
            // rehabilita en el handler de 'pallet_cleared'.
            const semiBtn = el(`btn-semi-p${pid}`);
            if (semiBtn) {
                semiBtn.disabled = true;
                semiBtn.dataset.palletFull = '1'; // marca para distinguirlo de pending state
            }
            clog(`⚠ Pallet ${pid} LLENO — vaciarlo antes de continuar (botón bloqueado)`, 'warn', 'rx');
        });

        if (ev === 'all_pallets_full') {
            clog('🛑 Ambos pallets llenos — proceso detenido hasta vaciado', 'error', 'rx');
        }
        return;
    }

    if (ev === 'pallet_cleared') {
        updatePallet(data.pallet, 0, false);
        resolvePending(`clear-p${data.pallet}`);

        // Rehabilitar el botón SEMI_AUTO del pallet que fue vaciado
        const semiBtn = el(`btn-semi-p${data.pallet}`);
        if (semiBtn) delete semiBtn.dataset.palletFull;
        // La re-habilitación real la hace evaluarBotonesSemiauto() a continuación
        evaluarBotonesSemiauto();

        clog(`✓ ESP32 confirmó el vaciado del Pallet ${data.pallet} — botón rehabilitado`, 'info', 'rx');
        return;
    }
}

/* ── Publicación de comandos ────────────────────────────────────────── */

/**
 * Publica un objeto como JSON en robot/cmd.
 * @param {Object} obj   - Comando a enviar.
 * @param {number} [qos] - Nivel QoS: 0 para servos/consultas, 1 para comandos críticos.
 *
 * Política de QoS:
 *  QoS 1 (default): set_mode, move, semi_decision, pallet_clear
 *  QoS 0:           servo (alta frecuencia), status (solo consulta)
 */
function publish(obj, qos = 1) {
    if (!connected) return;
    try {
        const msg = new Paho.MQTT.Message(JSON.stringify(obj));
        msg.destinationName = CFG.topicCmd;
        msg.qos = qos;
        mqtt.send(msg);
        sentCount++;
        el('m-sent').textContent = sentCount;
    } catch (err) {
        clog(`Error publicando: ${err.message}`, 'error', 'sys');
    }
}

/**
 * Genera un ID único para deduplicación de comandos QoS 1.
 * El firmware ESP32 descarta mensajes con msg_id igual al último procesado,
 * neutralizando los duplicados inherentes al protocolo QoS 1.
 * @returns {string} Ej: "1741234567890_A3F1"
 */
function genMsgId() {
    return Date.now() + '_' + Math.random().toString(36).slice(2, 6).toUpperCase();
}

/* ── Acciones de la UI ──────────────────────────────────────────────── */

/** Solicita cambio de modo al ESP32. QoS 1 + msg_id para entrega garantizada. */
function setMode(mode) {
    if (!connected) return;
    clog(`→ Solicitando cambio a modo: ${mode}`, 'info', 'tx');
    publish({ cmd: 'set_mode', mode, msg_id: genMsgId() }, 1);
    syncMode(mode); // feedback visual inmediato
}

/**
 * Actualiza la UI para reflejar el modo activo.
 * Cambia el botón activo y el panel visible. Idempotente.
 */
function syncMode(mode) {
    currentMode = mode;
    document.querySelectorAll('.mode-btn').forEach(btn => btn.classList.remove('active'));
    let key = mode.toLowerCase();
    if (mode === 'SEMI_AUTO') key = 'semiauto';
    if (mode === 'AUTOMATICO') key = 'auto';
    const btn = el(`btn-${key}`);
    if (btn) btn.classList.add('active');
    document.querySelectorAll('.mode-panel').forEach(p => p.classList.remove('active'));
    const panel = el(`panel-${key}`);
    if (panel) panel.classList.add('active');
    el('txt-mode').textContent = mode.replace('_', ' ');
}

/** Actualiza el label del slider en tiempo real (sin enviar MQTT). */
function onSlider(id, val) {
    el(`val-s${id}`).textContent = `${val}°`;
    updateSliderBg(id, val);
}

/**
 * Envía comando de servo individual. QoS 0: el valor más reciente siempre
 * reemplaza al anterior, y arm_busy en el firmware descarta duplicados.
 */
function sendServo(id, val) {
    clog(`→ Mover Servo ${SERVO_NAMES[id]} a ${val}°`, 'info', 'tx');
    publish({ cmd: 'servo', id: parseInt(id), angle: parseInt(val) }, 0);
}

/**
 * Envía comando de movimiento preconfigurado. QoS 1 + msg_id.
 * El pending state se resuelve al recibir 'move_done'.
 */
function sendMove(action) {
    clog(`→ Ejecutando movimiento predefinido: ${action.toUpperCase().replace('_', ' ')}`, 'info', 'tx');
    publish({ cmd: 'move', action, msg_id: genMsgId() }, 1);
}

/* ── Seguridad Modo SEMI-AUTO ───────────────────────────────────────── */

/**
 * Evalúa habilitación de botones de decisión semi-automática.
 * Condición global: connected AND esp32Online AND boxPending AND NOT armBusy.
 * Condición por botón de pallet: además, el pallet no debe estar lleno
 * (dataset.palletFull marcado por el handler de 'pallet_full').
 * Respeta el pending state: no modifica botones que ya están esperando confirmación.
 */
function evaluarBotonesSemiauto() {
    const condicionBase = connected && esp32Online && boxPending && !armBusy;

    // El botón "Ignorar" solo depende de la condición base
    const btnIg = el('btn-semi-ig');
    if (btnIg && !btnIg.dataset.pending) btnIg.disabled = !condicionBase;

    // Los botones de pallet se bloquean adicionalmente si ese pallet está lleno
    [1, 2].forEach(pid => {
        const b = el(`btn-semi-p${pid}`);
        if (!b || b.dataset.pending) return;
        const palletLleno = !!b.dataset.palletFull;
        b.disabled = !condicionBase || palletLleno;
    });
}

/**
 * Envía la decisión del operador en modo SEMI_AUTO. QoS 1 + msg_id.
 * Verifica todas las condiciones de seguridad.
 * Deshabilita los botones inmediatamente para prevenir envíos duplicados.
 * El pending state se resuelve al recibir box_collected o box_ignored.
 */
function semiDecision(dest) {
    if (!connected || !esp32Online || !boxPending || armBusy) {
        clog('⛔ Movimiento rechazado: Sistema desconectado, ocupado o sin caja', 'error', 'sys');
        return;
    }
    clog(`→ Decisión enviada: Destino -> ${dest}`, 'info', 'tx');
    publish({ cmd: 'semi_decision', dest, msg_id: genMsgId() }, 1);

    // Deshabilitar inmediatamente para prevenir doble envío
    boxPending = false;
    showSemiAlert(false);
    evaluarBotonesSemiauto();

    // Pending state en el botón presionado
    const key = dest === 'P1' ? 'semi-p1' : dest === 'P2' ? 'semi-p2' : 'semi-ig';
    const btnId = dest === 'P1' ? 'btn-semi-p1' : dest === 'P2' ? 'btn-semi-p2' : 'btn-semi-ig';
    setPending(key, btnId);
}

/**
 * Solicita vaciado de pallet. QoS 1 + msg_id para entrega garantizada.
 * Deshabilita el botón inmediatamente. Se rehabilita al recibir pallet_cleared
 * o tras el timeout de seguridad (8 segundos).
 */
function clearPallet(pid) {
    clog(`→ Solicitando vaciado del Pallet ${pid}`, 'info', 'tx');
    publish({ cmd: 'pallet_clear', pallet: pid, msg_id: genMsgId() }, 1);
    setPending(`clear-p${pid}`, `btn-clear-p${pid}`);
}

/** Solicita telemetría completa. QoS 0: solo consulta. */
function requestStatus() {
    clog('→ Solicitando telemetría completa a la ESP32', 'info', 'tx');
    publish({ cmd: 'status' }, 0);
}

/* ── Sistema de Pending State ───────────────────────────────────────── */

/**
 * Pone un botón en estado "esperando confirmación":
 * - Deshabilitado visualmente con ⏳ spinner
 * - Timeout de seguridad: si la confirmación no llega en pending_timeout_ms,
 *   el botón se rehabilita automáticamente para no quedar bloqueado.
 *
 * @param {string} key   - Identificador interno (ej: 'clear-p1', 'semi-p2').
 * @param {string} btnId - ID del elemento en el DOM (puede ser null si no hay botón).
 */
function setPending(key, btnId) {
    const btn = btnId ? el(btnId) : null;
    if (btn) {
        btn.disabled = true;
        btn.dataset.pending = '1';
        btn.dataset.originalText = btn.textContent;
        btn.textContent = '⏳ ' + btn.textContent.replace(/^[^\wáéíóúÑñ]*/, '');
    }
    clearTimeout(_pendingTimers[key]);
    _pendingTimers[key] = setTimeout(() => {
        clog(`⚠ Timeout: no se recibió confirmación de '${key}'. Rehabilitando.`, 'warn', 'sys');
        resolvePending(key);
    }, CFG.pending_timeout_ms);
}

/**
 * Cancela el pending state de una acción al recibir su confirmación (o por timeout).
 * Restaura el texto original del botón. Es seguro llamar si el key no está pendiente.
 *
 * @param {string} key - Identificador interno del pending a resolver.
 */
function resolvePending(key) {
    clearTimeout(_pendingTimers[key]);
    delete _pendingTimers[key];

    if (key.startsWith('semi-')) {
        ['btn-semi-p1', 'btn-semi-p2', 'btn-semi-ig'].forEach(id => restoreBtn(id));
        evaluarBotonesSemiauto();
        return;
    }
    if (key.startsWith('clear-p')) {
        restoreBtn(`btn-clear-p${key.slice(-1)}`);
        // La habilitación real la gestiona updatePallet() según estado del pallet
        return;
    }
}

/** Rehabilita todos los botones de modo tras recibir 'mode_changed'. */
function resolvePendingAllModes() {
    ['btn-manual', 'btn-semiauto', 'btn-auto'].forEach(id => restoreBtn(id));
}

/**
 * Restaura un botón desde estado pending a estado normal.
 * @param {string} btnId - ID del elemento en el DOM.
 */
function restoreBtn(btnId) {
    const btn = el(btnId);
    if (!btn) return;
    delete btn.dataset.pending;
    if (btn.dataset.originalText) {
        btn.textContent = btn.dataset.originalText;
        delete btn.dataset.originalText;
    }
}

/* ── Sistema de Actividad del ESP32 ────────────────────────────────── */

/**
 * DOS TIMERS INDEPENDIENTES con responsabilidades separadas:
 *
 * _activityTimer — Timer periódico de "heartbeat visual".
 *   Se REINICIA con cada mensaje recibido del ESP32 (sensor, heartbeat,
 *   box_collected, etc.). Si pasan 60s sin ningún mensaje, loguea
 *   "ESP32 ONLINE — esperando comandos" para informar al operador que
 *   el sistema sigue vivo pero sin actividad reciente.
 *   NO cambia badges. Se repite indefinidamente mientras esp32Online=true.
 *
 * _offlineDetectTimer — Timer de detección de desconexión.
 *   Se arma ÚNICAMENTE cuando se pierde la conexión MQTT (onConnectionLost)
 *   o llega el LWT del broker. Espera 3s para dar tiempo al evento 'offline'
 *   explícito del firmware a llegar. Si no llega, loguea la desconexión
 *   y marca el badge como rojo "Offline".
 *   Esto garantiza que al cortar Thonny, el log aparezca inmediatamente.
 */
let _activityTimer = null;
let _offlineDetectTimer = null;

/**
 * Reinicia el timer de actividad periódica.
 * Se llama en cada mensaje recibido del ESP32 (en onMessageArrived).
 * Al expirar loguea el mensaje informativo y se re-programa a sí mismo.
 */
function resetActivityTimer() {
    clearTimeout(_activityTimer);
    if (!esp32Online) return;
    _activityTimer = setTimeout(function tick() {
        if (!esp32Online) return;
        clog('🟢 ESP32 ONLINE — esperando comandos o detección de caja.', 'info', 'sys');
        // Re-programar para el siguiente minuto mientras siga online
        _activityTimer = setTimeout(tick, 60000);
    }, 60000);
}

/** Detiene el timer de actividad. Llamar cuando la ESP32 se marca offline. */
function clearActivityTimer() {
    clearTimeout(_activityTimer);
    _activityTimer = null;
}

/**
 * Arma el timer de detección de desconexión.
 * Se llama al perder la conexión MQTT. Espera 3s para que llegue el evento
 * 'offline' del firmware (que tiene información de causa). Si no llega,
 * loguea una desconexión genérica y marca el badge como rojo.
 */
function armOfflineDetectTimer() {
    clearTimeout(_offlineDetectTimer);
    _offlineDetectTimer = setTimeout(() => {
        if (!esp32Online) return; // ya fue procesado por el evento 'offline'
        esp32Online = false;
        clearActivityTimer();
        setBadge('esp', 'err');
        evaluarBotonesSemiauto();
        clog('❌ ESP32 OFFLINE — conexión perdida (sin evento de cierre del firmware).', 'error', 'sys');
    }, 3000);
}

/** Cancela el timer de detección de desconexión. */
function cancelOfflineDetectTimer() {
    clearTimeout(_offlineDetectTimer);
    _offlineDetectTimer = null;
}

/* ── UI Helpers ─────────────────────────────────────────────────────── */

function el(id) { return document.getElementById(id); }

function setBadge(which, state) {
    const badge = el(`badge-${which}`);
    if (!badge) return;
    badge.className = 'badge';
    badge.classList.add(state);
    const txt = el(`txt-${which}`);
    if (!txt) return;
    if (which === 'mqtt') txt.textContent = state === 'ok' ? 'Conectado' : 'Desconectado';
    if (which === 'esp') txt.textContent = state === 'ok' ? 'Online' : 'Offline';
}

function updateSensorBadge(detected) {
    const badge = el('badge-sensor');
    const txt = el('txt-sensor');
    if (!badge || !txt) return;
    badge.className = 'badge ' + (detected ? 'warn' : 'ok');
    txt.textContent = detected ? 'DETECTADO' : 'Libre';
}

function enableControls(on) {
    document.querySelectorAll('.btn:not(.btn-clear-log)').forEach(b => {
        if (!b.id.includes('btn-semi') && !b.dataset.pending) b.disabled = !on;
    });
    document.querySelectorAll('.slider').forEach(s => s.disabled = !on);
    document.querySelectorAll('.mode-btn').forEach(b => {
        if (!b.dataset.pending) b.disabled = !on;
    });
    evaluarBotonesSemiauto();
}

function updateMetrics(data) {
    if (data.mem_free !== undefined) el('m-mem').textContent = data.mem_free >= 1024 ? `${(data.mem_free / 1024).toFixed(1)} KB` : `${data.mem_free} B`;
    if (data.wifi_rssi !== undefined) el('m-rssi').textContent = `${data.wifi_rssi} dBm`;
    if (data.reconnects !== undefined) el('m-recon').textContent = data.reconnects;
    if (data.cmd_received !== undefined) el('m-cmds').textContent = data.cmd_received;
}

/**
 * Actualiza la representación visual de un pallet y persiste en localStorage.
 * @param {number}  pid   - ID del pallet (1 o 2).
 * @param {number}  count - Cajas depositadas.
 * @param {boolean} full  - True si el pallet está lleno.
 */
function updatePallet(pid, count, full) {
    palletState[pid] = { count, full };
    savePalletStateToStorage();

    el(`pallet${pid}-badge`).textContent = `${count} / 3`;
    for (let i = 1; i <= 3; i++) {
        const slot = el(`p${pid}-slot-${i}`);
        if (slot) slot.className = 'box-slot ' + (i <= count ? 'filled' : 'empty');
    }
    el(`pallet-card-${pid}`).classList.toggle('full', full);

    // Solo habilitar "Vaciar" si está lleno y no está en pending state
    const clearBtn = el(`btn-clear-p${pid}`);
    if (clearBtn && !clearBtn.dataset.pending) clearBtn.disabled = !full;
}

function syncSliders(servos) {
    Object.entries(servos).forEach(([id, angle]) => syncSlider(parseInt(id), angle));
}

function syncSlider(id, angle) {
    const s = el(`s${id}`);
    if (s) { s.value = angle; updateSliderBg(id, angle); }
    const v = el(`val-s${id}`);
    if (v) v.textContent = `${angle}°`;
}

function updateSliderBg(id, val) {
    const s = el(`s${id}`);
    if (!s) return;
    const pct = (val / 180) * 100;
    s.style.background = `linear-gradient(to right, var(--color-primary) ${pct}%, var(--bg-dark) ${pct}%)`;
}

function showSemiAlert(show) {
    const box = el('semiauto-alert');
    if (box) box.classList.toggle('hidden', !show);
}

/* ── Consola de Logs ────────────────────────────────────────────────── */

/**
 * Agrega una entrada formateada a la consola de la GUI.
 * @param {string} msg   - Texto del mensaje.
 * @param {string} level - 'info' | 'warn' | 'error' | 'debug'
 * @param {string} dir   - 'rx' (ESP32) | 'tx' (GUI) | 'sys' (sistema)
 */
function clog(msg, level = 'info', dir = 'tx') {
    const cons = el('console');
    if (!cons) return;
    const now = new Date();
    const ts = now.toLocaleTimeString('es-AR', { hour12: false }) +
        '.' + String(now.getMilliseconds()).padStart(3, '0');
    const entry = document.createElement('div');
    entry.className = `log-entry ${level} ${dir}`;
    let prefix = '';
    if (dir === 'rx') prefix = '<span class="log-origin rx">[ESP32] dice:</span>';
    else if (dir === 'tx') prefix = '<span class="log-origin tx">[Usuario-GUI]:</span>';
    else prefix = '<span class="log-origin sys">[Sistema]:</span>';
    entry.innerHTML = `
        <span class="log-ts">[${ts}]</span>
        <span class="log-level">[${level.toUpperCase().padEnd(5, ' ')}]</span>
        ${prefix}
        <span class="log-msg">${msg}</span>
    `;
    cons.appendChild(entry);
    if (el('chk-scroll')?.checked) cons.scrollTop = cons.scrollHeight;
}

function clearConsole() { el('console').innerHTML = ''; }
