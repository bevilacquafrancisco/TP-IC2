# --- LIBRERÍAS ---
# Importamos la biblioteca Paho-MQTT, que es el cliente estándar de Python para MQTT.
# Le ponemos el alias 'mqtt' para que sea más corto de escribir.
import paho.mqtt.client as mqtt

# ===== CONFIGURACIÓN GLOBAL =====
# Dirección del servidor MQTT. Debe ser EXACTAMENTE el mismo que usa el ESP32.
BROKER = "test.mosquitto.org"

# Tema (canal) donde este script va a PUBLICAR (enviar) los comandos.
# Debe coincidir con el TOPIC_SUB del ESP32.
TOPIC_PUB = "brazo/control"

# Tema (canal) al que este script se va a SUSCRIBIR (escuchar).
# Usado para recibir mensajes de estado o confirmaciones del ESP32.
# Debe coincidir con el TOPIC_PUB del ESP32.
TOPIC_SUB = "brazo/estado"

# ===== CALLBACKS (Funciones de Eventos) =====
# Estas funciones se ejecutan automáticamente cuando ocurren eventos de red.

def on_connect(client, userdata, flags, rc):
    """
    Callback: Se ejecuta automáticamente cuando el cliente se conecta exitosamente al broker.
    'rc' (result code) indica si la conexión fue exitosa (rc=0).
    """
    print(f"Conectado al broker MQTT (Código: {rc})")
    
    # Una vez conectados, nos suscribimos al tema de estado para escuchar al ESP32
    client.subscribe(TOPIC_SUB)
    print(f"Suscrito al tema: {TOPIC_SUB}")

def on_message(client, userdata, msg):
    """
    Callback: Se ejecuta automáticamente cada vez que llega un mensaje
    en un tema al que estamos suscritos (en este caso, 'brazo/estado').
    """
    # msg.payload contiene los datos en bytes, .decode() los convierte a texto.
    print(f"[ESP32 dice] {msg.payload.decode()}")

# ===== CONEXIÓN =====
# 1. Crear una instancia del cliente MQTT. "pc_control" es el ID único.
client = mqtt.Client("pc_control")

# 2. Asignar las funciones de callback a los eventos del cliente
client.on_connect = on_connect  # Llama a 'on_connect' cuando se conecte
client.on_message = on_message  # Llama a 'on_message' cuando reciba un msg

# 3. Conectar al broker
# Se conecta al BROKER, usando el puerto estándar 1883.
# 60 es el 'keepalive': el cliente enviará un "ping" cada 60s
# para mantener la conexión activa.
client.connect(BROKER, 1883, 60)

# 4. Iniciar el bucle de red (MUY IMPORTANTE)
# loop_start() inicia un hilo (thread) en segundo plano.
# Este hilo se encarga de:
#   - Recibir mensajes (y llamar a on_message)
#   - Enviar pings (keepalive)
#   - Gestionar reconexiones si se cae la red
# Esto permite que nuestro script principal (el 'while True')
# se dedique a pedir 'input' al usuario sin bloquear la red.
client.loop_start()

# ===== ENVÍO DE COMANDOS (Loop principal) =====
# Este es el bucle principal de nuestro programa.
print("Escribe tus comandos. Escribe 'salir' para terminar.")
while True:
    # Pausa el script y espera a que el usuario escriba algo y presione Enter
    comando = input("Comando (ej: servo1:90 o 'salir'): ")
    
    # Comprobamos si el usuario quiere salir
    if comando.lower() == "salir":
        break  # Rompe el bucle 'while True' para terminar el programa
    
    # Si no es 'salir', publica el comando en el tema 'brazo/control'
    # El ESP32 está escuchando en este tema y reaccionará.
    client.publish(TOPIC_PUB, comando)

# ===== DESCONEXIÓN LIMPIA =====
# Si el bucle 'while' se rompe (porque el usuario escribió 'salir')...

# 1. Detiene el hilo de red que iniciamos con loop_start()
client.loop_stop()

# 2. Envía un mensaje de desconexión al broker
client.disconnect()

print("Desconectado del broker MQTT")