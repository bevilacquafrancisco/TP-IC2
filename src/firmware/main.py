"""
================================================================================
main.py — Punto de entrada del firmware ESP32 Pick & Place
================================================================================
Proyecto:       Brazo Robótico Industrial Pick & Place
Materia:        Ingeniería en Computación II
Autor:          Francisco Bevilacqua
Versión:        5.0 — Modularización de robot_main.py (monolítico, v4.1)
MicroPython:    v1.20.0 on 2023-04-26; ESP32 module with ESP32
Protocolo:      MQTT sobre TCP, broker privado Mosquitto (ver config.py)

Este archivo reemplaza al robot_main.py original. Guardarlo como main.py en
la raíz del sistema de archivos del ESP32 (junto con el resto de los módulos
de este mismo directorio) para que se ejecute automáticamente al bootear.

El robot_main.py original concentraba en un solo archivo de ~1000 líneas:
configuración, estado global, WiFi, MQTT, sensor, servos, dispatcher de
comandos y el loop principal. Funcionaba, pero violaba SRP a nivel de
archivo: cualquier cambio (recalibrar una posición, ajustar un timeout,
agregar un comando nuevo) requería navegar un archivo enorme para encontrar
el fragmento correcto, y el riesgo de tocar algo no relacionado por error
crecía con el tamaño del archivo.

La modularización aplicada sigue la estructura:

    config.py   → constantes (sin lógica, sin dependencias)
    state.py    → estado mutable compartido (singleton) + logging
    wifi.py     → capa de red WiFi
    mqtt.py     → capa de transporte MQTT (agnóstica de comandos del robot)
    sensor.py   → lectura de hardware pura (sin publicar eventos)
    servos.py   → control de motores y secuencias de movimiento
    commands.py → dispatcher de comandos + lógica de modos + telemetría
    main.py     → orquestación: boot, inicialización, loop principal

La lógica de negocio (qué pasa cuando se recibe cada comando, qué modo
dispara qué acción) es IDÉNTICA a la versión v4.1 original — esta
modularización es un refactor estructural, no un cambio de comportamiento.

────────────────────────────────────────────────────────────────────────────
GRAFO DE DEPENDENCIAS (sin ciclos — requisito de MicroPython)
────────────────────────────────────────────────────────────────────────────
    config.py, state.py            (módulos hoja, sin dependencias)
            ↑
        wifi.py
            ↑
        mqtt.py  ←── sensor.py  (hoja de hardware, independiente de mqtt)
            ↑              ↑
        servos.py    ───────┘
            ↑
        commands.py   (conoce state, mqtt, servos, sensor, wifi)
            ↑
        main.py        (conoce y conecta TODOS los módulos anteriores)

main.py es el único módulo con visión completa del sistema — es quien
"inyecta" commands.on_message y commands.publish_status dentro de
mqtt.connect_mqtt(), evitando así que mqtt.py necesite importar
commands.py (lo que crearía un ciclo, ver docstring de mqtt.py).
================================================================================
"""

import sys

from machine import WDT, reset_cause, idle
from time import ticks_diff, ticks_ms
import gc

from config import TIMING, WDT_ENABLED, RESET_REASONS, MQTT
from state import state, log, log_sep
from wifi import connect_wifi, wifi_is_up
from mqtt import connect_mqtt, safe_poll
from servos import init_servos
from sensor import init_sensor
from commands import on_message, publish_status, process_sensor_event


# ============================================================================
# INFORMACIÓN DE ARRANQUE
# ============================================================================

def print_boot_info():
    """
    Imprime el encabezado de diagnóstico al arrancar: causa del último
    reset, memoria libre, versión de MicroPython y configuración de red.
    Es lo primero que se ve en la consola de Thonny — permite diagnosticar
    de un vistazo si el reset anterior fue anómalo (WDT o brownout) antes
    de que el resto del log lo tape.
    """
    log_sep("BRAZO ROBOTICO PICK & PLACE v5.0")
    cause = reset_cause()
    reason = RESET_REASONS.get(cause, "DESCONOCIDO codigo {}".format(cause))
    log("Reset anterior : {}".format(reason), "INFO")
    log("Causa numerica : {}".format(cause), "DEBUG")
    log("Memoria libre  : {} bytes".format(gc.mem_free()), "INFO")
    log("MicroPython    : {}".format(sys.version), "INFO")
    log("Broker MQTT    : {}:{}".format(MQTT["broker"], MQTT["port"]), "INFO")
    log("Topic CMD      : {}".format(MQTT["topic_cmd"].decode()), "INFO")
    log("Topic LOG      : {}".format(MQTT["topic_log"].decode()), "INFO")
    log("WDT            : {}".format("ACTIVO" if WDT_ENABLED else "DESACTIVADO (desarrollo)"), "INFO")
    log_sep()

    if cause == 3:
        log("ADVERTENCIA: reset por WATCHDOG.", "WARNING")
        log("  Verificar que WDT_ENABLED=False durante desarrollo.", "WARNING")
    elif cause == 6:
        log("ADVERTENCIA: reset por BROWNOUT (bajo voltaje).", "WARNING")
        log("  Verificar cable USB y fuente de servos.", "WARNING")


# ============================================================================
# APAGADO SEGURO
# ============================================================================

