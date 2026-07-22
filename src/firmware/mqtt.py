"""
================================================================================
mqtt.py — Capa de transporte MQTT: conexión, publicación y polling
================================================================================
Autor: Francisco Bevilacqua | Versión: 5.0

Responsabilidad única (SRP):
    Este módulo SOLO sabe hablar el protocolo MQTT (conectar, publicar,
    suscribir, hacer poll de mensajes entrantes). NO conoce el formato de
    los comandos del robot ni la lógica de negocio — eso vive en
    commands.py. Esta separación es deliberada (aplica DIP, Dependency
    Inversion Principle): si mañana se cambiara umqtt.simple por otra
    librería MQTT, solo este archivo se modifica.

CÓMO SE EVITA EL IMPORT CIRCULAR CON commands.py:
    El callback que procesa cada mensaje entrante (on_message) y la función
    que publica el snapshot de estado tras reconectar (publish_status) son
    lógica de NEGOCIO — viven en commands.py. Pero commands.py también
    necesita mqtt.mqtt_publish() para responder eventos. Si mqtt.py
    importara commands.py directamente, se formaría un ciclo:
        mqtt.py → commands.py → mqtt.py   (ImportError en MicroPython)

    La solución aplicada es inyección de dependencias (callbacks pasados
    como parámetros): connect_mqtt() RECIBE las funciones on_message_cb y
    status_cb como argumentos, en vez de importarlas. Quien conoce ambos
    módulos y los conecta es main.py (el único punto de la aplicación con
    visión completa del sistema) — ver connect_mqtt(commands.on_message,
    commands.publish_status) en main.py.

Dependencias:
    umqtt.simple (MQTTClient) → librería estándar de MicroPython para MQTT
    config  → MQTT (broker, puerto, credenciales, topics)
    state   → state.client, state.mqtt_ok, state.reconnect_count, state.mode
    wifi    → wifi_is_up() (guard antes de intentar conectar MQTT)
    state.log
================================================================================
"""

import gc
import json
from time import sleep_ms

from machine import reset_cause
from umqtt.simple import MQTTClient

from config import MQTT
from state import state, log
from wifi import wifi_is_up


def mqtt_publish(data):
    """
    Publica un diccionario Python, serializado a JSON, en el topic
    MQTT["topic_log"]. Falla silenciosamente (solo loguea WARNING) si no
    hay conexión activa — es deliberadamente "fire and forget" con QoS 0:
    la telemetría periódica no justifica el costo de esperar PUBACK, y un
    evento perdido se corrige con el próximo heartbeat.

    Args:
        data (dict): payload a publicar. Debe ser serializable a JSON
            (tipos primitivos: str, int, float, bool, dict, list, None).
    """
    if not state.mqtt_ok or state.client is None:
        return
    try:
        payload = json.dumps(data)
        state.client.publish(MQTT["topic_log"], payload.encode())
        log("-> {}".format(payload), "DEBUG")
    except Exception as exc:
        log("Error publicando: {}".format(exc), "WARNING")
        state.mqtt_ok = False


def connect_mqtt(on_message_cb, status_cb=None):
    """
    Conecta al broker MQTT y suscribe al topic de comandos.

    Decisiones de diseño heredadas de la versión monolítica (v4.0):
      - clean_session=False: el broker conserva la sesión y los mensajes
        QoS 1 no entregados entre desconexiones del ESP32.
      - Suscripción con qos=1 a topic_cmd: garantiza que los comandos de
        la GUI lleguen AL MENOS UNA VEZ, incluso si el ESP32 estaba
        offline cuando se enviaron. La deduplicación por msg_id (ver
        commands.on_message) neutraliza los duplicados inherentes a QoS 1.
      - Re-sincronización activa: publica 'online' inmediatamente, y si
        se proveyó status_cb, lo invoca 500ms después — evita que la GUI
        tenga que esperar hasta el próximo heartbeat (20s) para sincronizar
        pallets, servos y modo tras una reconexión.

    NOTA SOBRE PUBACK Y WDT:
        umqtt.simple en MicroPython no bloquea esperando el PUBACK al
        publicar (solo lo hace al recibir mensajes con qos=1 en la
        suscripción). La publicación con qos=0 (mqtt_publish) es
        fire-and-forget, por lo tanto no hay riesgo de congelamiento
        esperando PUBACK del broker.

    Args:
        on_message_cb (callable): función (topic: bytes, msg: bytes) -> None
            que procesará cada mensaje entrante. Normalmente commands.on_message.
        status_cb (callable | None): función sin argumentos que publica el
            estado completo del sistema. Se invoca 500ms después de conectar,
            si se proveyó. Normalmente commands.publish_status.

    Returns:
        bool: True si la conexión y suscripción fueron exitosas.
    """
    if not wifi_is_up():
        log("WiFi caido - no se puede conectar a MQTT", "WARNING")
        return False

    log("Conectando MQTT {}:{}...".format(MQTT["broker"], MQTT["port"]), "INFO")
    try:
        if state.client is not None:
            try:
                state.client.disconnect()
            except Exception:
                pass
            state.client = None

        gc.collect()  # liberar memoria antes de instanciar un nuevo cliente

        state.client = MQTTClient(
            MQTT["client_id"],
            MQTT["broker"],
            port=MQTT["port"],
            keepalive=MQTT["keepalive"],
            user=MQTT["user"],
            password=MQTT["password"],
        )
        state.client.set_callback(on_message_cb)
        state.client.connect(clean_session=False)
        state.client.subscribe(MQTT["topic_cmd"], qos=1)

        state.mqtt_ok = True
        state.reconnect_count += 1
        log("MQTT conectado (intento #{})".format(state.reconnect_count), "INFO")

        # Re-sincronización activa: publicar 'online' inmediatamente.
        mqtt_publish({
            "event":       "online",
            "reset_cause": reset_cause(),
            "mem_free":    gc.mem_free(),
            "reconnects":  state.reconnect_count,
            "mode":        state.mode,
        })

        if status_cb is not None:
            # Dar 500ms de margen para que la GUI procese 'online' antes
            # de recibir el 'status' completo (evita que llegue "de golpe").
            sleep_ms(500)
            status_cb()

        return True

    except Exception as exc:
        state.mqtt_ok = False
        state.client = None
        log("Error MQTT: {}".format(exc), "ERROR")
        return False


def safe_poll():
    """
    Envoltorio seguro de client.check_msg() (poll no bloqueante de
    mensajes entrantes). Cualquier error de red durante el poll marca
    state.mqtt_ok = False para que main.py dispare una reconexión en la
    siguiente iteración del loop, en vez de propagar la excepción.

    Returns:
        bool: True si el poll se ejecutó sin errores (haya o no mensajes
            nuevos), False si se detectó una desconexión.
    """
    if not state.mqtt_ok or state.client is None:
        return False
    try:
        state.client.check_msg()
        return True
    except OSError as exc:
        state.mqtt_ok = False
        log("MQTT desconectado (OSError {}): {}".format(exc.args[0], exc), "WARNING")
        return False
    except Exception as exc:
        state.mqtt_ok = False
        log("MQTT error: {}".format(exc), "ERROR")
        return False
