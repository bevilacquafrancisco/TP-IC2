"""
================================================================================
commands.py — Dispatcher de comandos MQTT y lógica de negocio del robot
================================================================================
Autor: Francisco Bevilacqua | Versión: 5.0

Responsabilidad única (SRP):
    Este es el módulo de más alto nivel del firmware (excepto main.py): la
    "capa de aplicación" que interpreta comandos JSON entrantes de la GUI,
    decide qué hacer con las detecciones del sensor según el modo activo, y
    arma el snapshot de telemetría completo. Orquesta a servos, sensor,
    mqtt y state, pero NINGUNO de esos módulos lo importa a él — así se
    evita cualquier ciclo de imports (ver justificación detallada en el
    encabezado de mqtt.py, sección "CÓMO SE EVITA EL IMPORT CIRCULAR").

Funciones públicas expuestas a main.py:
    on_message(topic, msg)       → callback registrado en mqtt.connect_mqtt()
    publish_status()             → snapshot completo de telemetría
    process_sensor_event()       → debe llamarse cada TIMING["sensor_poll_ms"]

Dependencias:
    gc, machine (reset_cause) → telemetría de sistema
    json    → parseo del payload de comandos
    config  → TIMING (no se usa aquí directamente, pero sensor.py y
              servos.py ya lo consumen — se documenta la ausencia a
              propósito, no es un olvido)
    state   → toda la máquina de estados (mode, pallet_count, arm_busy, etc.)
    mqtt    → mqtt_publish()
    servos  → servo_set, move_sequence, pick_and_place
    sensor  → read_sensor, poll_debounced
    wifi    → wifi_is_up (para RSSI en publish_status)
    state.log
================================================================================
"""

import gc
import json

from machine import reset_cause

from state import state, log
from mqtt import mqtt_publish
from servos import servo_set, move_sequence, pick_and_place
from sensor import read_sensor, poll_debounced
from wifi import wifi_is_up


# ============================================================================
# TELEMETRÍA
# ============================================================================

def publish_status():
    """
    Publica un snapshot completo del estado del sistema en robot/log.

    Se invoca en tres momentos distintos (todos coordinados desde main.py
    o mqtt.py, nunca por este módulo por sí solo):
      1. Al reconectar MQTT (vía el status_cb de mqtt.connect_mqtt), para
         que la GUI se re-sincronice sin esperar el próximo heartbeat.
      2. En cada heartbeat periódico (TIMING["heartbeat_ms"]).
      3. Cuando la GUI lo solicita explícitamente ({"cmd": "status"}).
    """
    status = {
        "event":        "status",
        "mode":         state.mode,
        "arm_busy":     state.arm_busy,
        "semi_pending": state.semi_pending,
        "pallets": {
            "1": {"count": state.pallet_count[1], "full": state.pallet_full[1]},
            "2": {"count": state.pallet_count[2], "full": state.pallet_full[2]},
        },
        "servos":       state.servo_angle.copy(),
        "sensor":       read_sensor(),
        "mem_free":     gc.mem_free(),
        "loop_count":   state.loop_count,
        "cmd_received": state.cmd_received,
        "reconnects":   state.reconnect_count,
        "wifi_rssi":    state.wifi.status("rssi") if (state.wifi and wifi_is_up()) else 0,
        "reset_cause":  reset_cause(),
    }
    log("Status publicado", "INFO")
    mqtt_publish(status)


# ============================================================================
# LÓGICA DE SENSOR Y MODOS AUTOMÁTICOS
# ============================================================================

def process_sensor_event():
    """
    Debe llamarse periódicamente (cada TIMING["sensor_poll_ms"]) desde el
    loop principal, y SOLO si state.arm_busy es False (leer el sensor
    mientras el brazo se mueve no tiene sentido y el caller ya lo filtra
    en main.py, igual que en la versión original).

    Delega el debounce a sensor.poll_debounced(); cuando esta confirma una
    detección, decide qué hacer según state.mode:
      - SEMI_AUTO: marca semi_pending y notifica a la GUI, que debe
        responder con un comando "semi_decision".
      - AUTOMATICO: elige destino automáticamente (llena Pallet 1 antes
        que Pallet 2) y dispara pick_and_place() directamente.
      - MANUAL: la detección se loguea/publica igual (telemetría útil)
        pero no dispara ninguna acción — el operador controla todo a mano.
    """
    if not poll_debounced():
        return

    log("Sensor: caja detectada (confirmado)", "INFO")
    mqtt_publish({"event": "sensor", "detected": True})

    if state.mode == "SEMI_AUTO":
        if not state.semi_pending:
            state.semi_pending = True
            log("Modo SEMI_AUTO: esperando decision del usuario", "INFO")
            mqtt_publish({"event": "box_detected"})

    elif state.mode == "AUTOMATICO":
        dest = None
        if not state.pallet_full[1]:
            dest = 1
        elif not state.pallet_full[2]:
            dest = 2
        else:
            log("Ambos pallets llenos - proceso detenido hasta vaciado", "WARNING")
            mqtt_publish({"event": "all_pallets_full"})
            return

        log("Modo AUTO: pick & place automatico -> Pallet {}".format(dest), "INFO")
        pick_and_place(dest)


