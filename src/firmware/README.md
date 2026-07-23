# `src/firmware/` — Firmware ESP32 (MicroPython) — Brazo Robótico Pick & Place

> Proyecto: Brazo Robótico Pick & Place — Ingeniería en Computación II (UNRAF)  
Autor:
Francisco Bevilacqua  
> Versión documentada: **v5.0 — modularización** del `robot_main.py` monolítico (v4.x)
> MicroPython: v1.20.0 on 2023-04-26; ESP32 module with ESP32
> Protocolo: MQTT sobre TCP, broker privado Mosquitto (ver `mosquitto-broker/README.md`)

---

## 1. Qué corre acá y por qué se modularizó

Este directorio contiene el firmware que se graba en la ESP32 y controla físicamente
el brazo robótico: 4 servomotores SG90 (base, hombro, codo, pinza) y un sensor
infrarrojo KY-032 que detecta cajas en la zona de recolección.

La versión original (`robot_main.py`, conservada en la raíz del repo como referencia
histórica y de comparación — **no es el firmware que se graba en la placa**)
concentraba en un único archivo de aproximadamente 1000 líneas: configuración,
estado global mutable, gestión de WiFi, cliente MQTT, lectura de sensor, control de
servos, el dispatcher de comandos entrantes y el loop principal.

Funcionaba correctamente, pero violaba el Principio de Responsabilidad Única (SRP) a
**nivel de archivo**: cualquier cambio puntual — recalibrar una posición del brazo,
ajustar un timeout, agregar un comando MQTT nuevo — requería abrir un archivo enorme y
ubicar el fragmento correcto entre lógica no relacionada, con el riesgo creciente de
tocar algo por error a medida que el archivo crecía. Esto es exactamente el problema
que Sommerville (cap. 7, *Software Engineering*) describe como pérdida de
mantenibilidad por acoplamiento estructural, y que motiva la modularización aplicada
acá.

**La lógica de negocio es idéntica entre la v4.x
monolítica y la v5.0 modular.** Este es un refactor estructural puro (cambia *cómo*
está organizado el código), no un cambio de comportamiento (qué hace el robot ante
cada comando o detección es exactamente lo mismo). Esa distinción — refactor vs.
cambio funcional — es un concepto de Sommerville cap. 9 (Evolución del Software) que
vale la pena destacar.

---

## 2. Estructura del directorio

```
firmware/
├── config.py     # Constantes: WiFi, MQTT, pines, PWM, timing, posiciones calibradas
├── state.py      # Estado mutable compartido (patrón Singleton) + utilidades de logging
├── wifi.py       # Gestión de la conexión WiFi (capa de red física)
├── mqtt.py       # Capa de transporte MQTT (conexión, publicación, polling)
├── sensor.py     # Lectura y debounce del sensor KY-032 (hardware puro)
├── servos.py     # Control de servomotores + secuencias de movimiento
├── commands.py   # Dispatcher de comandos MQTT + lógica de modos + telemetría
└── main.py       # Orquestación: boot, inicialización de hardware/red, loop principal
```

Al grabar en la ESP32, **todos estos archivos van sueltos en la raíz del sistema de
archivos de la placa** (no en una subcarpeta), y `main.py` debe llamarse exactamente
`main.py` porque MicroPython lo ejecuta automáticamente al bootear — es un nombre
reservado por el intérprete, no una convención del proyecto.

### 2.1 Grafo de dependencias (sin ciclos — requisito duro de MicroPython)

```
config.py, state.py            (módulos hoja, sin dependencias entre sí)
        ↑
    wifi.py
        ↑
    mqtt.py  ←── sensor.py      (sensor.py es hoja de hardware, no depende de mqtt.py)
        ↑              ↑
    servos.py    ───────┘
        ↑
    commands.py          (conoce state, mqtt, servos, sensor, wifi)
        ↑
    main.py               (conoce y conecta TODOS los módulos anteriores)
```

