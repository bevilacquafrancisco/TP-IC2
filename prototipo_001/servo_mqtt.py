from machine import Pin, PWM
from time import sleep
import network
from umqtt.simple import MQTTClient
import json

# --- CONFIGURACIÓN WIFI ---
SSID = "iPhone de Francisco"        # ¡CAMBIAR ESTO!
PASSWORD = "password" # ¡CAMBIAR ESTO!

# --- CONFIGURACIÓN MQTT ---
# Usamos el broker público para que funcione la Web sin configurar la PC local
BROKER = "test.mosquitto.org" 
TOPIC_CMD = b"brazo/comando"   # Recibimos órdenes aquí
TOPIC_ESTADO = b"brazo/estado" # Enviamos datos aquí (Requisito Telemetría)
CLIENT_ID = "ESP32_Brazo_Robot"

# --- SERVOS (Pines) ---
# Pines: 15, 2, 4, 5
servo_pins = [15, 2, 4, 5]
servos = [PWM(Pin(pin), freq=50) for pin in servo_pins]

# Guardamos la posición actual para enviarla a la web
posiciones_actuales = [90, 90, 90, 90] 

def set_angle(index, angle):
    """
    Mueve el servo al ángulo especificado (0 a 180 grados).
    Usa duty_u16 para 16 bits de resolución (0-65535).
    """
    if angle < 0: angle = 0
    if angle > 180: angle = 180
    
    # Mapeo: 0-180 grados -> 500-2500 microsegundos
    min_us = 500
    max_us = 2500
    us = int(min_us + (angle / 180) * (max_us - min_us))
    
    # Conversión a Duty Cycle de 16 bits
    # Periodo 50Hz = 20000 us
    duty = int((us / 20000) * 65535)
    
    servos[index].duty_u16(duty)
    posiciones_actuales[index] = angle # Actualizamos memoria

# --- CONECTAR WIFI ---
wifi = network.WLAN(network.STA_IF)
wifi.active(True)
if not wifi.isconnected():
    print("Conectando a WiFi...")
    wifi.connect(SSID, PASSWORD)
    while not wifi.isconnected():
        print(".", end="")
        sleep(1)
print("\nWiFi conectado:", wifi.ifconfig())

# --- CALLBACK MQTT ---
def on_message(topic, msg):
    print("Mensaje recibido:", msg)
    try:
        # Decodificar JSON
        data = json.loads(msg)
        
        # Extraer datos (Tu formato: {"servo": X, "angulo": Y})
        # Restamos 1 porque tus comandos envían servo 1-4, pero la lista es 0-3
        servo_idx = data["servo"] - 1 
        angulo = data["angulo"]
        
        # Mover servo
        if 0 <= servo_idx < len(servos):
            set_angle(servo_idx, angulo)
            print(f"Servo {servo_idx+1} -> {angulo}°")
            
            # --- CUMPLIMIENTO REQUISITO TELEMETRÍA ---
            # Enviar confirmación de vuelta al usuario/PC
            telemetria = {
                "msg": "movimiento_ok",
                "servo": servo_idx + 1,
                "angulo_actual": angulo,
                "todos_servos": posiciones_actuales
            }
            client.publish(TOPIC_ESTADO, json.dumps(telemetria))
            
    except Exception as e:
        print("Error procesando mensaje:", e)

# --- CONECTAR AL BROKER ---
try:
    client = MQTTClient(CLIENT_ID, BROKER)
    client.set_callback(on_message)
    client.connect()
    client.subscribe(TOPIC_CMD)
    print(f"Conectado a {BROKER}. Suscrito a {TOPIC_CMD}")
    
    # Aviso inicial
    client.publish(TOPIC_ESTADO, json.dumps({"msg": "ESP32 Online", "sistema": "listo"}))

    # --- LOOP PRINCIPAL ---
    while True:
        client.check_msg()
        sleep(0.01) # Pequeña pausa para no bloquear
        
except Exception as e:
    print("Error crítico:", e)
    # Aquí podrías agregar machine.reset() si quieres reinicio automático