def _shutdown():
    """
    Rutina de apagado invocada desde el bloque finally del loop principal
    (Ctrl+C en Thonny, o excepción no manejada). Lleva el brazo a una
    posición segura, desactiva el PWM de todos los servos, y notifica
    'offline' por MQTT antes de cerrar la conexión — evita dejar el brazo
    colgado en una posición arbitraria con carga sobre los servos.
    """
    log("Apagando sistema...", "INFO")

    # Import local (no al inicio del archivo) para evitar acoplar main.py
    # a los internals de servos.py más de lo necesario para esta única
    # rutina de emergencia.
    from servos import move_transito, servo_set, servo_pwm

    try:
        move_transito()
        servo_set(1, 90, smooth=True)  # Base a HOME
    except Exception:
        pass

    for sid, pwm in servo_pwm.items():
        try:
            pwm.duty(0)
            pwm.deinit()
        except Exception:
            pass

    if state.client is not None:
        try:
            from mqtt import mqtt_publish
            mqtt_publish({"event": "offline"})
            state.client.disconnect()
        except Exception:
            pass

    log("Sistema detenido.", "INFO")


# ============================================================================
# LOOP PRINCIPAL
# ============================================================================

def main():
    """
    Secuencia de arranque: boot info → hardware (servos, sensor) → red
    (WiFi, MQTT) → WDT opcional → loop principal con 5 tareas periódicas
    no bloqueantes, coordinadas por comparación de ticks_ms() (patrón
    estándar en sistemas embebidos bare-metal: evita usar sleep() como
    temporizador, lo que bloquearía todas las demás tareas).
    """
    print_boot_info()

    if not init_servos():
        log("Fallo critico en servos", "CRITICAL")
        return
    if not init_sensor():
        log("Fallo critico en sensor", "CRITICAL")
        return

    if not connect_wifi():
        log("Sin WiFi. Deteniendo.", "CRITICAL")
        return
    connect_mqtt(on_message, publish_status)

    if WDT_ENABLED:
        wdt = WDT(timeout=TIMING["wdt_timeout_ms"])
        log("WDT activado ({} ms)".format(TIMING["wdt_timeout_ms"]), "INFO")
    else:
        wdt = None
        log("WDT desactivado (modo desarrollo)", "WARNING")

    log_sep("SISTEMA OPERATIVO - MODO {}".format(state.mode))

    now = ticks_ms()
    state.t_mqtt_poll = now
    state.t_heartbeat = now
    state.t_gc = now
    state.t_sensor = now

    try:
        while True:
            now = ticks_ms()
            state.loop_count += 1

            # 1. Alimentar WDT (si está activo) — debe ser la primera
            #    operación de cada iteración para minimizar la chance de
            #    que una tarea lenta más abajo dispare un reset espurio.
            if wdt is not None:
                wdt.feed()

            # 2. Poll MQTT no bloqueante + reconexión automática.
            if ticks_diff(now, state.t_mqtt_poll) >= TIMING["mqtt_poll_ms"]:
                if state.mqtt_ok:
                    if not safe_poll():
                        log("Reconectando MQTT...", "WARNING")
                        connect_mqtt(on_message, publish_status)
                else:
                    if wifi_is_up():
                        connect_mqtt(on_message, publish_status)
                    else:
                        log("WiFi perdido - reconectando...", "WARNING")
                        connect_wifi()
                        connect_mqtt(on_message, publish_status)
                state.t_mqtt_poll = now

            # 3. Lectura de sensor con debounce (no ejecutar si brazo ocupado:
            #    evita procesar una nueva detección mientras el brazo todavía
            #    está resolviendo la anterior).
            if ticks_diff(now, state.t_sensor) >= TIMING["sensor_poll_ms"]:
                if not state.arm_busy:
                    process_sensor_event()
                state.t_sensor = now

            # 4. Heartbeat / telemetría periódica.
            if ticks_diff(now, state.t_heartbeat) >= TIMING["heartbeat_ms"]:
                log("Heartbeat | loop={} | mem={} B | mqtt={} | modo={}".format(
                    state.loop_count, gc.mem_free(),
                    "OK" if state.mqtt_ok else "DISC", state.mode), "INFO")
                if state.mqtt_ok:
                    publish_status()
                state.t_heartbeat = now

            # 5. Garbage collector manual — en un sistema de memoria
            #    limitada (RAM del ESP32) y ejecución 24/7, dejar el GC
            #    solo al criterio del intérprete puede acumular
            #    fragmentación; forzarlo periódicamente mantiene el
            #    heap predecible.
            if ticks_diff(now, state.t_gc) >= TIMING["gc_ms"]:
                before = gc.mem_free()
                gc.collect()
                after = gc.mem_free()
                log("GC: {} -> {} bytes".format(before, after), "DEBUG")
                state.t_gc = now

            # 6. Ceder ciclos al RTOS — ahorro de energía, no bloquea WDT.
            idle()

    except KeyboardInterrupt:
        log("Interrupcion por teclado (Ctrl+C)", "WARNING")

    except Exception as exc:
        log("ERROR NO MANEJADO: {}".format(exc), "CRITICAL")
        sys.print_exception(exc)

    finally:
        _shutdown()


# ============================================================================
# ENTRY POINT
# ============================================================================
# Al ejecutarse como main.py (nombre reservado por MicroPython para el
# script de arranque automático), __name__ es "__main__". Se envuelve en
# try/except para que cualquier excepción no capturada dentro de main()
# quede registrada con traceback completo antes de que el ESP32 quede
# en un estado indefinido — evita un fallo silencioso.
# ============================================================================
if __name__ == "__main__":
    try:
        main()
    except Exception as fatal:
        log_sep("ERROR FATAL")
        sys.print_exception(fatal)
        log_sep()