MicroPython (y Python en general) no admite imports circulares: si el módulo A
importa al módulo B, B no puede importar a A directamente. Este grafo se diseñó
explícitamente en capas para que cada módulo dependa solo de los que están "debajo"
de él, nunca al revés. `main.py` es el único módulo con visión completa del sistema:
es quien "inyecta" `commands.on_message` y `commands.publish_status` dentro de
`mqtt.connect_mqtt()` como parámetros (callbacks), en vez de que `mqtt.py` importe
`commands.py` directamente — lo cual habría cerrado un ciclo (`mqtt.py → commands.py
→ mqtt.py`). Esta técnica se llama **inyección de dependencias** y es la solución
estándar a este problema, documentada con detalle en el docstring de cabecera de
`mqtt.py`.

---

## 3. Por qué una clase `SystemState` y no variables globales sueltas

En el `robot_main.py` original, todo el estado eran variables globales de módulo
(`global mode`, `global arm_busy`, etc.) dentro de **un único archivo** — funcionaba
porque todo compartía el mismo namespace de Python.

Al separar en varios archivos, ese patrón se rompe silenciosamente. En Python, hacer:

```python
# módulo_a.py
mode = "MANUAL"
```
```python
# módulo_b.py
from módulo_a import mode
mode = "AUTOMATICO"   # ¡esto NO modifica módulo_a.mode!
```

**no** modifica la variable original — crea una variable local nueva en `módulo_b`
que sombrea (*shadows*) el import, porque los nombres inmutables (`int`, `str`,
`bool`) se **rebind** (se reasignan) en vez de mutarse. Este bug es difícil de
detectar porque el código no lanza ningún error: simplemente el cambio de estado
queda invisible para el resto del sistema.

La solución aplicada, siguiendo el mismo patrón que `settings` en
`backend/app/core/config.py` (Singleton de facto), es encapsular todo el estado
mutable en los **atributos de una única instancia**:

```python
# state.py
class SystemState:
    def __init__(self):
        self.mode = "MANUAL"
        self.arm_busy = False
        # ...

state = SystemState()   # instancia única
```

```python
# cualquier otro módulo
from state import state
state.mode = "AUTOMATICO"   # SÍ es visible desde cualquier módulo que importó `state`
```

Mutar un **atributo** de un objeto (`state.mode = "AUTOMATICO"`) sí es visible desde
cualquier módulo que haya importado esa misma instancia, porque todos apuntan al
mismo objeto en memoria — a diferencia del rebind de una variable de nombre simple.
Los diccionarios (`pallet_count`, `servo_angle`) ya eran mutables en el original y se
conservan igual dentro de la clase, porque mutar el contenido de un dict (`d[key] =
val`) nunca tuvo este problema — solo el *rebind* de nombres simples lo tiene.

`state.py` no importa nada del proyecto (módulo hoja, igual que `config.py`),
garantizando que puede importarse desde cualquier otro módulo sin riesgo de ciclo,
sin importar cómo evolucione el resto del firmware.

---

## 4. Descripción módulo por módulo

### 4.1 `config.py` — fuente única de verdad para constantes

Sin dependencias (módulo hoja). Contiene:

- **`WIFI`**: SSID, password, timeout de conexión.
- **`MQTT`**: IP del broker privado, puerto (1883), topics (`robot/cmd` / `robot/log`),
  `client_id`, y **credenciales de usuario** (`user`, `password`) — obligatorias desde
  que se migró del broker público `test.mosquitto.org` (que aceptaba clientes
  anónimos) al broker Mosquitto privado con `allow_anonymous false` (ver
  `mosquitto-broker/README.md`).
- **`PINS`**: GPIOs asignados a cada servo y al sensor — específicamente elegidos
  entre los "seguros" del ESP32 (que no interfieren con las señales de boot
  strapping): 25, 26, 27, 32 para servos, 33 para el sensor.
- **`PWM_FREQ` / `PWM_MIN_DUTY` / `PWM_MAX_DUTY`**: parámetros de la señal PWM para
  los SG90 a 50 Hz con resolución de 10 bits.
- **`TIMING`**: todos los intervalos y umbrales temporales del sistema en un solo
  lugar (polling MQTT, heartbeat, garbage collector, polling de sensor, debounce,
  pasos de movimiento suave, timeout de watchdog) — centralizados para facilitar el
  ajuste fino durante pruebas de banco sin tener que buscar constantes dispersas por
  el código.
