"""
================================================================================
servos.py — Control de servomotores SG90 y secuencias de movimiento
================================================================================
Autor: Francisco Bevilacqua | Versión: 5.0

Responsabilidad única (SRP):
    Todo lo relacionado con MOVER el brazo físico: conversión ángulo→duty
    cycle, movimiento suave paso a paso, posiciones predefinidas (config.POS)
    y las secuencias de alto nivel (home, recolectar, pick_and_place).

    Este módulo SÍ importa mqtt.py (a diferencia de sensor.py) porque las
    secuencias de movimiento publican eventos de progreso hacia la GUI en
    tiempo real (move_start, move_done, pick_start, box_collected, etc.) —
    es información que la GUI necesita mientras el movimiento está
    ocurriendo, no después. No hay riesgo de ciclo: mqtt.py no importa
    servos.py.

Dependencias:
    machine (Pin, PWM) → control de hardware PWM
    config  → PINS, PWM_FREQ/MIN/MAX_DUTY, POS, TIMING, MAX_CAJAS_PALLET
    state   → state.servo_angle, state.arm_busy, state.pallet_count/full
    mqtt    → mqtt_publish() para eventos de progreso
    state.log
================================================================================
"""

from machine import Pin, PWM
from time import sleep_ms

from config import PINS, PWM_FREQ, PWM_MIN_DUTY, PWM_MAX_DUTY, POS, TIMING, MAX_CAJAS_PALLET
from state import state, log
from mqtt import mqtt_publish

# Instancias de PWM por servo: {1: PWM, 2: PWM, 3: PWM, 4: PWM}.
# Vive como variable de módulo (no en state.py) porque es un HANDLE de
# hardware — igual criterio que _sensor_pin en sensor.py. Lo que SÍ es
# estado de negocio compartido (el ÁNGULO actual, consultado por
# commands.publish_status) vive en state.servo_angle.
servo_pwm = {}

SERVO_NAMES = {1: "Base", 2: "Hombro", 3: "Codo", 4: "Pinza"}


# ============================================================================
# CONVERSIÓN Y CONTROL DE BAJO NIVEL
# ============================================================================

def angle_to_duty(angle):
    """
    Convierte un ángulo (0-180°) a duty cycle PWM 10-bit para SG90 a 50Hz,
    mediante mapeo lineal entre PWM_MIN_DUTY y PWM_MAX_DUTY.

    Args:
        angle (int | float): ángulo deseado, se recorta a [0, 180].

    Returns:
        int: duty cycle correspondiente.
    """
    angle = max(0, min(180, angle))
    return int(PWM_MIN_DUTY + (angle / 180.0) * (PWM_MAX_DUTY - PWM_MIN_DUTY))


def servo_set(servo_id, angle, smooth=False):
    """
    Mueve un servo al ángulo indicado, actualizando state.servo_angle.

    Args:
        servo_id (int): 1=Base, 2=Hombro, 3=Codo, 4=Pinza.
        angle (int): ángulo objetivo, se recorta a [0, 180].
        smooth (bool): si True, se mueve paso a paso (TIMING["servo_step_deg"]
            por paso, cada TIMING["servo_step_ms"]) para evitar tirones
            mecánicos que podrían desalinear la pinza o tumbar una caja ya
            sostenida. Si False, el servo salta directo al ángulo (usado
            para la pinza, donde el "tirón" es deseable/rápido).

    Returns:
        bool: True si el servo existe y se movió, False si servo_id no
            está inicializado en servo_pwm (error de configuración).
    """
    if servo_id not in servo_pwm:
        log("Servo {} no inicializado".format(servo_id), "ERROR")
        return False

    angle = max(0, min(180, int(angle)))
    current = state.servo_angle[servo_id]

    if smooth and abs(angle - current) > TIMING["servo_step_deg"]:
        step = TIMING["servo_step_deg"] if angle > current else -TIMING["servo_step_deg"]
        pos = current
        while (step > 0 and pos < angle) or (step < 0 and pos > angle):
            pos += step
            pos = max(0, min(180, pos))
            servo_pwm[servo_id].duty(angle_to_duty(pos))
            sleep_ms(TIMING["servo_step_ms"])

    servo_pwm[servo_id].duty(angle_to_duty(angle))  # asegurar llegada exacta
    state.servo_angle[servo_id] = angle
    log("Servo {} -> {}°".format(servo_id, angle), "DEBUG")
    return True


