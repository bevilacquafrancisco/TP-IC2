"""
================================================================================
config.py — Configuración centralizada del firmware ESP32
================================================================================
Proyecto:    Brazo Robótico Pick & Place — Ingeniería en Computación II
Autor:       Francisco Bevilacqua
Versión:     5.0 (modularización de robot_main.py monolítico)
MicroPython: v1.20.0 on 2023-04-26; ESP32 module with ESP32

Responsabilidad única (SRP):
    Este módulo es la ÚNICA fuente de verdad para constantes de configuración
    (credenciales de red, pines GPIO, timing, posiciones calibradas de los
    servos). Ningún otro módulo del firmware define estos valores por su
    cuenta — todos importan desde acá.

    Ventaja concreta para este proyecto: si se recalibra una posición del
    brazo, o se cambia el pin de un servo, se edita UN SOLO archivo. En el
    robot_main.py original monolítico, estos valores estaban mezclados con
    lógica de negocio en el mismo archivo de 1000 líneas, lo que dificultaba
    ubicarlos rápidamente durante la calibración en banco de pruebas.

Dependencias: ninguna (módulo hoja — no importa nada del proyecto).
    Esto es intencional: config.py debe poder importarse desde cualquier
    otro módulo sin riesgo de import circular, porque nunca importa nada
    a su vez.
================================================================================
"""

# ------------------------------------------------------------------------
# WIFI — credenciales y timeout de conexión a la red local
# ------------------------------------------------------------------------
WIFI = {
    "ssid":      "nombre_de_red_wifi",
    "password":  "contraseña_wifi",
    # Tiempo máximo en segundos que el sistema espera para conectarse a la
    # red WiFi antes de abortar y reintentar en el loop principal.
    "timeout_s": 20,
}

# ------------------------------------------------------------------------
# MQTT — broker privado, credenciales y topics
# ------------------------------------------------------------------------
MQTT = {
    "broker":    "192.168.x.x",   # IP local de la PC (ver README Fase 1, paso 5)
    "port":      1883,
    "keepalive": 60,
    "topic_cmd": b"robot/cmd",       # Suscripción: comandos entrantes desde la GUI
    "topic_log": b"robot/log",       # Publicación: eventos/telemetría hacia la GUI
    "client_id": b"ESP32_Francisco_IC2",
    # [SEC] Credenciales del broker privado. En v4.0 no existían porque
    # test.mosquitto.org acepta clientes anónimos (broker público sin
    # autenticación). Ahora son obligatorias: Mosquitto privado rechaza
    # cualquier conexión sin user/password válidos (allow_anonymous false).
    "user":      b"esp32",
    "password":  b"password_generada",  # debe coincidir con el passwd del broker
}

# ------------------------------------------------------------------------
# PINES — GPIOs seguros del ESP32 (no afectan boot strapping)
# ------------------------------------------------------------------------
PINS = {
    "servo_base":   25,
    "servo_hombro": 26,
    "servo_codo":   27,
    "servo_pinza":  32,
    "sensor_ky032": 33,   # KY-032: OUT -> GPIO33, INPUT_PULL_UP, LOW=detectado
}

# ------------------------------------------------------------------------
# PWM — Frecuencia y rango de duty cycle para servomotores SG90 a 50Hz
# ------------------------------------------------------------------------
PWM_FREQ = 50  # Hz — estándar para servos SG90 (período de 20ms)

# Rango de duty cycle para SG90 a 50Hz con resolución 10-bit (0-1023):
#   0°   -> ~25  (~0.5ms / 20ms * 1023)
#   90°  -> ~76  (~1.5ms / 20ms * 1023)
#   180° -> ~128 (~2.5ms / 20ms * 1023)
PWM_MIN_DUTY = 25
PWM_MAX_DUTY = 128

# ------------------------------------------------------------------------
# TIMING — todos los intervalos y umbrales temporales del sistema, en un
# solo lugar para facilitar el ajuste fino durante pruebas de campo.
# ------------------------------------------------------------------------
TIMING = {
    "mqtt_poll_ms":     200,    # frecuencia de check_msg() (poll no bloqueante)
    "heartbeat_ms":    20000,   # heartbeat / telemetría periódica hacia la GUI
    "gc_ms":           15000,   # intervalo del garbage collector manual
    "sensor_poll_ms":    100,   # frecuencia de lectura del sensor KY-032
    "sensor_debounce":     5,   # muestras consecutivas para confirmar detección
    "servo_step_ms":      15,   # ms entre pasos de movimiento suave
    "servo_step_deg":      2,   # grados por paso (movimiento suave)
    "wdt_timeout_ms":   8000,   # watchdog software
}

# WDT: False=desarrollo con Thonny, True=producción sin Thonny.
# [HW] Si se deja en True mientras se debuguea paso a paso en Thonny, el
# watchdog reinicia el ESP32 en medio de una sesión de breakpoints —
# confundible con un bug real. Mantener False durante desarrollo.
WDT_ENABLED = False

# Número máximo de cajas por pallet antes de requerir vaciado manual/GUI.
MAX_CAJAS_PALLET = 3

# ------------------------------------------------------------------------
# POSICIONES CALIBRADAS (ángulos en grados)
# Formato: [base, hombro, codo, pinza]. None = no mover ese servo en este paso.
# Modificar SOLO acá para re-calibrar sin tocar la lógica en servos.py.
# ------------------------------------------------------------------------
POS = {
    # Posición HOME / reposo seguro
    "home": [90, 90, 90, 90],

    # Tránsito seguro entre zonas (brazo levantado para no tumbar cajas).
    # Se usa ANTES de girar la base, y DESPUÉS de depositar.
    "transito": [None, 90, 90, 90],

    # Zona de recolección (frente al sensor KY-032)
    "recoleccion_aprox":  [180, 12, 90, 90],  # llega con pinza abierta
    "recoleccion_agarre": [180, 12, 90, 42],  # cierra pinza para agarrar caja

    # Pallet 1 (Base=110) — posiciones de descarga por nivel de apilado
    "pallet1_transito": [110, 90, 90, 42],
    "pallet1_caja1":    [110,  8, 90, 42],
    "pallet1_caja2":    [110, 20, 102, 42],
    "pallet1_caja3":    [110, 30, 105, 42],

    # Pallet 2 (Base=70) — posiciones de descarga por nivel de apilado
    "pallet2_transito": [70, 90, 90, 42],
    "pallet2_caja1":    [70,  8, 90, 42],
    "pallet2_caja2":    [70, 20, 102, 42],
    "pallet2_caja3":    [70, 30, 110, 42],
}

# ------------------------------------------------------------------------
# CAUSAS DE RESET del ESP32 — traducción de los códigos numéricos de
# machine.reset_cause() a texto legible para logs y para publish_status().
# ------------------------------------------------------------------------
RESET_REASONS = {
    1: "PWRON_RESET - encendido normal",
    2: "HARD_RESET - reset por pin EN",
    3: "WDT_RESET - WATCHDOG (loop bloqueo la CPU)",
    4: "DEEPSLEEP_RESET - salida de deep sleep",
    5: "SOFT_RESET - reset por software",
    6: "BROWNOUT_RESET - TENSION INSUFICIENTE (revisar USB)",
    7: "SDIO_RESET",
}
