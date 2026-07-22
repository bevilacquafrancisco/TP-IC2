"""
================================================================================
wifi.py — Gestión de la conexión WiFi (capa de red, modo estación)
================================================================================
Autor: Francisco Bevilacqua | Versión: 5.0

Responsabilidad única (SRP):
    Establecer y verificar la conexión WiFi. Ningún otro módulo llama
    directamente a `network.WLAN(...)` — este es el único punto de contacto
    del firmware con la interfaz de red física.

Dependencias:
    config  → WIFI (ssid, password, timeout_s)
    state   → state.wifi (referencia a la interfaz, para que mqtt.py pueda
              consultar RSSI y wifi_is_up() sin importar `network` de nuevo)
    state.log / state.log_sep → logging consistente con el resto del firmware
================================================================================
"""

import network
from time import sleep_ms

from config import WIFI
from state import state, log


def connect_wifi():
    """
    Conecta el ESP32 a la red WiFi configurada en config.WIFI.

    Comportamiento:
        - Si ya hay una conexión activa (ej. tras un soft-reset que no
          reinicializó la interfaz), retorna True inmediatamente sin
          reintentar la conexión — evita un timeout innecesario.
        - Si no, intenta conectar y espera en polling de 500ms hasta
          WIFI["timeout_s"] segundos, imprimiendo un "." de progreso cada
          intento (y salto de línea cada 5s) para feedback visual en Thonny.

    [HW] wifi.active(True) + wifi.connect() es no bloqueante en MicroPython;
    el polling manual de wifi.isconnected() es el mecanismo estándar para
    esperar sin congelar el intérprete — a diferencia de un `while True`
    sin timeout, que dejaría el sistema colgado indefinidamente si el
    router está apagado.

    Returns:
        bool: True si la conexión quedó establecida, False si se agotó
            el timeout o si ocurrió una excepción de hardware/driver.
    """
    log("Conectando a WiFi '{}'...".format(WIFI["ssid"]), "INFO")
    try:
        state.wifi = network.WLAN(network.STA_IF)
        state.wifi.active(True)

        if state.wifi.isconnected():
            log("WiFi ya conectado: {}".format(state.wifi.ifconfig()[0]), "INFO")
            return True

        state.wifi.connect(WIFI["ssid"], WIFI["password"])

        # timeout_s * 2 porque cada iteración del polling espera 500ms
        # (2 iteraciones = 1 segundo real).
        timeout = WIFI["timeout_s"] * 2
        dots = 0
        while not state.wifi.isconnected() and timeout > 0:
            sleep_ms(500)
            timeout -= 1
            dots += 1
            if dots % 10 == 0:  # cada 10 puntos (5 segundos) → salto de línea
                print()
            print(".", end="")
        print()

        if state.wifi.isconnected():
            cfg = state.wifi.ifconfig()  # (ip, mascara, gateway, dns)
            log("WiFi OK | IP: {} | GW: {}".format(cfg[0], cfg[2]), "INFO")
            return True

        log("Timeout WiFi", "ERROR")
        return False

    except Exception as exc:
        log("Error WiFi: {}".format(exc), "CRITICAL")
        return False


def wifi_is_up():
    """
    Verifica si la interfaz WiFi está inicializada y conectada.

    Usado por mqtt.py antes de intentar reconectar MQTT (no tiene sentido
    reintentar el broker si ni siquiera hay capa de red disponible), y por
    commands.publish_status() para reportar RSSI.

    Returns:
        bool: True si state.wifi existe y reporta isconnected()==True.
    """
    return state.wifi is not None and state.wifi.isconnected()
