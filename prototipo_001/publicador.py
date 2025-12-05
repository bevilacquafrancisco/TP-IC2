import paho.mqtt.client as mqtt
import json
import time

BROKER = "192.168.0.10"  # IP de tu PC (donde corre Mosquitto)
PORT = 1883
TOPIC = "brazo/comando"

client = mqtt.Client("PC-Control")
client.connect(BROKER, PORT, 60)

# Ejemplo de comandos para mover los servos
comandos = [
    {"servo": 1, "angulo": 0},
    {"servo": 1, "angulo": 90},
    {"servo": 2, "angulo": 45},
    {"servo": 3, "angulo": -45},
    {"servo": 4, "angulo": 90},
]

for cmd in comandos:
    payload = json.dumps(cmd)
    client.publish(TOPIC, payload)
    print("Enviado:", payload)
    time.sleep(2)

client.disconnect()
