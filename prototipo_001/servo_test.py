from machine import Pin, PWM
from time import sleep

# --- CONFIGURACIÓN DE SERVOS ---
# Cambia los pines según tu conexión
servo_pins = [15, 2, 4, 5]
servos = [PWM(Pin(pin), freq=50) for pin in servo_pins]

# --- FUNCIONES AUXILIARES ---
def set_angle(servo, angle):
    """
    Ajusta el ángulo del servo.
    angle: -90 (izquierda), 0 (centro), 90 (derecha)
    """
    # Conversión de ángulo a ciclo de trabajo (duty_u16)
    # 1 ms → ~3276 | 2 ms → ~6553 | 1.5 ms → ~4915
    min_us = 500     # Ajustado por seguridad
    max_us = 2500
    us = int((angle + 90) * (max_us - min_us) / 180 + min_us)
    duty = int(us / 20000 * 65535)
    servo.duty_u16(duty)

# --- PROGRAMA PRINCIPAL ---
posiciones = [-90, 0, 90]

for i, servo in enumerate(servos):
    print(f"\nProbando SERVO {i+1} en pin {servo_pins[i]}")
    for ang in posiciones:
        print(f" → Moviendo a {ang}°")
        set_angle(servo, ang)
        sleep(2)
    servo.deinit()
    print("✅ Servo comprobado")

print("\nTodos los servos fueron probados correctamente.")
