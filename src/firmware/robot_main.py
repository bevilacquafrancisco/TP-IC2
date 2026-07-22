"""
=================================================================================
BRAZO ROBOTICO PICK & PLACE - ESP32 MICROPYTHON
=================================================================================
 
Proyecto:       Brazo Robótico Industrial Pick & Place
Materia:        Ingenieria en Computacion II
Versión:        4.0
MicroPython:    v1.20.0 on 2023-04-26; ESP32 module with ESP32
Protocolo:      MQTT sobre TCP (direccion-IP:1883)
 
MEJORAS v4.0 (sobre v3.x):
  - QoS 1 en suscripción a robot/cmd: garantía de entrega de comandos.
  - clean_session=False: el broker reintenta comandos perdidos al reconectar.
  - Deduplicación de comandos via msg_id: duplicados QoS 1 descartados.
  - Re-sincronización activa al reconectar: publica online + status completo.
  - Guards de idempotencia reforzados en todos los comandos de acción.
 
HARDWARE:
  Microcontrolador : ESP32 NodeMCU
  Servomotores     : 4x SG90 (alimentados con fuente 5V/2A externa)
  Sensor           : KY-032 (detector IR de obstáculos)
  Alimentación ESP : USB-C desde PC 
 
PINES (GPIOs seguros - no afectan boot):
  Servo Base    (S1) -> GPIO 25  (PWM)
  Servo Hombro  (S2) -> GPIO 26  (PWM)
  Servo Codo    (S3) -> GPIO 27  (PWM)
  Servo Pinza   (S4) -> GPIO 32  (PWM)
  Sensor KY-032      -> GPIO 33  (Digital Input, Pull-Up interno)
  NOTA: GND de servos conectado a GND común con fuente 5V y ESP32.
        VCC de servos conectado SOLO a fuente 5V/2A (NO al 3.3V del ESP32).
 
TOPICS MQTT:
  Publicador(ESP32) -> Suscriptor(GUI)   : robot/log    (estados, eventos, telemetría)
  Publicador(PC)    -> Suscriptor(ESP32) : robot/cmd    (comandos de control)
 
MODOS DE OPERACIÓN:
  MANUAL       : Control directo de cada servo vía sliders + movimientos
                 preconfigurados (recolectar, depositar, home).
  SEMI_AUTO    : KY-032 detecta caja -> alerta en GUI -> usuario decide
                 destino (Pallet 1, Pallet 2 o ignorar).
  AUTOMATICO   : KY-032 detecta caja -> secuencia completa automática.
                 Llena Pallet 1 (3 cajas) luego Pallet 2 (3 cajas).
                 Si pallet lleno, espera confirmación de vaciado desde GUI.
 
POSICIONES CALIBRADAS (ángulos en grados):
  Zona Recolección (frente al sensor KY-032):
    Base=180, Hombro=12, Codo=90, Pinza=90 (abierta) -> 50 (cerrada)
 
  Tránsito seguro (para no tumbar cajas al moverse entre zonas):
    Hombro=90, Codo=90  (levanta el brazo antes de girar la base)
 
  Pallet 1 (Base=110):
    Caja 1: Hombro=15, Codo=115
    Caja 2: Hombro=20, Codo=102
    Caja 3: Hombro=30, Codo=95
 
  Pallet 2 (Base=70):
    Caja 1: Hombro=15, Codo=115
    Caja 2: Hombro=20, Codo=102
    Caja 3: Hombro=30, Codo=95
 
PROTOCOLO DE COMANDOS (JSON sobre robot/cmd):
  Modo:
    {"cmd":"set_mode",   "mode":"MANUAL"|"SEMI_AUTO"|"AUTOMATICO"}
  Manual - servo individual:
    {"cmd":"servo",      "id":1-4, "angle":0-180}
  Manual - movimientos preconfigurados:
    {"cmd":"move",       "action":"home"|"recolectar"|"abrir_pinza"|"cerrar_pinza"}
  Semi-automático - respuesta a alerta de caja detectada:
    {"cmd":"semi_decision", "dest":"P1"|"P2"|"ignorar"}
  Pallet vaciado (habilita continuar en modo automático):
    {"cmd":"pallet_clear",  "pallet":1|2}
  Solicitar estado completo:
    {"cmd":"status"}
 
EVENTOS PUBLICADOS (JSON sobre robot/log):
  {"event":"online",        "reset_cause":N, "mem_free":N, ...}
  {"event":"status",        "mode":..., "pallets":..., ...}
  {"event":"sensor",        "detected":bool}
  {"event":"servo_ack",     "id":N, "angle":N}
  {"event":"move_start",    "action":...}
  {"event":"move_done",     "action":...}
  {"event":"box_detected"}  (semi-auto: espera decision del usuario)
  {"event":"box_collected", "dest":"P1"|"P2", "level":N}
  {"event":"pallet_full",   "pallet":1|2}
  {"event":"error",         "msg":...}
  {"event":"offline"}
 
AHORRO DE ENERGÍA:
  - machine.idle() en cada iteración del loop cede ciclos al RTOS
  - Servo desactivado (duty=0) cuando no se usa para evitar vibración y consumo
  - Sensor KY-032 leído por polling con debounce (no interrupción continua)
  - GC periódico para mantener memoria libre
 
WDT:
  WDT_ENABLED = False durante desarrollo con Thonny.
  WDT_ENABLED = True  en producción (guardar como main.py en la ESP32).
 
=================================================================================
"""
 
# -- Imports ------------------------------------------------------------------
import gc       # Garbage Collector: gestión manual de memoria heap
import json     # JSON para comunicación estructurada con la GUI
import sys      # Acceso al sistema: versión, excepciones, streams
import network  # Configuración de interfaces de red (WiFi)
 
from machine import Pin, PWM, WDT, reset_cause, idle
from time import sleep_ms, ticks_diff, ticks_ms
from umqtt.simple import MQTTClient     ## MQTTClient es una clase que implementa las operaciones básicas del protocolo
 
# =============================================================================
# CONFIGURACIÓN — MODIFICAR SOLO ESTA SECCIÓN
# =============================================================================
 
WIFI = {
    "ssid":      "nombre_de_red_wifi",
    "password":  "contraseña_wifi",
    "timeout_s": 20, # Tiempo máximo en segundos que el sistema espera para conectarse a la red WiFi antes de abortar y seguir intentando en el loop principal.
}
 
MQTT = {
    "broker":    "192.168.x.x",   # IP local de la PC (ver README Fase 1, paso 5)
    "port":      1883,
    "keepalive": 60,
    "topic_cmd": b"robot/cmd",
    "topic_log": b"robot/log",
    "client_id": b"ESP32_Francisco_IC2",
    # [SEC] Credenciales del broker privado. En v4.0 no existían porque
    # test.mosquitto.org acepta clientes anónimos (broker público sin
    # autenticación). Ahora son obligatorias: Mosquitto privado rechaza
    # cualquier conexión sin user/password válidos (allow_anonymous false).
    #
    # [SEC] Limitación de alcance conocida: estas credenciales quedan en
    # texto plano dentro del firmware .py. MicroPython no tiene un mecanismo
    # estándar de variables de entorno como Python en servidor (no hay
    # proceso "shell" que las inyecte). La mitigación real para producción
    # sería un archivo secrets.py separado, agregado a .gitignore, e
    # importado aquí — documentado como trabajo futuro. Para esta entrega
    # académica, hardcodear en config.py es una limitación aceptada y
    # declarada, no un descuido.
    "user":      b"esp32",
    "password":  b"password_generada",  # debe coincidir con el passwd del broker
}
 
