"""
================================================================================
state.py — Estado global del sistema (singleton) y utilidades de logging
================================================================================
Autor: Francisco Bevilacqua | Versión: 5.0

Responsabilidad única (SRP):
    Este módulo es el ÚNICO lugar donde vive el estado MUTABLE compartido
    entre módulos (modo de operación, contadores de pallets, ángulos de
    servos, flags de conexión, etc.). Todo módulo que necesite leer o
    modificar estado del sistema lo hace a través del objeto `state`
    definido acá — nunca declarando sus propias variables globales
    paralelas que puedan desincronizarse.

POR QUÉ UNA CLASE Y NO VARIABLES GLOBALES SUELTAS (decisión de diseño clave):
    En el robot_main.py original monolítico, todo el estado eran variables
    globales del módulo (`global mode`, `global arm_busy`, etc.) dentro de
    UN SOLO archivo — funcionaba porque todo vivía en el mismo namespace.

    Al modularizar en varios archivos, ese patrón se rompe: en Python (y
    MicroPython) hacer `from state import mode` y después `mode = "AUTO"`
    en otro módulo NO modifica el `mode` original — crea una variable local
    nueva que "sombrea" el import, porque los nombres inmutables (int, str,
    bool) se REBINDEAN, no se mutan. Este es un error clásico y difícil de
    detectar al modularizar un sistema con estado compartido.

    La solución estándar (y la que se aplica acá) es encapsular el estado
    en los ATRIBUTOS de una instancia única (patrón Singleton de facto,
    igual que `settings` en config.py del backend). Mutar un ATRIBUTO de un objeto
    (`state.mode = "AUTO"`) SÍ es visible desde cualquier módulo que haya
    importado esa misma instancia, porque todos apuntan al mismo objeto en
    memoria. Los diccionarios (pallet_count, servo_angle) ya eran mutables
    en el original y se preservan igual acá dentro de la clase.

Dependencias: ninguna (módulo hoja, igual que config.py).
    state.py NO importa config.py a propósito: mantenerlo sin dependencias
    evita cualquier posibilidad de import circular, sin importar cómo
    evolucionen los demás módulos en el futuro.
================================================================================
"""

from time import ticks_ms