def servo_idle(servo_id):
    """
    Desactiva el PWM del servo (duty=0) para ahorrar energía y evitar
    vibración/zumbido cuando no necesita mantener posición activamente.
    NOTA: usar solo cuando el brazo está en reposo y sin carga sostenida
    (con carga, duty=0 dejaría caer la caja).
    """
    if servo_id in servo_pwm:
        servo_pwm[servo_id].duty(0)


def init_servos():
    """
    Inicializa los 4 servos PWM en sus GPIOs y los lleva a posición HOME.

    Returns:
        bool: True si los 4 servos se inicializaron y llegaron a HOME
            sin excepciones.
    """
    log("Inicializando servos...", "INFO")
    pin_ids = [
        (1, PINS["servo_base"]),
        (2, PINS["servo_hombro"]),
        (3, PINS["servo_codo"]),
        (4, PINS["servo_pinza"]),
    ]
    try:
        for sid, gpio in pin_ids:
            pwm = PWM(Pin(gpio), freq=PWM_FREQ)
            pwm.duty(angle_to_duty(90))  # posición neutral de arranque
            servo_pwm[sid] = pwm
            log("  Servo {} ({}) -> GPIO {} OK".format(sid, SERVO_NAMES[sid], gpio), "DEBUG")

        sleep_ms(500)
        log("Moviendo a HOME...", "INFO")
        move_sequence("home")
        log("Servos inicializados en HOME", "INFO")
        return True
    except Exception as exc:
        log("Error inicializando servos: {}".format(exc), "CRITICAL")
        return False


# ============================================================================
# SECUENCIAS DE MOVIMIENTO
# ============================================================================

def execute_pos(pos_key, smooth=True):
    """
    Ejecuta una posición predefinida de config.POS.
    Formato de cada entrada: [base, hombro, codo, pinza], donde None
    significa "no mover ese servo en este paso".

    Args:
        pos_key (str): clave dentro de config.POS.
        smooth (bool): ver servo_set().

    Returns:
        bool: True si la posición existe y se ejecutó.
    """
    if pos_key not in POS:
        log("Posición '{}' no existe en POS".format(pos_key), "ERROR")
        return False
    angles = POS[pos_key]
    for sid, angle in zip([1, 2, 3, 4], angles):
        if angle is not None:
            servo_set(sid, angle, smooth=smooth)
    return True


def move_transito():
    """
    Movimiento de tránsito seguro: levanta hombro y codo ANTES de girar
    la base. Previene que el brazo tumbe cajas al moverse entre zonas
    (recolección ↔ pallets).
    """
    log("Transito seguro: levantando hombro y codo", "INFO")
    servo_set(2, 90, smooth=True)   # Hombro arriba
    servo_set(3, 90, smooth=True)   # Codo arriba
    servo_set(4, 90, smooth=False)  # Pinza abierta durante tránsito


def move_sequence(action):
    """
    Ejecuta una secuencia de movimiento completa con nombre descriptivo.
    Usada por el modo MANUAL para movimientos preconfigurados (home,
    recolectar, abrir_pinza, cerrar_pinza).

    Publica 'move_start'/'move_done' por MQTT para que la GUI pueda
    mostrar estado de progreso. Usa state.arm_busy como guard de
    exclusión mutua: mientras la secuencia corre, otros comandos
    servo/move deben ser rechazados por el caller (ver commands.py).

    Args:
        action (str): "home" | "recolectar" | "abrir_pinza" | "cerrar_pinza".
    """
    state.arm_busy = True
    log("Iniciando movimiento: {}".format(action), "INFO")
    mqtt_publish({"event": "move_start", "action": action})

    try:
        if action == "home":
            servo_set(2, 90, smooth=True)
            servo_set(3, 90, smooth=True)
            servo_set(4, 90, smooth=False)
            servo_set(1, 90, smooth=True)

        elif action == "recolectar":
            move_transito()
            servo_set(1, 180, smooth=True)  # Base a zona de recolección
            servo_set(2, 15, smooth=True)   # Hombro baja
            servo_set(3, 90, smooth=True)   # Codo
            servo_set(4, 90, smooth=False)  # Pinza abierta

        elif action == "abrir_pinza":
            servo_set(4, 90, smooth=False)

        elif action == "cerrar_pinza":
            servo_set(4, 0, smooth=False)

        else:
            log("Acción '{}' no reconocida".format(action), "WARNING")
            state.arm_busy = False
            return

    except Exception as exc:
        log("Error en secuencia '{}': {}".format(action, exc), "ERROR")

    log("Movimiento completado: {}".format(action), "INFO")
    mqtt_publish({"event": "move_done", "action": action})
    state.arm_busy = False