# GPIOs seguros (no afectan boot strapping del ESP32)
PINS = {
    "servo_base":   25,
    "servo_hombro": 26,
    "servo_codo":   27,
    "servo_pinza":  32,
    "sensor_ky032": 33,   # KY-032: OUT -> GPIO33, INPUT_PULL_UP, LOW=detectado
}
 
# Frecuencia PWM para SG90: 50 Hz (período 20ms)
PWM_FREQ = 50
 
# Rango de duty cycle para SG90 a 50Hz con resolución 10-bit (0-1023):
#   0°   -> ~25  (~0.5ms / 20ms * 1023)
#   90°  -> ~76  (~1.5ms / 20ms * 1023)
#   180° -> ~128 (~2.5ms / 20ms * 1023)
PWM_MIN_DUTY = 25
PWM_MAX_DUTY = 128
 
TIMING = {
    "mqtt_poll_ms":    200,    # frecuencia de check_msg
    "heartbeat_ms":   20000,   # heartbeat / telemetría periódica
    "gc_ms":          15000,   # garbage collector
    "sensor_poll_ms":   100,   # polling del sensor KY-032
    "sensor_debounce":    5,   # muestras consecutivas para confirmar detección
    "servo_step_ms":     15,   # ms entre pasos de movimiento suave
    "servo_step_deg":     2,   # grados por paso (movimiento suave)
    "wdt_timeout_ms":  8000,   # watchdog software
}
 
# WDT: False=desarrollo con Thonny, True=producción sin Thonny
WDT_ENABLED = False
 
# Número máximo de cajas por pallet
MAX_CAJAS_PALLET = 3
 
# =============================================================================
# POSICIONES CALIBRADAS
# Modificar aquí para re-calibrar sin tocar la lógica del programa.
# Formato: [base, hombro, codo, pinza]
# =============================================================================
 
POS = {
    # Posición HOME / reposo seguro
    "home": [90, 90, 90, 90],
 
    # Tránsito seguro entre zonas (brazo levantado para no tumbar cajas)
    # Se usa ANTES de girar la base, y DESPUES de depositar
    "transito": [None, 90, 90, 90],  # None = no mover la base en este paso
 
    # Zona de recolección (frente al sensor KY-032)
    "recoleccion_aprox": [180, 12, 90, 90],   # llega con pinza abierta
    "recoleccion_agarre": [180, 12, 90, 44],   # cierra pinza para agarrar caja
 
    # Pallet 1 (Base=110) - posiciones de descarga por nivel de apilado
    "pallet1_transito": [110, 90, 90, 44],     # base girada, brazo arriba
    "pallet1_caja1":    [110, 8, 90, 44],    # 1er caja en la base
    "pallet1_caja2":    [110, 20, 102, 44],    # 2da caja sobre la 1era
    "pallet1_caja3":    [110, 30, 105,  44],    # 3er caja en lo alto
 
    # Pallet 2 (Base=70) - posiciones de descarga por nivel de apilado
    "pallet2_transito": [70, 90, 90, 46],      # base girada, brazo arriba
    "pallet2_caja1":    [70, 8, 90, 46],
    "pallet2_caja2":    [70, 20, 102, 46],
    "pallet2_caja3":    [70, 30, 110,  46],
}
 
# =============================================================================
# ESTADO GLOBAL
# =============================================================================
 
# Hardware
servo_pwm   = {}   # {1: PWM, 2: PWM, 3: PWM, 4: PWM}, instancias de PWM para cada servo, inicializadas en init_servos()
servo_angle = {1: 90, 2: 90, 3: 90, 4: 90}   # ángulo actual de cada servo, "ángulo conocido por software"
sensor_pin  = None # Pin del sensor KY-032, inicializado en init_sensor()
client      = None # Cliente MQTT, inicializado en connect_wifi() y usado en mqtt_publish()
wifi        = None # Interfaz WiFi, inicializada en connect_wifi() y usada en wifi_is_up()
mqtt_ok     = False # True si la conexión MQTT está activa, False si hubo error al publicar (reintenta en el loop principal)
 
# Lógica de negocio
mode              = "MANUAL"        # MANUAL | SEMI_AUTO | AUTOMATICO
pallet_count      = {1: 0, 2: 0}   # cajas depositadas en cada pallet
pallet_full       = {1: False, 2: False} # True si el pallet alcanzó MAX_CAJAS_PALLET, bloquea depositar más hasta recibir comando de vaciado
arm_busy          = False           # True mientras ejecuta una secuencia
semi_pending      = False           # True si hay caja detectada esperando decisión
sensor_consecutive = 0              # contador de muestras para debounce
 
# Contadores diagnóstico
_loop_count      = 0 # incrementado en cada iteración del loop principal, útil para diagnóstico y telemetría.
_cmd_received    = 0 # total de comandos recibidos (incluye duplicados QoS 1)
_reconnect_count = 0 # total de reconexiones WiFi/MQTT realizadas, útil para diagnóstico de estabilidad de la conexión.
 
# Deduplicación de comandos QoS 1.
# Almacena el último msg_id procesado para descartar duplicados que el broker
# reenvía automáticamente cuando no recibió PUBACK a tiempo.
# Con QoS 1 es garantizado que un mensaje llega AL MENOS UNA VEZ; sin
# deduplicación podría ejecutarse dos veces. Esta variable rompe esa ambigüedad.
_last_cmd_id = None
 
# Timestamps
_t_mqtt_poll  = 0 # última vez que se llamó client.check_msg() para procesar comandos entrantes
_t_heartbeat  = 0 # última vez que se publicó un mensaje de heartbeat/telemetría
_t_gc         = 0 # última vez que se llamó gc.collect() para liberar memoria
_t_sensor     = 0 # última vez que se leyó el sensor KY-032 para detección de cajas (con debounce)
 
# =============================================================================
# LOGGING
# =============================================================================
 
def log(msg, level="INFO"):
    """Log formateado para consola Thonny: [timestamp] [LEVEL] mensaje"""
    print("[{:010d}] [{:<8s}] {}".format(ticks_ms(), level, msg)) # timestamp en ms desde el arranque, nivel de log alineado a la izquierda, mensaje
 
def log_sep(title=""):
    line = "=" * 70
    if title:
        print(line)
        pad = (70 - len(title)) // 2
        print(" " * pad + title)
    print(line)
 
# =============================================================================
# INFORMACIÓN DE ARRANQUE
# =============================================================================
 
_RESET_REASONS = {
    1: "PWRON_RESET - encendido normal",
    2: "HARD_RESET - reset por pin EN",
    3: "WDT_RESET - WATCHDOG (loop bloqueo la CPU)",
    4: "DEEPSLEEP_RESET - salida de deep sleep",
    5: "SOFT_RESET - reset por software",
    6: "BROWNOUT_RESET - TENSION INSUFICIENTE (revisar USB)",
    7: "SDIO_RESET",
}
 