- **`POS`**: diccionario de posiciones calibradas en grados (`[base, hombro, codo,
  pinza]`, con `None` significando "no mover este servo en este paso") para cada zona
  operativa: home, tránsito seguro, zona de recolección, y cada nivel de apilado de
  cada pallet. Modificar **solo acá** para recalibrar sin tocar la lógica de
  movimiento en `servos.py`.
- **`RESET_REASONS`**: traducción de los códigos numéricos de `machine.reset_cause()`
  a texto legible, usada tanto en logs de arranque como en la telemetría enviada a
  la GUI.

**Nota de seguridad `[SEC]` documentada explícitamente en el propio archivo**: las
credenciales de WiFi y del broker MQTT quedan en texto plano en `config.py`.
MicroPython no tiene un mecanismo estándar de variables de entorno como el Python de
servidor (no hay un proceso "shell" que las inyecte en tiempo de ejecución sobre la
placa). La mitigación real para un despliegue de producción sería un archivo
`secrets.py` separado, excluido del control de versiones (`.gitignore`), e importado
desde acá (`from secrets import WIFI_PASSWORD, MQTT_PASSWORD`). Para esta entrega
académica, hardcodear en `config.py` es una **limitación de alcance aceptada y
declarada** (ver ADR correspondiente en `planificacion.md`), mantenida igual que en
el `robot_main.py` original para no introducir cambios de comportamiento durante la
modularización — el objetivo de este refactor fue estructural, no de seguridad (esa
mejora queda documentada como trabajo futuro).

### 4.2 `state.py` — estado global y logging

Ver sección 3 para el razonamiento de diseño. Además de la clase `SystemState`,
alberga `log()` y `log_sep()`. Estas dos funciones no tienen un módulo "natural"
propio dentro de la lista de 8 archivos del proyecto — se ubican en `state.py` por
tres razones documentadas en su propio docstring: (1) no dependen de nada, igual que
el resto del archivo; (2) las usan **todos** los demás módulos (wifi, mqtt, servos,
sensor, commands, main), así que vivir en `state.py` evita que cualquiera de ellos
dependa de otro módulo de negocio solo para poder loguear; (3) conceptualmente, cada
línea de log documenta un cambio de estado del sistema, así que mantenerlas junto a
`state` es coherente, no arbitrario. Si el proyecto creciera y ameritara logging
estructurado (persistencia a archivo, niveles configurables en runtime), esto se
separaría a su propio `logger.py` — documentado como evolución futura razonable.

### 4.3 `wifi.py` — capa de red física

Único punto de contacto del firmware con `network.WLAN`. Expone `connect_wifi()`
(con reintento por polling no bloqueante de 500 ms, hasta `WIFI["timeout_s"]`
segundos) y `wifi_is_up()` (usada por `mqtt.py` antes de intentar reconectar, y por
`commands.publish_status()` para reportar RSSI).

### 4.4 `sensor.py` — hardware puro, sin conocimiento de negocio

Responsabilidad estrictamente acotada a leer el pin del KY-032 y aplicar debounce por
muestreo consecutivo. **No** decide qué hacer cuando se detecta una caja (eso depende
del modo de operación activo) y **no** publica eventos MQTT — esa lógica de negocio
vive en `commands.py`. Esta separación (hardware puro vs. decisión de negocio) es
deliberada: mantener `sensor.py` sin dependencia de `mqtt.py` permitiría, por
ejemplo, escribir un test unitario que simule el pin y verifique el algoritmo de
debounce sin necesitar una conexión MQTT activa.

`poll_debounced()` tiene comportamiento de "flanco de subida confirmado": retorna
`True` **exactamente una vez** por objeto detectado (resetea su contador interno
apenas confirma), no en cada poll mientras el objeto sigue frente al sensor — así el
caller (`commands.process_sensor_event`) no necesita lógica adicional de "¿ya procesé
este evento?".

### 4.5 `servos.py` — control de motores y secuencias de movimiento