class SystemState:
    """
    Contenedor único de todo el estado mutable en tiempo de ejecución del
    firmware. Se instancia UNA sola vez al final de este archivo (`state`)
    y esa misma instancia se importa desde todos los demás módulos.

    Atributos — Conectividad:
        client (MQTTClient | None): instancia del cliente MQTT activo.
            None si todavía no se conectó o si se perdió la conexión.
            Asignado por mqtt.connect_mqtt().
        wifi (network.WLAN | None): interfaz WiFi en modo estación.
            Asignado por wifi.connect_wifi().
        mqtt_ok (bool): True si la última operación MQTT fue exitosa.
            Usado como guard en mqtt.mqtt_publish() y mqtt.safe_poll()
            para evitar operar sobre una conexión caída.

    Atributos — Lógica de negocio (máquina de estados del brazo):
        mode (str): "MANUAL" | "SEMI_AUTO" | "AUTOMATICO". Determina qué
            rama de decisión se ejecuta en commands.process_sensor_event()
            cuando el sensor KY-032 confirma una detección.
        arm_busy (bool): True mientras servos.move_sequence() o
            servos.pick_and_place() están ejecutando una secuencia. Actúa
            como guard de exclusión mutua: mientras es True, se ignoran
            nuevos comandos "servo"/"move" (ver commands.py) y no se lee
            el sensor (ver main.py) — evita movimientos superpuestos que
            podrían dañar los servos o tirar cajas ya sostenidas.
        semi_pending (bool): True si hay una caja detectada en modo
            SEMI_AUTO esperando que el operador elija destino desde la GUI.
        pallet_count (dict[int, int]): cajas depositadas por pallet {1: N, 2: N}.
        pallet_full (dict[int, bool]): True si ese pallet alcanzó
            config.MAX_CAJAS_PALLET y bloquea nuevos depósitos hasta que
            la GUI envíe "pallet_clear".

    Atributos — Hardware (posición conocida por software):
        servo_angle (dict[int, int]): último ángulo comandado a cada servo
            {1: Base, 2: Hombro, 3: Codo, 4: Pinza}. Es la fuente de verdad
            que consulta commands.publish_status() para informar posición
            actual a la GUI — el hardware en sí no reporta su ángulo, así
            que este diccionario ES esa memoria.

    Atributos — Contadores de diagnóstico (telemetría, ver publish_status):
        loop_count (int): iteraciones del loop principal desde el arranque.
        cmd_received (int): comandos MQTT recibidos (incluye duplicados QoS 1
            antes de deduplicar — ver last_cmd_id).
        reconnect_count (int): reconexiones MQTT realizadas.
        last_cmd_id (str | None): msg_id del último comando crítico procesado,
            usado por commands.on_message() para descartar duplicados que el
            broker reenvía cuando no recibió PUBACK a tiempo (semántica QoS 1
            "al menos una vez", no "exactamente una vez").

    Atributos — Timestamps del loop principal (ticks_ms):
        t_mqtt_poll, t_heartbeat, t_gc, t_sensor (int): última vez que se
            ejecutó cada tarea periódica. Comparados con ticks_diff() en
            main.py para decidir si ya corresponde ejecutar la tarea de
            nuevo, sin bloquear el loop con sleep().
    """

    def __init__(self):
        # -- Conectividad --
        self.client = None
        self.wifi = None
        self.mqtt_ok = False

        # -- Lógica de negocio --
        self.mode = "MANUAL"
        self.arm_busy = False
        self.semi_pending = False
        self.pallet_count = {1: 0, 2: 0}
        self.pallet_full = {1: False, 2: False}

        # -- Hardware: posición conocida por software --
        self.servo_angle = {1: 90, 2: 90, 3: 90, 4: 90}

        # -- Contadores de diagnóstico --
        self.loop_count = 0
        self.cmd_received = 0
        self.reconnect_count = 0
        self.last_cmd_id = None

        # -- Timestamps de tareas periódicas (se inicializan en main.py
        #    justo antes de entrar al loop, con el ticks_ms() real de ese
        #    momento; 0 acá es solo un placeholder seguro) --
        self.t_mqtt_poll = 0
        self.t_heartbeat = 0
        self.t_gc = 0
        self.t_sensor = 0


# Instancia única reutilizada por todo el firmware (Singleton de facto).
# Todos los módulos hacen `from state import state` y acceden/mutan sus
# atributos — nunca crean una SystemState() propia.
state = SystemState()


# ============================================================================
# UTILIDADES DE LOGGING
# ============================================================================
# [DECISIÓN DE DISEÑO] log() y log_sep() no tienen un módulo "natural" propio
# dentro de la lista de 8 archivos solicitada (no hay un logging.py dedicado).
# Se ubican acá porque:
#   1. No dependen de nada (igual que el resto de este archivo).
#   2. Se usan desde TODOS los demás módulos (wifi, mqtt, servos, sensor,
#      commands, main) — vivir en state.py evita que cualquiera de esos
#      módulos dependa de otro módulo de negocio solo para poder loguear.
#   3. Conceptualmente, cada línea de log documenta un CAMBIO DE ESTADO del
#      sistema — mantenerlas junto a `state` es coherente, no arbitrario.
# Si el proyecto creciera y ameritara logging estructurado (ej. persistencia
# a archivo, niveles configurables en runtime), esto se separaría a su
# propio logger.py — documentado acá como evolución futura razonable.
# ============================================================================

def log(msg, level="INFO"):
    """
    Log formateado para consola Thonny: [timestamp_ms] [LEVEL] mensaje.

    Args:
        msg (str): mensaje a registrar.
        level (str): severidad — "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL".
    """
    print("[{:010d}] [{:<8s}] {}".format(ticks_ms(), level, msg))


def log_sep(title=""):
    """
    Imprime una línea separadora de 70 '=', opcionalmente con un título
    centrado. Usado para delimitar secciones en el log de arranque
    (ver main.print_boot_info) y para resaltar errores fatales.

    Args:
        title (str): texto a centrar sobre la línea separadora. Si es
            cadena vacía, imprime solo la línea sin encabezado.
    """
    line = "=" * 70
    if title:
        print(line)
        pad = (70 - len(title)) // 2
        print(" " * pad + title)
    print(line)