def print_boot_info():
    log_sep("BRAZO ROBOTICO PICK & PLACE v4.0")
    cause  = reset_cause()
    reason = _RESET_REASONS.get(cause, "DESCONOCIDO codigo {}".format(cause))
    log("Reset anterior : {}".format(reason), "INFO")
    log("Causa numerica : {}".format(cause),  "DEBUG")
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
 
# =============================================================================
# CONTROL DE SERVOS
# =============================================================================
 
def angle_to_duty(angle):
    """
    Convierte ángulo (0-180°) a duty cycle PWM 10-bit para SG90 a 50Hz.
    Fórmula: mapeo lineal entre PWM_MIN_DUTY y PWM_MAX_DUTY.
    """
    angle = max(0, min(180, angle)) # limitar ángulo a rango válido
    duty = int(PWM_MIN_DUTY + (angle / 180.0) * (PWM_MAX_DUTY - PWM_MIN_DUTY)) # mapeo lineal
    return duty # duty cycle correspondiente al ángulo dado
 
def servo_set(servo_id, angle, smooth=False):
    """
    Mueve un servo al ángulo indicado.
    Si smooth=True, lo hace paso a paso para movimiento suave.
    servo_id: 1=Base, 2=Hombro, 3=Codo, 4=Pinza
    """
    if servo_id not in servo_pwm:
        log("Servo {} no inicializado".format(servo_id), "ERROR")
        return False
 
    angle = max(0, min(180, int(angle))) # validar y convertir a entero
    current = servo_angle[servo_id] # ángulo actual conocido por software
 
    if smooth and abs(angle - current) > TIMING["servo_step_deg"]: # solo hacer movimiento suave si la diferencia es mayor al paso definido, para evitar movimientos innecesarios en ajustes pequeños
        step = TIMING["servo_step_deg"] if angle > current else -TIMING["servo_step_deg"] # determinar dirección del paso
        pos = current
        while (step > 0 and pos < angle) or (step < 0 and pos > angle): # iterar hasta alcanzar el ángulo objetivo
            pos += step # avanzar un paso
            pos = max(0, min(180, pos)) # asegurar que el paso no exceda los límites de 0-180°
            servo_pwm[servo_id].duty(angle_to_duty(pos)) # actualizar PWM al nuevo ángulo del paso
            sleep_ms(TIMING["servo_step_ms"]) # esperar entre pasos para crear efecto de movimiento suave
    
    servo_pwm[servo_id].duty(angle_to_duty(angle)) # finalmente, asegurar que el servo llega al ángulo exacto solicitado
    servo_angle[servo_id] = angle # actualizar el ángulo conocido por software después del movimiento
    log("Servo {} -> {}°".format(servo_id, angle), "DEBUG")
    return True
 
def servo_idle(servo_id):
    """
    Desactiva el PWM del servo (duty=0) para ahorrar energía y
    evitar vibración cuando el servo no necesita mantener posición.
    NOTA: Solo usar cuando el brazo está en reposo y no hay carga.
    """
    if servo_id in servo_pwm:
        servo_pwm[servo_id].duty(0)
 
def init_servos():
    """Inicializa los 4 servos PWM y los lleva a posición HOME."""
    log("Inicializando servos...", "INFO")
    names = {1: "Base", 2: "Hombro", 3: "Codo", 4: "Pinza"} # para logs más legibles
    pin_ids = [
        (1, PINS["servo_base"]), # GPIO 25
        (2, PINS["servo_hombro"]), # GPIO 26
        (3, PINS["servo_codo"]), # GPIO 27
        (4, PINS["servo_pinza"]), # GPIO 32
    ]
    try:
        for sid, gpio in pin_ids: # iterar sobre cada servo y su GPIO correspondiente
            pwm = PWM(Pin(gpio), freq=PWM_FREQ) # inicializar PWM en el pin del servo con la frecuencia adecuada
            pwm.duty(angle_to_duty(90))   # posición neutral
            servo_pwm[sid] = pwm # almacenar la instancia de PWM en el diccionario global para control futuro
            log("  Servo {} ({}) -> GPIO {} OK".format(sid, names[sid], gpio), "DEBUG")
        
        sleep_ms(500)
        log("Moviendo a HOME...", "INFO")
        move_sequence("home")
        log("Servos inicializados en HOME", "INFO")
        return True
    except Exception as exc:
        log("Error inicializando servos: {}".format(exc), "CRITICAL")
        return False
 
# =============================================================================
# SENSOR KY-032
# =============================================================================
 
def init_sensor():
    """Inicializa el pin del sensor KY-032 con pull-up interno."""
    global sensor_pin # sensor_pin es una variable global que se asigna aquí para ser usada en la función read_sensor() y en otras partes del programa donde se necesite leer el estado del sensor KY-032.
    log("Inicializando sensor KY-032 en GPIO {}...".format(PINS["sensor_ky032"]), "INFO")
    try:
        # KY-032: OUT -> LOW cuando detecta obstáculo, HIGH en reposo
        # Pull-up interno para estabilizar la señal
        sensor_pin = Pin(PINS["sensor_ky032"], Pin.IN, Pin.PULL_UP)
        log("Sensor KY-032 OK", "INFO")
        return True
    except Exception as exc:
        log("Error inicializando sensor: {}".format(exc), "CRITICAL")
        return False
 
def read_sensor():
    """
    Lee el sensor KY-032.
    Retorna True si detecta objeto (señal LOW).
    Retorna False si no hay objeto (señal HIGH).
    """
    if sensor_pin is None: # si el sensor no se inicializó correctamente, consideramos que no detecta nada para evitar falsos positivos.
        return False 
    return sensor_pin.value() == 0   # LOW = detección
 
# =============================================================================
# SECUENCIAS DE MOVIMIENTO
# =============================================================================
 
def execute_pos(pos_key, smooth=True):
    """
    Ejecuta una posición predefinida del diccionario POS.
    Formato: [base, hombro, codo, pinza] donde None = no mover.
    """
    if pos_key not in POS:
        log("Posición '{}' no existe en POS".format(pos_key), "ERROR")
        return False
    angles = POS[pos_key] # ángulos objetivo para cada servo en la posición predefinida
    ids    = [1, 2, 3, 4] # IDs de los servos en orden: Base, Hombro, Codo, Pinza
    for sid, angle in zip(ids, angles): # iterar sobre cada servo y su ángulo objetivo correspondiente
        if angle is not None: # solo mover el servo si el ángulo no es None (None indica que ese servo no debe moverse en esta posición)
            servo_set(sid, angle, smooth=smooth) # mover el servo al ángulo especificado con la opción de movimiento suave
    return True
 
def move_transito():
    """
    Movimiento de tránsito seguro: levanta hombro y codo ANTES de girar base.
    Previene que el brazo tumbe cajas al moverse entre zonas.
    """
    log("Transito seguro: levantando hombro y codo", "INFO")
    servo_set(2, 90, smooth=True)   # Hombro arriba
    servo_set(3, 90, smooth=True)   # Codo arriba
    servo_set(4, 90, smooth=False)  # Pinza abierta durante tránsito
 