Sí importa `mqtt.py` (a diferencia de `sensor.py`), porque las secuencias de
movimiento publican eventos de progreso hacia la GUI en tiempo real (`move_start`,
`move_done`, `pick_start`, `box_collected`, etc.) mientras el movimiento está
ocurriendo, no después. No hay riesgo de ciclo porque `mqtt.py` no importa `servos.py`
de vuelta.

Funciones clave:

- **`angle_to_duty()`**: mapeo lineal de ángulo (0–180°) a duty cycle PWM de 10 bits.
- **`servo_set(id, angle, smooth)`**: si `smooth=True`, mueve el servo paso a paso
  (`TIMING["servo_step_deg"]` por paso, cada `TIMING["servo_step_ms"]`) para evitar
  tirones mecánicos que podrían desalinear la pinza o tumbar una caja ya sostenida.
  Se usa `smooth=False` solo para la pinza, donde el movimiento rápido es deseable.
- **`move_transito()`**: levanta hombro y codo **antes** de girar la base — previene
  que el brazo tumbe cajas al desplazarse entre la zona de recolección y los
  pallets. Este orden de operaciones (levantar antes de girar) es una decisión de
  diseño mecánico explícita, no incidental.
- **`move_sequence(action)`**: ejecuta movimientos preconfigurados del modo MANUAL
  (`home`, `recolectar`, `abrir_pinza`, `cerrar_pinza`). Usa `state.arm_busy` como
  guard de exclusión mutua.
- **`pick_and_place(dest_pallet)`**: la secuencia completa de 9 pasos (tránsito →
  recolección → agarre → tránsito → giro a destino → nivel de apilado → depósito →
  tránsito → vuelta a recolección), usada por los modos SEMI_AUTO y AUTOMÁTICO.
  Actualiza `state.pallet_count`/`state.pallet_full` y publica eventos de progreso
  (`pick_start`, `box_collected`, `pallet_full`, `error`).

### 4.6 `mqtt.py` — capa de transporte MQTT, agnóstica del protocolo de comandos

Solo sabe hablar el protocolo MQTT: conectar, publicar, suscribir, hacer poll de
mensajes entrantes. No conoce el formato JSON de los comandos del robot — eso es
responsabilidad de `commands.py`. Esta separación aplica el Principio de Inversión de
Dependencias (DIP, SOLID): si mañana se reemplazara `umqtt.simple` por otra librería
MQTT, solo este archivo cambiaría.

Decisiones heredadas de la v4.0 monolítica, preservadas sin cambios de comportamiento:

- **`clean_session=False`**: el broker conserva la sesión y los mensajes QoS 1 no
  entregados entre desconexiones de la ESP32.
- **Suscripción a `topic_cmd` con `qos=1`**: garantiza entrega *al menos una vez* de
  los comandos de la GUI, incluso si la ESP32 estaba offline al momento de enviarse.
  La deduplicación por `msg_id` (ver 4.7) neutraliza los duplicados inherentes a esta
  garantía de QoS 1 ("at least once", no "exactly once").
- **Re-sincronización activa**: al reconectar, publica `'online'` inmediatamente y,
  si se le pasó un `status_cb`, lo invoca 500 ms después — así la GUI no tiene que
  esperar hasta el próximo heartbeat (20 s) para recibir el estado completo de
  pallets, servos y modo tras una reconexión.
- **Publicación fire-and-forget con QoS 0**: `mqtt_publish()` nunca bloquea esperando
  un PUBACK del broker (`umqtt.simple` solo bloquea al *recibir* mensajes QoS 1 en la
  suscripción, no al publicar), por lo tanto no hay riesgo de que el loop principal
  se congele esperando confirmación de un evento de telemetría.

`connect_mqtt(on_message_cb, status_cb=None)` recibe ambos callbacks como
parámetros — es el mecanismo de inyección de dependencias descripto en 2.1 para
romper el ciclo de imports con `commands.py`.

### 4.7 `commands.py` — capa de aplicación: dispatcher + lógica de modos

El módulo de más alto nivel del firmware junto con `main.py`: interpreta los
comandos JSON entrantes de la GUI, decide qué hacer con las detecciones del sensor
según el modo activo, y arma el snapshot de telemetría completo. Orquesta a `servos`,
`sensor`, `mqtt` y `state`, pero **ninguno** de esos módulos lo importa de vuelta —
así se evita cualquier ciclo (ver 2.1).