def pick_and_place(dest_pallet):
    """
    Secuencia completa de Pick & Place (modo SEMI_AUTO / AUTOMATICO):
      1. Tránsito seguro          6. Bajar al nivel de apilado correcto
      2. Ir a zona de recolección  7. Abrir pinza (depositar)
      3. Cerrar pinza (agarrar)    8. Tránsito seguro (salir del pallet)
      4. Tránsito (levantar)       9. Volver a zona de recolección
      5. Girar a pallet destino

    Actualiza state.pallet_count / state.pallet_full y publica eventos
    de progreso (pick_start, box_collected, pallet_full, error) por MQTT.

    Args:
        dest_pallet (int): 1 o 2.

    Returns:
        bool: True si depositó correctamente, False si el pallet ya
            estaba lleno o si ocurrió un error durante la secuencia.
    """
    if state.pallet_full[dest_pallet]:
        log("Pallet {} lleno - no se puede depositar".format(dest_pallet), "WARNING")
        mqtt_publish({"event": "pallet_full", "pallet": dest_pallet})
        return False

    state.arm_busy = True
    level = state.pallet_count[dest_pallet] + 1  # nivel donde se depositará (1, 2 o 3)
    p_key = "pallet{}_caja{}".format(dest_pallet, level)

    log("Pick&Place -> Pallet {} nivel {}".format(dest_pallet, level), "INFO")
    mqtt_publish({"event": "pick_start", "dest": "P{}".format(dest_pallet), "level": level})

    try:
        # PASO 1: tránsito seguro antes de ir a recolección.
        move_transito()
        sleep_ms(1000)  # margen para que el operador termine de ubicar la caja

        # PASO 2: ir a zona de recolección con pinza abierta.
        servo_set(1, POS["recoleccion_aprox"][0], smooth=True)
        servo_set(2, POS["recoleccion_aprox"][1], smooth=True)
        servo_set(3, POS["recoleccion_aprox"][2], smooth=True)
        servo_set(4, POS["recoleccion_aprox"][3], smooth=False)
        sleep_ms(300)

        # PASO 3: cerrar pinza para agarrar la caja.
        servo_set(4, POS["recoleccion_agarre"][3], smooth=False)
        sleep_ms(400)
        log("Caja recolectada", "INFO")

        # PASO 4: tránsito seguro — levantar brazo con la caja sostenida.
        servo_set(2, POS["transito"][1], smooth=True)
        servo_set(3, POS["transito"][2], smooth=True)

        # PASO 5: girar base hacia el pallet destino.
        t_key = "pallet{}_transito".format(dest_pallet)
        servo_set(1, POS[t_key][0], smooth=True)

        # PASO 6: bajar al nivel de apilado correcto.
        if p_key not in POS:
            log("Posicion '{}' no definida en POS".format(p_key), "ERROR")
            state.arm_busy = False
            return False

        angles = POS[p_key]
        servo_set(1, angles[0], smooth=True)  # ajuste fino de base (compensa backlash)
        servo_set(2, angles[1], smooth=True)
        servo_set(3, angles[2], smooth=True)
        sleep_ms(300)

        # PASO 7: abrir pinza para depositar.
        servo_set(4, POS["recoleccion_aprox"][3], smooth=False)
        sleep_ms(400)
        log("Caja depositada en Pallet {} nivel {}".format(dest_pallet, level), "INFO")

        # PASO 8: tránsito seguro al salir del pallet.
        servo_set(2, POS["transito"][1], smooth=True)
        servo_set(3, POS["transito"][2], smooth=True)

        # PASO 9: volver a zona de recolección.
        servo_set(1, POS["recoleccion_aprox"][0], smooth=True)

        # Actualizar contadores de negocio.
        state.pallet_count[dest_pallet] += 1
        if state.pallet_count[dest_pallet] >= MAX_CAJAS_PALLET:
            state.pallet_full[dest_pallet] = True
            log("Pallet {} LLENO ({} cajas)".format(dest_pallet, MAX_CAJAS_PALLET), "WARNING")
            mqtt_publish({"event": "pallet_full", "pallet": dest_pallet})

        mqtt_publish({
            "event": "box_collected",
            "dest": "P{}".format(dest_pallet),
            "level": level,
            "count": state.pallet_count[dest_pallet],
            "full": state.pallet_full[dest_pallet],
        })

    except Exception as exc:
        log("ERROR en Pick&Place: {}".format(exc), "CRITICAL")
        mqtt_publish({"event": "error", "msg": str(exc)})
        state.arm_busy = False
        return False

    state.arm_busy = False
    return True