def move_sequence(action):
    """
    Ejecuta una secuencia de movimiento completa con nombre descriptivo.
    Usada por el modo MANUAL para movimientos preconfigurados.
    """
    global arm_busy # arm_busy es una variable global que se establece en True al iniciar la ejecución de una secuencia de movimiento para indicar que el brazo está ocupado realizando esa acción. Esto se utiliza como guard para evitar que se inicien otras acciones o movimientos mientras el brazo aún no ha terminado la secuencia actual, garantizando así que los comandos se ejecuten de manera ordenada y sin interferencias.
    arm_busy = True
    log("Iniciando movimiento: {}".format(action), "INFO")
    mqtt_publish({"event": "move_start", "action": action})
 
    try:
        if action == "home":
            servo_set(2, 90, smooth=True)
            servo_set(3, 90, smooth=True)
            servo_set(4, 90, smooth=False)
            servo_set(1, 90, smooth=True)
 
        elif action == "recolectar":
            # Lleva el brazo a zona de recolección con pinza abierta
            move_transito()
            servo_set(1, 180, smooth=True)  # Base a zona de recolección
            servo_set(2, 15,  smooth=True)  # Hombro baja
            servo_set(3, 90,  smooth=True)  # Codo
            servo_set(4, 90,  smooth=False) # Pinza abierta
 
        elif action == "abrir_pinza":
            servo_set(4, 90, smooth=False)
 
        elif action == "cerrar_pinza":
            servo_set(4, 0, smooth=False)
 
        else:
            log("Acción '{}' no reconocida".format(action), "WARNING")
            arm_busy = False
            return
 
    except Exception as exc:
        log("Error en secuencia '{}': {}".format(action, exc), "ERROR")
 
    log("Movimiento completado: {}".format(action), "INFO")
    mqtt_publish({"event": "move_done", "action": action})
    arm_busy = False
 
def pick_and_place(dest_pallet):
    """
    Secuencia completa de Pick & Place:
      1. Tránsito seguro
      2. Ir a zona de recolección
      3. Bajar y cerrar pinza (recolectar caja)
      4. Tránsito seguro (levantar brazo)
      5. Girar a pallet destino
      6. Bajar al nivel de apilado correcto
      7. Abrir pinza (depositar caja)
      8. Tránsito seguro (levantar brazo)
      9. Volver a zona de recolección
 
    dest_pallet: 1 o 2
    Retorna True si depositó correctamente, False si pallet lleno.
    """
    # estas variables globales se actualizan durante la ejecución de esta función para reflejar el estado actual del brazo y los pallets. 
    # arm_busy se establece en True al iniciar la secuencia para indicar que el brazo está ocupado, y se actualizan pallet_count y pallet_full 
    # según se van depositando cajas en los pallets para llevar un registro del número de cajas y si el pallet ha alcanzado su capacidad máxima.
    global arm_busy, pallet_count, pallet_full 
    if pallet_full[dest_pallet]:
        log("Pallet {} lleno - no se puede depositar".format(dest_pallet), "WARNING")
        mqtt_publish({"event": "pallet_full", "pallet": dest_pallet})
        return False
 
    arm_busy = True
    level = pallet_count[dest_pallet] + 1   # nivel donde se depositará (1, 2 o 3)
    p_key = "pallet{}_caja{}".format(dest_pallet, level) # clave para acceder a la posición de depósito en el diccionario POS, por ejemplo "pallet1_caja2" para el segundo nivel del pallet 1
 
    log("Pick&Place -> Pallet {} nivel {}".format(dest_pallet, level), "INFO")
    mqtt_publish({
        "event": "pick_start", # evento que indica el inicio de una secuencia de pick and place, útil para la GUI para mostrar animaciones o estados de carga.
        "dest":  "P{}".format(dest_pallet), # destino del pallet en formato "P1" o "P2", para que la GUI pueda identificar a qué pallet se dirige la caja.
        "level": level, # nivel de apilado en el pallet (1, 2 o 3), que indica la altura a la que se depositará la caja, útil para la GUI para mostrar el estado de llenado del pallet.
    })
 
    try:
        # ── PASO 1: Tránsito seguro antes de ir a recolección
        move_transito()
        # Se espera 1 segundo para dar tiempo a depositar la caja en la zona de recolección y evitar que el movimiento del brazo interfiera.
        sleep_ms(1000)
        
        # ── PASO 2: Ir a zona de recolección con pinza abierta
        # Se lee POS["recoleccion_aprox"] = [base, hombro, codo, pinza].
        servo_set(1, POS["recoleccion_aprox"][0], smooth=True)   # Base a recolección
        servo_set(2, POS["recoleccion_aprox"][1], smooth=True)   # Hombro baja
        servo_set(3, POS["recoleccion_aprox"][2], smooth=True)   # Codo
        servo_set(4, POS["recoleccion_aprox"][3], smooth=False)  # Pinza abierta
        sleep_ms(300) # esperar a que el brazo llegue a la posición de recolección antes de cerrar la pinza
 
        # ── PASO 3: Cerrar pinza para agarrar caja
        # Se lee POS["recoleccion_agarre"][3]: solo se mueve la pinza (índice 3).
        servo_set(4, POS["recoleccion_agarre"][3], smooth=False)
        sleep_ms(400)
        log("Caja recolectada", "INFO")
 
        # ── PASO 4: Tránsito seguro - levantar brazo con caja
        # Se lee POS["transito"] = [None, hombro, codo, pinza]: índices 1 y 2.
        servo_set(2, POS["transito"][1], smooth=True)   # Hombro arriba
        servo_set(3, POS["transito"][2], smooth=True)   # Codo arriba
 
        # ── PASO 5: Girar base hacia el pallet destino
        # Se lee POS["pallet{N}_transito"][0]: base del pallet correspondiente.
        t_key = "pallet{}_transito".format(dest_pallet) # clave para acceder a la posición de tránsito del pallet destino en el diccionario POS, por ejemplo "pallet2_transito" para el pallet 2
        servo_set(1, POS[t_key][0], smooth=True) # Base gira hacia el pallet destino mientras el brazo está levantado para evitar tumbar la caja
 
        # ── PASO 6: Bajar al nivel de apilado correcto
        if p_key not in POS:
            log("Posicion '{}' no definida en POS".format(p_key), "ERROR")
            arm_busy = False
            return False
        
        angles = POS[p_key] # ángulos objetivo para la posición de depósito en el pallet destino, por ejemplo [70, 20, 102, 46] para "pallet2_caja2"
        servo_set(1, angles[0], smooth=True)  # Acomodar base para compensar backlash
        servo_set(2, angles[1], smooth=True)  # Hombro al nivel
        servo_set(3, angles[2], smooth=True)  # Codo al nivel
        sleep_ms(300)
 
        # ── PASO 7: Abrir pinza para depositar
        # Se lee POS["recoleccion_aprox"][3]: pinza abierta (90°).
        servo_set(4, POS["recoleccion_aprox"][3], smooth=False)
        sleep_ms(400)
        log("Caja depositada en Pallet {} nivel {}".format(dest_pallet, level), "INFO")
 
        # ── PASO 8: Tránsito seguro al salir del pallet
        # Se lee POS["transito"] = [None, hombro, codo, pinza]: índices 1 y 2.
        servo_set(2, POS["transito"][1], smooth=True)
        servo_set(3, POS["transito"][2], smooth=True)
 
        # ── PASO 9: Volver a zona de recolección
        # Se lee POS["recoleccion_aprox"][0]: base a 180° (zona del sensor).
        servo_set(1, POS["recoleccion_aprox"][0], smooth=True)
 
        # ── Actualizar contadores
        pallet_count[dest_pallet] += 1
        if pallet_count[dest_pallet] >= MAX_CAJAS_PALLET: # si el número de cajas en el pallet alcanzó o superó el máximo definido, se marca el pallet como lleno para bloquear futuros depósitos hasta que se reciba un comando de vaciado.
            pallet_full[dest_pallet] = True # marcar pallet como lleno para bloquear futuros depósitos
            log("Pallet {} LLENO ({} cajas)".format(dest_pallet, MAX_CAJAS_PALLET), "WARNING")
            mqtt_publish({"event": "pallet_full", "pallet": dest_pallet}) # publicar evento de pallet lleno para que la GUI pueda actualizar el estado visual del pallet
 
        mqtt_publish({
            "event":  "box_collected", # evento que indica que una caja ha sido recolectada y depositada, útil para la GUI para actualizar el estado de los pallets y mostrar animaciones de apilado.
            "dest":   "P{}".format(dest_pallet), # destino del pallet en formato "P1" o "P2", para que la GUI pueda identificar a qué pallet se dirigió la caja.
            "level":  level, # nivel de apilado en el pallet (1, 2 o 3), que indica la altura a la que se depositó la caja, útil para la GUI para mostrar el estado de llenado del pallet.
            "count":  pallet_count[dest_pallet], # número de cajas en el pallet después de este depósito, útil para la GUI para mostrar el estado de llenado del pallet.
            "full":   pallet_full[dest_pallet], # estado de llenado del pallet después de este depósito (True si alcanzó el máximo), útil para la GUI para mostrar el estado de llenado del pallet.
        })
 
    except Exception as exc:
        log("ERROR en Pick&Place: {}".format(exc), "CRITICAL")
        mqtt_publish({"event": "error", "msg": str(exc)})
        arm_busy = False
        return False
 
    arm_busy = False
    return True
 
