# --- LIBRERÍAS ---
# Importamos las clases Pin (para definir pines) y PWM (para Modulación por Ancho de Pulso, usado en servos)
from machine import Pin, PWM
# Importamos el cliente MQTT simple de la biblioteca umqtt
from umqtt.simple import MQTTClient
# Importamos 'network' para la gestión de WiFi y 'time' para pausas
import network, time

# ===== CONFIGURACIÓN GLOBAL =====
# Define las credenciales de tu red WiFi
WIFI_SSID = "Red WIFI"
WIFI_PASS = "Contraseña WIFI"

# Define la configuración de MQTT
BROKER = "test.mosquitto.org"   # Dirección del servidor MQTT público
TOPIC_SUB = b"brazo/control"     # Tema al que nos suscribimos (para recibir órdenes). 'b' lo marca como bytes.
TOPIC_PUB = b"brazo/estado"      # Tema en el que publicamos (para enviar confirmaciones).

# ===== CONECTAR WIFI =====
# Inicializa la interfaz WiFi en modo Estación (STA_IF), es decir, como cliente que se conecta a un router.
wlan = network.WLAN(network.STA_IF)
wlan.active(True)  # Activa la interfaz WiFi

print("Intentando conectar a:", WIFI_SSID)
wlan.connect(WIFI_SSID, WIFI_PASS)  # Inicia la conexión a la red WiFi

# Bucle de espera: se detiene aquí hasta que la conexión WiFi sea exitosa
while not wlan.isconnected():
    print("Conectando WiFi...")
    time.sleep(1)  # Espera 1 segundo antes de volver a comprobar

# Una vez conectado, imprime la configuración de red (incluyendo la IP)
print("WiFi conectado:", wlan.ifconfig())

# ===== CONFIGURAR SERVOS =====
# Lista que almacena los objetos PWM para cada servo.
servos = [
    # Servo 1 en Pin 15
    PWM(Pin(15), freq=50),
    # Servo 2 en Pin 12
    PWM(Pin(12), freq=50),
    # Servo 3 en Pin 14
    PWM(Pin(14), freq=50),
    # Servo 4 en Pin 27
    PWM(Pin(27), freq=50)
]
# freq=50: Establece la frecuencia de la señal PWM a 50 Hz (un ciclo de 20ms).
# Esta es la frecuencia estándar que esperan la mayoría de los servomotores.

# Lista para almacenar la última posición conocida de cada servo
posiciones = [90, 90, 90, 90]  # Asumimos que todos empiezan a 90 grados

def set_servo(servo_id, angulo):
    """
    Mueve un servo específico a un ángulo determinado.
    :param servo_id: Índice del servo en la lista 'servos' (0 a 3).
    :param angulo: Ángulo deseado (normalmente entre 0 y 180).
    """
    
    # --- Cálculo del Ancho de Pulso ---
    # Los servos se controlan por la *duración* del pulso (ancho de pulso), no por el porcentaje (duty cycle).
    # Estos valores son estándar: ~500μs (microsegundos) para 0° y ~2500μs para 180°.
    min_us = 500
    max_us = 2500

    # 1. Mapeo lineal: Convierte el ángulo (0-180) al ancho de pulso en microsegundos (500-2500).
    # (angulo / 180) da un porcentaje (0.0 a 1.0) de la carrera total.
    us = int(min_us + (max_us - min_us) * (angulo / 180))

    # 2. Conversión a 'Duty': Convierte el ancho de pulso (μs) al valor de 'duty' que entiende MicroPython (0-1023).
    # La señal de 50Hz tiene un ciclo total de 20.000 μs (1 / 50 * 1.000.000).
    # El 'duty' es la proporción del pulso 'ON' (en μs) sobre el ciclo total (20.000 μs), escalado a 10 bits (0-1023).
    # Fórmula: duty = (us / 20000) * 1023
    # Fórmula alternativa (más genérica): duty = (us * 1023 * freq) / 1_000_000
    duty = int(us * 1023 * 50 / 1000000)
    
    # Envía el pulso al servo correcto
    servos[servo_id].duty(duty)
    
    # Actualiza la posición en nuestra lista de seguimiento
    posiciones[servo_id] = angulo
    print(f"Servo {servo_id+1} → {angulo}° (Duty: {duty})")

# ===== CALLBACK MQTT =====
def on_message(topic, msg):
    """
    Función 'Callback': Se ejecuta automáticamente CADA VEZ que llega un mensaje
    en uno de los temas a los que estamos suscritos.
    :param topic: El tema en el que se recibió el mensaje.
    :param msg: El contenido del mensaje (en bytes).
    """
    try:
        # 1. Decodificar el mensaje de bytes a un string de texto (ej: "servo1:120")
        texto = msg.decode()
        print("Mensaje recibido:", texto)
        
        # 2. Parsear (analizar) el comando
        # Formato esperado: "servoX:angulo" (ej: "servo1:120")
        if texto.startswith("servo"):
            # Quita "servo" -> "1:120" y luego divide por ":" -> s="1", ang="120"
            s, ang = texto.replace("servo", "").split(":")
            
            # Convierte a números enteros y ajusta el ID (servo "1" es el índice 0)
            servo_id = int(s) - 1
            angulo = int(ang)
            
            # 3. Ejecutar la acción
            set_servo(servo_id, angulo)
            
            # 4. Publicar una respuesta de confirmación
            estado = f"Servo {servo_id+1} movido a {angulo} grados"
            client.publish(TOPIC_PUB, estado)
            
    except Exception as e:
        # Si algo falla (ej: mensaje mal formateado "hola"), imprime el error
        # y evita que el programa se detenga.
        print("Error al procesar el mensaje:", e)

# ===== CONEXIÓN MQTT =====
print("Conectando al broker MQTT...")
# Crea una instancia del cliente MQTT
# "esp32_brazo" es el ID único de este cliente. Si otro dispositivo usa el mismo ID, uno será desconectado.
client = MQTTClient("esp32_brazo", BROKER)

# Configura la función 'on_message' para que sea llamada cuando lleguen mensajes
client.set_callback(on_message)

# Conecta al servidor broker
client.connect()

# Se suscribe al tema 'brazo/control'. Ahora recibirá todos los mensajes enviados a ese tema.
client.subscribe(TOPIC_SUB)
print("Conectado al broker MQTT y suscrito a", TOPIC_SUB)

# ===== LOOP PRINCIPAL =====
# El programa principal se ejecuta en este bucle infinito
while True:
    # Revisa si hay mensajes nuevos esperando en el broker.
    # Si hay un mensaje, esta función automáticamente llamará a 'on_message'.
    client.check_msg()
    
    # Pequeña pausa para que el procesador pueda realizar otras tareas
    # y no consumir el 100% de la CPU solo revisando mensajes.
    time.sleep(0.1)  # Revisa 10 veces por segundo