# ============================================================================
# DISPATCHER DE COMANDOS MQTT
# ============================================================================

def on_message(topic, msg):
    """
    Callback MQTT registrado vía mqtt.connect_mqtt(on_message_cb=on_message).
    Recibe comandos JSON desde la GUI sobre robot/cmd y los despacha.

    DEBE ser rápida: las secuencias largas (move_sequence, pick_and_place)
    se ejecutan síncronamente acá mismo porque MicroPython/umqtt.simple no
    ofrece un modelo async nativo simple para este proyecto — se acepta el
    trade-off de que check_msg() no vuelve a llamarse hasta que la
    secuencia termina (igual que en la v4.0 original).

    DEDUPLICACIÓN QoS 1:
        Con QoS 1, el broker puede reenviar el mismo mensaje si no recibió
        PUBACK a tiempo. Para evitar ejecutar un comando dos veces (ej.
        depositar la misma caja dos veces), se compara el campo opcional
        "msg_id" del payload contra state.last_cmd_id. Comandos sin riesgo
        (como "status") no incluyen msg_id y no pasan por este filtro.

    Args:
        topic (bytes): topic del mensaje (siempre MQTT["topic_cmd"] en este
            proyecto, dado que solo hay una suscripción activa).
        msg (bytes): payload JSON crudo.
    """
    state.cmd_received += 1
    raw = msg.decode()
    log("<- CMD: {}".format(raw), "INFO")

    try:
        data = json.loads(raw)
    except Exception:
        log("JSON invalido: {}".format(raw), "ERROR")
        return

    # ── Deduplicación de duplicados QoS 1 ──────────────────────────────
    incoming_id = data.get("msg_id", None)
    if incoming_id is not None:
        if incoming_id == state.last_cmd_id:
            log("CMD duplicado ignorado (msg_id={})".format(incoming_id), "WARNING")
            return
        state.last_cmd_id = incoming_id
    # ────────────────────────────────────────────────────────────────────

    cmd = data.get("cmd", "")

    if cmd == "set_mode":
        _handle_set_mode(data)
    elif cmd == "servo":
        _handle_servo(data)
    elif cmd == "move":
        _handle_move(data)
    elif cmd == "semi_decision":
        _handle_semi_decision(data)
    elif cmd == "pallet_clear":
        _handle_pallet_clear(data)
    elif cmd == "status":
        publish_status()
    else:
        log("Comando desconocido: '{}'".format(cmd), "WARNING")


# ── Handlers privados por tipo de comando ──────────────────────────────
# Separados de on_message() para que cada uno sea legible y testeable de
# forma aislada, sin un if/elif gigante concentrando toda la lógica.

def _handle_set_mode(data):
    """Comando {"cmd":"set_mode","mode":"MANUAL"|"SEMI_AUTO"|"AUTOMATICO"}."""
    new_mode = data.get("mode", "MANUAL").upper()
    if new_mode in ("MANUAL", "SEMI_AUTO", "AUTOMATICO"):
        state.mode = new_mode
        log("Modo cambiado a: {}".format(state.mode), "INFO")
        mqtt_publish({"event": "mode_changed", "mode": state.mode})
    else:
        log("Modo desconocido: {}".format(new_mode), "WARNING")


def _handle_servo(data):
    """Comando {"cmd":"servo","id":1-4,"angle":0-180} — control manual individual."""
    if state.arm_busy:
        log("Brazo ocupado, comando ignorado", "WARNING")
        return
    sid = int(data.get("id", 1))
    angle = int(data.get("angle", 90))
    if 1 <= sid <= 4:
        servo_set(sid, angle, smooth=True)
        mqtt_publish({"event": "servo_ack", "id": sid, "angle": angle})
    else:
        log("Servo id={} fuera de rango".format(sid), "ERROR")


def _handle_move(data):
    """Comando {"cmd":"move","action":"home"|"recolectar"|"abrir_pinza"|"cerrar_pinza"}."""
    if state.arm_busy:
        log("Brazo ocupado, comando ignorado", "WARNING")
        return
    move_sequence(data.get("action", "home"))


def _handle_semi_decision(data):
    """Comando {"cmd":"semi_decision","dest":"P1"|"P2"|"ignorar"} — respuesta
    del operador a una alerta de caja detectada en modo SEMI_AUTO."""
    if not state.semi_pending:
        log("No hay caja pendiente para decision", "WARNING")
        return
    dest = data.get("dest", "ignorar")
    state.semi_pending = False
    if dest == "P1":
        pick_and_place(1)
    elif dest == "P2":
        pick_and_place(2)
    else:
        log("Caja ignorada por el usuario", "INFO")
        mqtt_publish({"event": "box_ignored"})


def _handle_pallet_clear(data):
    """Comando {"cmd":"pallet_clear","pallet":1|2} — vaciado confirmado desde la GUI."""
    pallet_id = int(data.get("pallet", 1))
    if pallet_id in (1, 2):
        state.pallet_count[pallet_id] = 0
        state.pallet_full[pallet_id] = False
        log("Pallet {} vaciado por usuario".format(pallet_id), "INFO")
        mqtt_publish({"event": "pallet_cleared", "pallet": pallet_id})
    else:
        log("Pallet id={} invalido".format(pallet_id), "ERROR")