# =============================================================================
# WIFI
# =============================================================================
 
def connect_wifi():
    global wifi
    log("Conectando a WiFi '{}'...".format(WIFI["ssid"]), "INFO")
    try:
        wifi = network.WLAN(network.STA_IF) # crear interfaz WiFi en modo estación (STA)
        wifi.active(True) # activar la interfaz WiFi
        if wifi.isconnected(): 
            log("WiFi ya conectado: {}".format(wifi.ifconfig()[0]), "INFO")
            return True
        wifi.connect(WIFI["ssid"], WIFI["password"]) # iniciar conexión a la red WiFi con las credenciales configuradas
        timeout = WIFI["timeout_s"] * 2 # convertir timeout a número de iteraciones (500ms cada una)
        dots = 0 # contador para imprimir puntos de progreso cada 500ms mientras se espera la conexión
        while not wifi.isconnected() and timeout > 0: # esperar a que se establezca la conexión WiFi, verificando cada 500ms
            sleep_ms(500)
            timeout -= 1 # decrementar el contador de timeout
            dots += 1 # incrementar el contador de puntos para mostrar progreso visual en la consola mientras se espera la conexión
            if dots % 10 == 0: # cada 10 puntos (5 segundos),
                print() # imprimir un salto de línea para evitar que los puntos se acumulen en una sola línea y dificulten la lectura del log, creando una nueva línea cada 5 segundos de espera.
            print(".", end="") # imprimir un punto sin salto de línea para mostrar progreso visual en la consola mientras se espera la conexión WiFi. Cada punto representa 500ms de espera. Se imprime un punto cada 500ms hasta que se establece la conexión o se agota el timeout.
        print() # imprimir un salto de línea al finalizar la espera para que el siguiente log se imprima en una nueva línea limpia después de los puntos de progreso.
        if wifi.isconnected():
            cfg = wifi.ifconfig() # obtener la configuración de red actual, que incluye la dirección IP asignada, la máscara de subred, la puerta de enlace y el servidor DNS. Esto se utiliza para mostrar información útil en el log una vez que se ha establecido la conexión WiFi.
            log("WiFi OK | IP: {} | GW: {}".format(cfg[0], cfg[2]), "INFO") # imprimir un mensaje de log indicando que la conexión WiFi se ha establecido correctamente, mostrando la dirección IP asignada y la puerta de enlace para confirmar que el dispositivo está conectado a la red y tiene acceso a Internet.
            return True
        else:
            log("Timeout WiFi", "ERROR")
            return False
    except Exception as exc:
        log("Error WiFi: {}".format(exc), "CRITICAL")
        return False
 
def wifi_is_up():
    # Verifica si la conexión WiFi está activa y conectada.
    return wifi is not None and wifi.isconnected() 
 
# =============================================================================
# MQTT
# =============================================================================
 
def mqtt_publish(data):
    """Publica dict como JSON en robot/log. Falla silenciosamente."""
    global mqtt_ok # mqtt_ok se establece en False si ocurre un error al publicar, para evitar intentar publicar repetidamente cuando la conexión MQTT no está disponible. El loop principal puede intentar reconectar y restablecer mqtt_ok a True cuando la conexión se restablezca.
    if not mqtt_ok or client is None:
        return
    try:
        payload = json.dumps(data) # convertir el diccionario de datos a una cadena JSON para enviarlo como payload del mensaje MQTT. Esto permite enviar información estructurada y fácilmente interpretable por la GUI u otros clientes MQTT que se suscriban al topic de log.
        client.publish(MQTT["topic_log"], payload.encode()) # publicar el mensaje MQTT en el topic de log configurado, enviando el payload JSON codificado como bytes. Esto permite que la GUI u otros clientes MQTT reciban los eventos y datos del sistema para visualización, diagnóstico o control.
        log("-> {}".format(payload), "DEBUG")
    except Exception as exc:
        log("Error publicando: {}".format(exc), "WARNING")
        mqtt_ok = False
 
def publish_status():
    """Publica estado completo del sistema."""
    status = {
        "event":        "status", # tipo de mensaje para que la GUI lo identifique como un reporte de estado completo, útil para mostrar información detallada en la interfaz de usuario cuando se solicite.
        "mode":         mode, # modo actual del sistema (MANUAL, SEMI_AUTO, AUTOMATICO), útil para que la GUI muestre el modo de operación actual y ajuste su interfaz o funcionalidades según corresponda.
        "arm_busy":     arm_busy,
        "semi_pending": semi_pending, # True si hay una caja detectada esperando decisión del usuario en modo SEMI_AUTO, útil para que la GUI muestre un indicador visual de que hay una acción pendiente y permita al usuario tomar la decisión de destino o ignorar la caja.
        "pallets": {
            "1": {"count": pallet_count[1], "full": pallet_full[1]},
            "2": {"count": pallet_count[2], "full": pallet_full[2]},
        },
        "servos":       servo_angle.copy(), # ángulos actuales de los servos, útil para que la GUI muestre el estado de los motores.
        "sensor":       read_sensor(), # estado actual del sensor KY-032 (True si detecta caja, False si no), útil para que la GUI muestre un indicador visual de detección de cajas en la zona de recolección.
        "mem_free":     gc.mem_free(), # cantidad de memoria libre, útil para monitorear el uso de memoria del sistema.
        "loop_count":   _loop_count, # cantidad de iteraciones del bucle principal, útil para monitorear el rendimiento del sistema.
        "cmd_received": _cmd_received, # cantidad de comandos recibidos, útil para monitorear la actividad del sistema.
        "reconnects":   _reconnect_count, # cantidad de reconexiones WiFi/MQTT realizadas, útil para diagnosticar la estabilidad de la conexión.
        "wifi_rssi":    wifi.status("rssi") if wifi else 0, # intensidad de la señal WiFi (RSSI), útil para diagnosticar la calidad de la conexión inalámbrica.
        "reset_cause":  reset_cause(), # causa del último reset, útil para diagnosticar problemas de estabilidad o reinicios inesperados.
    }
    log("Status publicado", "INFO")
    mqtt_publish(status) # publicar el estado completo del sistema a través de MQTT para que la GUI u otros clientes puedan recibir y mostrar esta información detallada sobre el estado actual del robot, incluyendo modo, estado del brazo, pallets, servos, sensor, memoria y estadísticas de operación.
 