Funciones públicas expuestas a `main.py`:

| Función | Responsabilidad |
|---|---|
| `on_message(topic, msg)` | Callback registrado en `mqtt.connect_mqtt()`. Parsea JSON, deduplica por `msg_id`, despacha a un handler privado según `cmd`. |
| `publish_status()` | Snapshot completo de telemetría (modo, pallets, servos, sensor, memoria, reconexiones, RSSI, causa de reset). |
| `process_sensor_event()` | Debe llamarse cada `TIMING["sensor_poll_ms"]` desde el loop principal, solo si `state.arm_busy` es `False`. |

**Deduplicación QoS 1 por `msg_id`**: con QoS 1, el broker puede reenviar el mismo
mensaje si no recibió PUBACK a tiempo. Para evitar ejecutar un comando dos veces (ej.
depositar la misma caja dos veces, o cambiar de modo repetidamente), se compara el
campo opcional `"msg_id"` del payload contra `state.last_cmd_id`. Comandos sin riesgo
de duplicación (como `"status"`) no incluyen `msg_id` y no pasan por este filtro.

Comandos soportados (protocolo JSON sobre `robot/cmd`):

```
{"cmd":"set_mode",     "mode":"MANUAL"|"SEMI_AUTO"|"AUTOMATICO"}
{"cmd":"servo",        "id":1-4, "angle":0-180}
{"cmd":"move",         "action":"home"|"recolectar"|"abrir_pinza"|"cerrar_pinza"}
{"cmd":"semi_decision","dest":"P1"|"P2"|"ignorar"}
{"cmd":"pallet_clear", "pallet":1|2}
{"cmd":"status"}
```

Lógica de modos en `process_sensor_event()`:

- **MANUAL**: la detección del sensor se loguea y se publica como telemetría, pero
  no dispara ninguna acción — el operador controla todo manualmente.
- **SEMI_AUTO**: marca `state.semi_pending = True` y publica `box_detected`; la GUI
  muestra una alerta y espera un comando `semi_decision` del operador.
- **AUTOMÁTICO**: decide el destino automáticamente (llena Pallet 1 antes que
  Pallet 2) y dispara `pick_and_place()` directamente, sin intervención humana.

**Advertencia de diseño documentada en el propio módulo**: `on_message()` debe ser
rápida, pero las secuencias largas (`move_sequence`, `pick_and_place`) se ejecutan
**síncronamente** dentro del callback, porque `umqtt.simple` en MicroPython no ofrece
un modelo asíncrono nativo simple para este proyecto. Se acepta el trade-off de que
`check_msg()` no vuelve a llamarse hasta que la secuencia termina — igual que en la
v4.0 original, sin cambio de comportamiento.

### 4.8 `main.py` — orquestación y loop principal

Secuencia de arranque: `print_boot_info()` → inicialización de hardware (`servos`,
`sensor`) → conectividad (`wifi`, `mqtt`) → configuración opcional de WDT → entrada al
loop principal.

El loop principal ejecuta **6 tareas periódicas no bloqueantes**, coordinadas por
comparación de `ticks_ms()` (patrón estándar en sistemas embebidos bare-metal: evita
usar `sleep()` como temporizador, lo que bloquearía todas las demás tareas):

1. Alimentar el Watchdog Timer (si está activo) — primera operación de cada
   iteración, para minimizar la chance de que una tarea lenta más abajo dispare un
   reset espurio.
2. Poll MQTT no bloqueante + reconexión automática de WiFi/MQTT según corresponda.
3. Lectura de sensor con debounce (se salta si `state.arm_busy` es `True`).
4. Heartbeat / telemetría periódica cada `TIMING["heartbeat_ms"]` (20 s).
5. Garbage collector manual cada `TIMING["gc_ms"]` (15 s) — en un sistema de RAM
   limitada con ejecución 24/7, forzar el GC periódicamente mantiene el heap
   predecible en vez de dejarlo solo al criterio del intérprete.
6. `idle()` — cede ciclos al RTOS subyacente (ahorro de energía, no bloquea el WDT).

