import paho.mqtt.client as mqtt
import json
import time

# --- CONFIGURACIÓN ---
BROKER = "test.mosquitto.org"
PORT = 1883
TOPIC = "brazo/comando"

# --- CONEXIÓN ---
print(f"Conectando a {BROKER}...")
client = mqtt.Client("PC_Solo_Conexion") # ID único
client.connect(BROKER, PORT, 60)

# Iniciamos el bucle en segundo plano para mantener la conexión viva (Pings)
client.loop_start()
print("✅ Conectado al protocolo MQTT.")
print("El sistema está en espera (Idle). No se enviarán movimientos automáticos.")
print("------------------------------------------------------------------")
print("Opcional: Escribe un comando manual (ej: servo1:90) o escribe 'salir'.")
print("Si no escribes nada, la conexión se mantiene abierta sin ruido.")

try:
    while True:
        # El script se queda esperando aquí. No envía nada a menos que tú lo escribas.
        comando = input("Esperando input > ")
        
        if comando.lower() == "salir":
            break
            
        # Lógica para enviar solo si el usuario escribe manualmente
        # Aceptamos formato "servo1:90" y lo convertimos a JSON para la ESP32
        try:
            if ":" in comando:
                partes = comando.split(":")
                servo_num = int(partes[0].replace("servo", ""))
                angulo = int(partes[1])
                
                # Creamos el paquete JSON
                datos = {"servo": servo_num, "angulo": angulo}
                payload = json.dumps(datos)
                
                client.publish(TOPIC, payload)
                print(f"📤 Enviado manual: {payload}")
            else:
                print("⚠️ Formato incorrecto. Usa: servoX:angulo (ej: servo1:90)")
                
        except ValueError:
            print("⚠️ Error en los números. Intenta de nuevo.")

except KeyboardInterrupt:
    print("\nDetenido por el usuario.")

# --- CIERRE ---
client.loop_stop()
client.disconnect()
print("Desconectado.")