def on_message(topic, msg):
    """
    Callback MQTT: recibe comandos desde la GUI.
    DEBE ser rápida: delega secuencias al loop principal via flags.
 
    DEDUPLICACIÓN QoS 1:
    Con QoS 1, el broker puede reenviar el mismo mensaje si no recibió
    PUBACK a tiempo. Para evitar que un comando se ejecute dos veces
    (ej: semi_decision enviado al pallet dos veces), se verifica el campo
    opcional "msg_id" del payload. Si el msg_id coincide con el último
    procesado, el mensaje se descarta silenciosamente.
    Si la GUI no incluye msg_id (comandos sin riesgo como "status"),
    el mecanismo no aplica y el comando se procesa normalmente.
    """
    global _cmd_received, mode, arm_busy, semi_pending
    global pallet_full, pallet_count, _last_cmd_id
 
    _cmd_received += 1
    raw = msg.decode() # decodificar el mensaje MQTT recibido de bytes a cadena de texto para procesarlo como JSON. El payload del mensaje MQTT se espera que sea una cadena JSON que contiene el comando y sus parámetros, por lo que es necesario decodificarlo antes de intentar parsearlo.
    log("<- CMD: {}".format(raw), "INFO")
 
    try:
        data = json.loads(raw) # parsear la cadena JSON a un diccionario de Python para acceder a los campos del comando. Se espera que el JSON tenga al menos un campo "cmd" que indique el tipo de comando, y otros campos opcionales según el comando específico (ej: "mode" para set_mode, "id" y "angle" para servo, etc.). Si el JSON no es válido o no tiene el formato esperado, se captura la excepción y se registra un error en el log.
    except Exception:
        log("JSON invalido: {}".format(raw), "ERROR")
        return
 
    # ── DEDUPLICACIÓN: descartar duplicados QoS 1 ──────────────────────────
    # La GUI incluye msg_id en todos los comandos de acción crítica
    # (semi_decision, pallet_clear, move, set_mode).
    # Si el broker reenvía el mismo mensaje (mismo msg_id), se ignora.
    incoming_id = data.get("msg_id", None) # obtener el campo opcional "msg_id" del comando recibido para verificar si es un comando crítico que requiere deduplicación. Si el campo "msg_id" no está presente, se asume que el comando no es crítico y se procesa normalmente sin aplicar la deduplicación.
    if incoming_id is not None:
        if incoming_id == _last_cmd_id: # si el msg_id del comando entrante coincide con el último msg_id procesado, se considera un duplicado y se ignora para evitar ejecutar el mismo comando dos veces, lo cual podría causar problemas como apilar una caja dos veces en el mismo pallet o cambiar el modo repetidamente.
            log("CMD duplicado ignorado (msg_id={})".format(incoming_id), "WARNING")
            return
        _last_cmd_id = incoming_id # actualizar el último msg_id procesado para futuras comparaciones y detección de duplicados en comandos críticos.
    # ──────────────────────────────────────────────────────────────────────
 
    cmd = data.get("cmd", "") # obtener el campo "cmd" del comando recibido para determinar qué acción se debe realizar. Este campo es obligatorio para identificar el tipo de comando (ej: "set_mode", "servo", "move", "semi_decision", "pallet_clear", "status"). Si el campo "cmd" no está presente, se asigna una cadena vacía, lo que hará que el comando sea desconocido y se registre una advertencia en el log.
 
    # -- Cambio de modo --
    if cmd == "set_mode":
        new_mode = data.get("mode", "MANUAL").upper()
        if new_mode in ("MANUAL", "SEMI_AUTO", "AUTOMATICO"):
            mode = new_mode
            log("Modo cambiado a: {}".format(mode), "INFO")
            mqtt_publish({"event": "mode_changed", "mode": mode}) # publicar evento de cambio de modo para que la GUI pueda actualizar su interfaz y funcionalidades según el nuevo modo seleccionado por el usuario.
        else:
            log("Modo desconocido: {}".format(new_mode), "WARNING")
 
    # -- Control manual de servo individual --
    elif cmd == "servo":
        if arm_busy:
            log("Brazo ocupado, comando ignorado", "WARNING")
            return
        sid   = int(data.get("id",    1)) # obtener el campo "id" del comando recibido para identificar el servo a controlar. Si el campo "id" no está presente, se asigna un valor predeterminado de 1.
        angle = int(data.get("angle", 90)) # obtener el campo "angle" del comando recibido para establecer el ángulo del servo. Si el campo "angle" no está presente, se asigna un valor predeterminado de 90.
        if 1 <= sid <= 4:
            servo_set(sid, angle, smooth=True)
            mqtt_publish({"event": "servo_ack", "id": sid, "angle": angle}) # publicar evento de reconocimiento de comando de servo para que la GUI pueda confirmar que el servo ha recibido el comando y mostrar el nuevo ángulo en la interfaz.
        else:
            log("Servo id={} fuera de rango".format(sid), "ERROR")
 
    # -- Movimiento preconfigurado manual --
    elif cmd == "move":
        if arm_busy:
            log("Brazo ocupado, comando ignorado", "WARNING")
            return
        action = data.get("action", "home") # obtener el campo "action" del comando recibido para identificar la secuencia de movimiento preconfigurada a ejecutar. Si el campo "action" no está presente, se asigna un valor predeterminado de "home".
        move_sequence(action) # ejecutar la secuencia de movimiento correspondiente a la acción solicitada, por ejemplo "home", "recolectar", "abrir_pinza", "cerrar_pinza". La función move_sequence() se encargará de realizar los movimientos necesarios para cada acción predefinida.
 
    # -- Decisión semi-automática --
    elif cmd == "semi_decision":
        if not semi_pending:
            log("No hay caja pendiente para decision", "WARNING")
            return
        dest = data.get("dest", "ignorar") # obtener el campo "dest" del comando recibido para identificar el destino seleccionado por el usuario para la caja detectada en modo SEMI_AUTO. Si el campo "dest" no está presente, se asigna un valor predeterminado de "ignorar".
        semi_pending = False
        if dest == "P1":
            pick_and_place(1)
        elif dest == "P2":
            pick_and_place(2)
        else:
            log("Caja ignorada por el usuario", "INFO")
            mqtt_publish({"event": "box_ignored"})
 
    # -- Pallet vaciado por el usuario --
    elif cmd == "pallet_clear":
        pallet_id = int(data.get("pallet", 1))
        if pallet_id in (1, 2):
            pallet_count[pallet_id] = 0 # resetear el contador de cajas del pallet a 0 para reflejar que ha sido vaciado
            pallet_full[pallet_id]  = False # marcar el pallet como no lleno para permitir futuros depósitos
            log("Pallet {} vaciado por usuario".format(pallet_id), "INFO")
            mqtt_publish({"event": "pallet_cleared", "pallet": pallet_id}) # publicar evento de pallet vaciado para que la GUI pueda actualizar el estado visual del pallet y reflejar que ahora está vacío y listo para recibir cajas nuevamente.
        else:
            log("Pallet id={} invalido".format(pallet_id), "ERROR")
 
    # -- Solicitar estado --
    elif cmd == "status":
        publish_status()
 
    else:
        log("Comando desconocido: '{}'".format(cmd), "WARNING")
 