`_shutdown()` (invocada desde el bloque `finally`, ante `Ctrl+C` en Thonny o una
excepción no manejada) lleva el brazo a una posición segura de tránsito antes de
desactivar el PWM de todos los servos y publicar `'offline'` — evita dejar el brazo
colgado en una posición arbitraria con carga eléctrica sobre los servos.

---

## 5. Watchdog Timer (WDT) — desarrollo vs. producción

```python
WDT_ENABLED = False   # config.py
```

`False` durante el desarrollo con Thonny: si se dejara `True` mientras se debuguea
paso a paso (breakpoints), el watchdog reiniciaría la ESP32 en medio de una sesión de
pausa — fácilmente confundible con un bug real. Se cambia a `True` únicamente para la
grabación final de producción (`main.py` en la placa, sin Thonny conectado
supervisando), con `TIMING["wdt_timeout_ms"] = 8000`: si el loop principal se bloquea
más de 8 segundos sin alimentar el watchdog, la ESP32 se reinicia sola —
recuperación automática ante un cuelgue, sin intervención humana, crítico para un
sistema que debería poder operar desatendido.

---

## 6. Convención de tópicos y eventos MQTT

```
Suscripción  (GUI → ESP32):  robot/cmd   (comandos de control, QoS 1 para acciones críticas)
Publicación  (ESP32 → GUI):  robot/log   (eventos, estados, telemetría, QoS 0)
```

Eventos publicados en `robot/log`:

```
{"event":"online",        "reset_cause":N, "mem_free":N, "reconnects":N, "mode":...}
{"event":"status",        "mode":..., "arm_busy":..., "pallets":..., "servos":..., ...}
{"event":"sensor",        "detected":bool}
{"event":"box_detected"}                       (SEMI_AUTO: espera decisión del usuario)
{"event":"servo_ack",     "id":N, "angle":N}
{"event":"move_start"|"move_done", "action":...}
{"event":"pick_start",    "dest":"P1"|"P2", "level":N}
{"event":"box_collected", "dest":"P1"|"P2", "level":N, "count":N, "full":bool}
{"event":"box_ignored"}
{"event":"pallet_full",   "pallet":1|2}
{"event":"all_pallets_full"}
{"event":"pallet_cleared","pallet":1|2}
{"event":"mode_changed",  "mode":...}
{"event":"error",         "msg":...}
{"event":"offline"}
```

Este esquema de eventos es el contrato que consume `gui/robot_script.js` (ver
`gui/README.md`) — cualquier cambio en el nombre de un evento o en sus campos debe
actualizarse en ambos lados simultáneamente, ya que no hay un esquema JSON compartido
formal entre firmware y GUI en el alcance actual (limitación conocida: se podría
introducir un archivo `protocol.md` o un JSON Schema versionado como trabajo futuro).

---

## 7. Cómo grabar el firmware en la ESP32

1. Editar `config.py` con el SSID/password de WiFi y la IP real del broker Mosquitto
   privado (ver `mosquitto-broker/README.md` para levantarlo primero).
2. Con Thonny (u otra herramienta MicroPython), subir **los 8 archivos** de este
   directorio a la raíz del sistema de archivos de la ESP32.
3. Verificar que `WDT_ENABLED = False` mientras se prueba interactivamente.
4. Ejecutar y observar el log de arranque (`print_boot_info()`) en la consola de
   Thonny: confirma causa del último reset, memoria libre, versión de MicroPython,
   y configuración de broker/topics.
5. Para producción (demo final sin Thonny conectado), cambiar `WDT_ENABLED = True` y
   reiniciar la placa — `main.py` se ejecuta automáticamente al bootear.

---

## 8. Relación con `robot_main.py` / `robot_main_v4.py` (versiones históricas)

Los archivos `robot_main.py` (usa credenciales del broker privado) y
`robot_main_v4.py` (usa el broker público `test.mosquitto.org`, sin autenticación) que
puedan encontrarse sueltos en otras partes del repositorio son **versiones
monolíticas anteriores**, conservadas como evidencia de la evolución del proyecto. **No son
el firmware vigente.** El firmware que efectivamente se graba en la ESP32 es,
exclusivamente, el conjunto de 8 archivos de este directorio.