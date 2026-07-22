"""
================================================================================
sensor.py — Lectura y debounce del sensor infrarrojo KY-032
================================================================================
Autor: Francisco Bevilacqua | Versión: 5.0

Responsabilidad única (SRP):
    Este módulo SOLO sabe leer el pin del sensor KY-032 y aplicar debounce
    por muestreo consecutivo. NO decide qué hacer cuando se detecta una
    caja (eso depende del modo de operación: SEMI_AUTO vs AUTOMATICO) y NO
    publica eventos MQTT — esa lógica de negocio vive en commands.py.

    Esta separación (hardware puro vs. decisión de negocio) es la misma
    que aplica servos.py: mantener sensor.py sin dependencia de mqtt.py
    permite, por ejemplo, escribir un test unitario que simule el pin y
    verifique el debounce sin necesitar una conexión MQTT activa.

Dependencias:
    machine (Pin) → acceso al GPIO del sensor
    config  → PINS["sensor_ky032"], TIMING["sensor_debounce"]
    state.log
================================================================================
"""

from machine import Pin

from config import PINS, TIMING
from state import log

# Referencia al pin del sensor, inicializada por init_sensor().
# Vive como variable de módulo (no en state.py) porque es un HANDLE de
# hardware de bajo nivel que ningún otro módulo necesita tocar
# directamente — solo se consume a través de las funciones públicas de
# este archivo, igual que servo_pwm en servos.py.
_sensor_pin = None

# Contador de muestras consecutivas con detección positiva. Privado a este
# módulo: es un detalle de implementación del algoritmo de debounce, no
# estado de negocio que otros módulos necesiten leer.
_consecutive = 0


def init_sensor():
    """
    Inicializa el pin del sensor KY-032 con pull-up interno.

    [HW] El KY-032 es un módulo de salida digital de colector abierto: sin
    pull-up, el pin flotaría en estado indeterminado cuando el sensor no
    detecta nada, generando falsos positivos por ruido. Pull-up interno del
    ESP32 evita tener que agregar una resistencia externa en la placa.

    Returns:
        bool: True si el pin se inicializó correctamente.
    """
    global _sensor_pin
    log("Inicializando sensor KY-032 en GPIO {}...".format(PINS["sensor_ky032"]), "INFO")
    try:
        # KY-032: OUT -> LOW cuando detecta obstáculo, HIGH en reposo.
        _sensor_pin = Pin(PINS["sensor_ky032"], Pin.IN, Pin.PULL_UP)
        log("Sensor KY-032 OK", "INFO")
        return True
    except Exception as exc:
        log("Error inicializando sensor: {}".format(exc), "CRITICAL")
        return False


def read_sensor():
    """
    Lectura cruda (instantánea, sin debounce) del sensor KY-032.

    Returns:
        bool: True si detecta un objeto (señal LOW), False si no hay
            objeto (señal HIGH) o si el sensor no fue inicializado
            (fail-safe: se asume "sin detección" ante cualquier duda,
            para no disparar movimientos del brazo por un sensor roto).
    """
    if _sensor_pin is None:
        return False
    return _sensor_pin.value() == 0  # LOW = detección


def poll_debounced():
    """
    Debe llamarse periódicamente (cada TIMING["sensor_poll_ms"]) desde el
    loop principal. Acumula muestras consecutivas positivas y confirma una
    detección solo cuando se alcanza TIMING["sensor_debounce"] muestras
    seguidas — filtra ruido eléctrico y falsos positivos de una sola
    lectura espuria.

    Comportamiento tipo "flanco de subida confirmado": al confirmar,
    resetea el contador interno inmediatamente, de modo que esta función
    retorna True EXACTAMENTE UNA VEZ por objeto detectado, no en cada
    poll mientras el objeto sigue frente al sensor. El caller (ver
    commands.process_sensor_event) no necesita lógica adicional de
    "ya procesé este evento".

    Returns:
        bool: True únicamente en el poll donde se confirma una nueva
            detección; False en todos los demás casos (sin objeto,
            o acumulando muestras todavía por debajo del umbral).
    """
    global _consecutive

    if read_sensor():
        _consecutive += 1
    else:
        _consecutive = 0

    if _consecutive >= TIMING["sensor_debounce"]:
        _consecutive = 0  # reset inmediato: evita retriggers en el mismo objeto
        return True

    return False