def connect_mqtt():
    """
    Conecta al broker MQTT. Retorna True si exitoso.
 
    CAMBIOS v4.0:
    - clean_session=False: el broker conserva la sesión y los mensajes
      no entregados entre desconexiones. Combinado con QoS 1 en la
      suscripción, garantiza que comandos enviados mientras el ESP32
      estaba offline sean entregados al reconectar.
    - Suscripción con qos=1: el broker confirma la entrega de cada
      mensaje con un paquete PUBACK. Si el ESP32 no responde (está en
      medio de una secuencia de movimiento o perdió la red), el broker
      reintenta el mensaje automáticamente.
    - Re-sincronización activa: publica 'online' y 500ms después 'status'
      completo, para que la GUI no tenga que esperar el heartbeat (20s)
      para sincronizar el estado de pallets y modo tras una reconexión.
 
    NOTA SOBRE PUBACK Y WDT:
    umqtt.simple en MicroPython no bloquea esperando el PUBACK al publicar
    (solo lo hace al recibir mensajes con qos=1 en la suscripción).
    La publicación con qos=0 (mqtt_publish) es fire-and-forget, por lo
    tanto no hay riesgo de congelamiento esperando PUBACK del broker.
    """
    global client, mqtt_ok, _reconnect_count
    if not wifi_is_up():
        log("WiFi caido - no se puede conectar a MQTT", "WARNING")
        return False
    log("Conectando MQTT {}:{}...".format(MQTT["broker"], MQTT["port"]), "INFO")
    try:
        if client is not None:
            try:
                client.disconnect() # desconectar el cliente MQTT existente si ya hay uno para liberar recursos y evitar conflictos al crear una nueva conexión. Esto es especialmente importante si se están realizando reconexiones frecuentes debido a inestabilidad de la red, para asegurarse de que no queden conexiones colgadas que puedan consumir memoria o causar errores.
            except Exception:
                pass
            client = None
        gc.collect() # recolectar basura antes de crear un nuevo cliente para liberar memoria y reducir riesgo de errores por falta de memoria al conectar MQTT.
        client = MQTTClient(
            MQTT["client_id"],
            MQTT["broker"],
            port      = MQTT["port"],
            keepalive = MQTT["keepalive"],
            user      = MQTT["user"],      # NUEVO — autenticación obligatoria
            password  = MQTT["password"],  # NUEVO — autenticación obligatoria
        )
        client.set_callback(on_message) # registrar la función de callback para manejar los mensajes entrantes del broker MQTT. Esta función se ejecutará cada vez que se reciba un mensaje en los topics a los que el cliente está suscrito, permitiendo procesar comandos desde la GUI u otros clientes MQTT.
 
        # clean_session=False: el broker conserva la cola de mensajes QoS 1
        # pendientes entre desconexiones del ESP32.
        client.connect(clean_session=False)
 
        # QoS 1 en suscripción a robot/cmd:
        # Garantiza que los comandos de la GUI lleguen AL MENOS UNA VEZ,
        # incluso si el ESP32 estaba offline cuando se enviaron.
        # La deduplicación via msg_id en on_message() neutraliza los
        # posibles duplicados inherentes al protocolo QoS 1.
        client.subscribe(MQTT["topic_cmd"], qos=1)
 
        mqtt_ok = True
        _reconnect_count += 1
        log("MQTT conectado (intento #{})".format(_reconnect_count), "INFO")
 
        # Re-sincronización activa: publicar 'online' inmediatamente
        mqtt_publish({
            "event":       "online",
            "reset_cause": reset_cause(),
            "mem_free":    gc.mem_free(),
            "reconnects":  _reconnect_count,
            "mode":        mode,
        })
 
        # Publicar estado completo 500ms después para dar tiempo a la GUI
        # a procesar 'online' y estar lista para recibir 'status'.
        # Esto evita que la GUI tenga que esperar hasta 20s (heartbeat)
        # para sincronizar pallets, servos y modo tras una reconexión.
        sleep_ms(500)
        publish_status()
 
        return True
    except Exception as exc:
        mqtt_ok = False
        client  = None
        log("Error MQTT: {}".format(exc), "ERROR")
        return False
 
def safe_poll():
    """check_msg() seguro. Retorna True si OK."""
    global mqtt_ok
    if not mqtt_ok or client is None:
        return False
    try:
        client.check_msg() # procesar mensajes entrantes sin bloquear. Si no hay mensajes, retorna inmediatamente. Si hay un mensaje, ejecuta el callback registrado (on_message) para procesarlo. Si ocurre un error de red o desconexión, se captura la excepción y se marca mqtt_ok como False para intentar reconectar en el loop principal.
        return True 
    except OSError as exc:
        mqtt_ok = False
        log("MQTT desconectado (OSError {}): {}".format(exc.args[0], exc), "WARNING")
        return False
    except Exception as exc:
        mqtt_ok = False
        log("MQTT error: {}".format(exc), "ERROR")
        return False
 
# =============================================================================
# LÓGICA DE SENSOR Y MODOS AUTOMÁTICOS
# =============================================================================
 
def process_sensor():
    """
    Gestiona la detección del sensor con debounce y ejecuta la
    lógica correspondiente al modo activo.
    Debe llamarse periódicamente desde el loop principal.
    """
    global sensor_consecutive, semi_pending
 
    if arm_busy:
        return   # No leer sensor mientras el brazo está en movimiento
 
    detected = read_sensor()
 
    if detected:
        sensor_consecutive += 1
    else:
        sensor_consecutive = 0
 
    # Confirmar detección solo con N muestras consecutivas (debounce)
    if sensor_consecutive < TIMING["sensor_debounce"]:
        return
 
    # Caja confirmada — resetear contador para no disparar varias veces
    sensor_consecutive = 0
 
    log("Sensor: caja detectada (confirmado)", "INFO")
    mqtt_publish({"event": "sensor", "detected": True})
 
    if mode == "SEMI_AUTO":
        if not semi_pending:
            semi_pending = True
            log("Modo SEMI_AUTO: esperando decision del usuario", "INFO")
            mqtt_publish({"event": "box_detected"})
 
    elif mode == "AUTOMATICO":
        # Decidir destino automáticamente: llenar P1 antes que P2
        dest = None
        if not pallet_full[1]:
            dest = 1
        elif not pallet_full[2]:
            dest = 2
        else:
            log("Ambos pallets llenos - proceso detenido hasta vaciado", "WARNING")
            mqtt_publish({"event": "all_pallets_full"})
            return
 
        log("Modo AUTO: pick & place automatico -> Pallet {}".format(dest), "INFO")
        pick_and_place(dest)
 
# =============================================================================
# LOOP PRINCIPAL
# =============================================================================
 
def main():
    global _loop_count, _t_mqtt_poll, _t_heartbeat, _t_gc, _t_sensor
 
    print_boot_info()
 
    # -- Hardware
    if not init_servos():
        log("Fallo critico en servos", "CRITICAL")
        return
    if not init_sensor():
        log("Fallo critico en sensor", "CRITICAL")
        return
 
    # -- Conectividad
    if not connect_wifi():
        log("Sin WiFi. Deteniendo.", "CRITICAL")
        return
    connect_mqtt()
 
    # -- WDT (solo producción)
    if WDT_ENABLED:
        wdt = WDT(timeout=TIMING["wdt_timeout_ms"])
        log("WDT activado ({} ms)".format(TIMING["wdt_timeout_ms"]), "INFO")
    else:
        wdt = None
        log("WDT desactivado (modo desarrollo)", "WARNING")
 
    log_sep("SISTEMA OPERATIVO - MODO {}".format(mode))
 
    now = ticks_ms()
    _t_mqtt_poll = now
    _t_heartbeat = now
    _t_gc        = now
    _t_sensor    = now
 
    try:
        while True:
            now = ticks_ms()
            _loop_count += 1
 
            # 1. Alimentar WDT
            if wdt is not None:
                wdt.feed()
 
            # 2. Poll MQTT
            if ticks_diff(now, _t_mqtt_poll) >= TIMING["mqtt_poll_ms"]: # verificar si es momento de hacer poll a MQTT para procesar mensajes entrantes. Esto se hace cada mqtt_poll_ms milisegundos para asegurar que los comandos de la GUI se procesen con una latencia razonable sin bloquear el loop principal.
                if mqtt_ok:
                    if not safe_poll(): # si safe_poll retorna False, significa que hubo un error al procesar MQTT (ej: desconexión), por lo que se intenta reconectar.
                        log("Reconectando MQTT...", "WARNING")
                        connect_mqtt()
                else:
                    if wifi_is_up(): # si WiFi está activo pero mqtt_ok es False, intentar reconectar MQTT. Si WiFi no está activo, intentar reconectar WiFi primero y luego MQTT.
                        connect_mqtt()
                    else:
                        log("WiFi perdido - reconectando...", "WARNING")
                        connect_wifi() # intentar reconectar WiFi. El loop intentará conectar MQTT en la siguiente iteración una vez que WiFi esté activo.
                        connect_mqtt() # intentar conectar MQTT inmediatamente después de reconectar WiFi para restablecer la conexión lo antes posible. Si WiFi se reconectó exitosamente, este intento de conexión MQTT debería tener éxito. Si WiFi aún no está activo, connect_mqtt() manejará el error y mqtt_ok seguirá siendo False, lo que hará que el loop siga intentando reconectar en las siguientes iteraciones.
                _t_mqtt_poll = now # actualizar el timestamp del último poll MQTT para programar el próximo poll después de mqtt_poll_ms milisegundos.
 
            # 3. Lectura de sensor (no ejecutar si brazo ocupado)
            if ticks_diff(now, _t_sensor) >= TIMING["sensor_poll_ms"]:
                process_sensor()
                _t_sensor = now
 
            # 4. Heartbeat / telemetría
            if ticks_diff(now, _t_heartbeat) >= TIMING["heartbeat_ms"]: # cada heartbeat_ms milisegundos, publicar un mensaje de latido (heartbeat) que incluye información de telemetría como el número de iteraciones del loop, memoria libre, estado de MQTT y modo actual. Esto permite monitorear el estado del sistema en tiempo real a través de la GUI u otros clientes MQTT que se suscriban al topic de log.
                log("Heartbeat | loop={} | mem={} B | mqtt={} | modo={}".format(
                    _loop_count, gc.mem_free(),
                    "OK" if mqtt_ok else "DISC", mode), "INFO")
                if mqtt_ok:
                    publish_status()
                _t_heartbeat = now
 
            # 5. Garbage collector
            if ticks_diff(now, _t_gc) >= TIMING["gc_ms"]: # cada gc_ms milisegundos, ejecutar el recolector de basura para liberar memoria no utilizada y evitar errores por falta de memoria. Se registra la cantidad de memoria libre antes y después de la recolección para monitorear el uso de memoria del sistema.
                before = gc.mem_free() # obtener la cantidad de memoria libre antes de ejecutar el recolector de basura para monitorear cuánto espacio se libera con cada ciclo de recolección.
                gc.collect() # ejecutar el recolector de basura para liberar memoria no utilizada. Esto es especialmente importante en un sistema con recursos limitados como un microcontrolador, donde la acumulación de objetos no referenciados puede llevar rápidamente a quedarse sin memoria disponible.
                after  = gc.mem_free() # obtener la cantidad de memoria libre después de ejecutar el recolector de basura para comparar con el valor antes de la recolección y monitorear el uso de memoria del sistema a lo largo del tiempo.
                log("GC: {} -> {} bytes".format(before, after), "DEBUG")
                _t_gc = now # actualizar el timestamp del último ciclo de recolección de basura para programar el próximo ciclo después de gc_ms milisegundos.
 
            # 6. Ceder ciclos al RTOS (ahorro de energía, no bloquea WDT)
            idle() # ceder el control al sistema operativo en tiempo real (RTOS) para permitir que otras tareas se ejecuten y para ahorrar energía. Esto es especialmente importante en un microcontrolador para evitar bloquear el loop principal y permitir que el sistema responda a eventos como interrupciones, WDT, o tareas del sistema operativo.
 
    except KeyboardInterrupt:
        log("Interrupcion por teclado (Ctrl+C)", "WARNING")
 
    except Exception as exc:
        log("ERROR NO MANEJADO: {}".format(exc), "CRITICAL")
        sys.print_exception(exc)
 
    finally: # limpieza y apagado seguro del sistema
        log("Apagando sistema...", "INFO")
        # Llevar brazo a HOME antes de apagar
        try:
            move_transito() # levantar brazo para tránsito seguro
            servo_set(1, 90, smooth=True) # Base a 90° (HOME)
        except Exception:
            pass
        # Desactivar PWM de todos los servos
        for sid, pwm in servo_pwm.items():
            try:
                pwm.duty(0)
                pwm.deinit()
            except Exception:
                pass
        # Publicar offline
        if client is not None:
            try:
                mqtt_publish({"event": "offline"})
                client.disconnect()
            except Exception:
                pass
        log("Sistema detenido.", "INFO")
 
# =============================================================================
# ENTRY POINT
# =============================================================================
"""
    El bloque de código que se ejecuta cuando se ejecuta el script. Se llama a la función main() dentro de un bloque try-except 
    para capturar cualquier excepción no manejada que pueda ocurrir durante la ejecución del programa. Si ocurre una excepción, 
    se registra un mensaje de error crítico en el log y se imprime la traza completa de la excepción para facilitar el diagnóstico del problema. 
    Esto ayuda a asegurar que cualquier error inesperado sea registrado adecuadamente y no cause un fallo silencioso del sistema.
"""
if __name__ == "__main__":
    try:
        main()
    except Exception as fatal:
        log_sep("ERROR FATAL")
        sys.print_exception(fatal)
        log_sep()