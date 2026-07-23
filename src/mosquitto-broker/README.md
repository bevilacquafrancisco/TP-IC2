# `mosquitto-broker/` — Broker MQTT privado (Eclipse Mosquitto)

Broker MQTT privado del sistema **Brazo Robótico Pick & Place**. Es la capa de
transporte publicador/suscriptor que conecta el firmware del ESP32
(`firmware/`) con el panel de control web (`gui/`), reemplazando al broker
público `test.mosquitto.org` utilizado en versiones anteriores del proyecto.

---

## 1. Rol en la arquitectura del sistema

```
┌─────────────┐   MQTT/TCP:1883    ┌──────────────────┐   MQTT/WS:9001        ┌──────────────────┐
│   ESP32     │◄──────────────────►│    Mosquitto     │◄───────────────────►  │  GUI (navegador) │
│ (firmware/) │   user: esp32      │ (este directorio)│  user: gui_operator   │  (robot_script.js│
└─────────────┘                    └──────────────────┘                       └──────────────────┘
                                            ▲
                                            │ valida contra
                                            │ passwd + acl.conf
                                            │
                                    (sin relación con el JWT del
                                     backend FastAPI — ver §5)
```

Este broker es el **único punto de encuentro** entre el hardware y la
interfaz de usuario. No conoce operadores humanos, sesiones ni tokens — solo
reconoce dos identidades de aplicación (`esp32` y `gui_operator`) y dos
tópicos (`robot/cmd`, `robot/log`). Toda la lógica de negocio (modos de
operación, secuencias de movimiento, decisiones semiautomáticas) vive en los
extremos del sistema; el broker se limita a enrutar mensajes de forma
confiable y a controlar quién puede publicar o suscribirse a cada tópico.

---

## 2. Contenido del directorio

| Archivo / carpeta | Propósito |
|---|---|
| `mosquitto.conf` | Configuración del broker: listeners, autenticación, logging, persistencia. |
| `acl.conf` | Lista de control de acceso: qué usuario puede leer/escribir en qué tópico. |
| `generar_passwd.py` | Script utilitario que genera el archivo `passwd` con las credenciales de los dos usuarios de aplicación. |
| `passwd` | Credenciales hasheadas de `esp32` y `gui_operator` (generado localmente, **no versionado**). |
| `logs/` | Registro de conexiones y errores del broker (generado en tiempo de ejecución, **no versionado**). |
| `data/` | Persistencia de sesiones y mensajes con calidad de servicio (generado en tiempo de ejecución, **no versionado**). |

`passwd`, `logs/` y `data/` están excluidos del control de versiones (ver
`.gitignore` de este subdirectorio): son secretos o datos de ejecución
regenerables, nunca artefactos de diseño que deban vivir en el repositorio.

---

## 3. `mosquitto.conf` — configuración explicada

### 3.1 Listeners duales: TCP y WebSocket

```conf
listener 1883
protocol mqtt

listener 9001
protocol websockets
```

**Decisión:** un mismo broker con dos listeners, en lugar de dos brokers
independientes.

**Justificación:** el firmware del ESP32 usa `umqtt.simple`, una
implementación de MQTT sobre TCP crudo sin soporte de WebSocket. El
navegador, en cambio, no puede abrir un socket TCP arbitrario contra el
puerto 1883 por restricciones propias del entorno del navegador (no existe
un `TCPSocket` genérico en JavaScript de página web); necesita WebSocket,
que es lo que expone `Paho.MQTT.Client` en `robot_script.js`. Concentrar
ambos listeners en un único proceso evita duplicar el estado del sistema
(tópicos, retención de mensajes, sesiones) en dos procesos independientes,
que sería la alternativa si se optara por dos brokers separados.

### 3.2 Autenticación obligatoria

```conf
allow_anonymous false
password_file C:/mosquitto-broker/passwd
acl_file C:/mosquitto-broker/acl.conf
```

**Decisión:** `allow_anonymous false` es no negociable. En la versión con
broker público, cualquier cliente en Internet podía publicar en `robot/cmd`
y mover el brazo físico sin ningún tipo de autenticación — el vector de
ataque más directo posible sobre un sistema con actuadores reales. Migrar a
un broker privado sin exigir autenticación habría sido una mejora
cosmética, sin mitigar el riesgo real que motivó el cambio.

**Alcance de `password_file`/`acl_file`:** al no declararse
`per_listener_settings true`, Mosquitto aplica `allow_anonymous`,
`password_file` y `acl_file` de forma **global**, con las mismas reglas para
ambos listeners (1883 y 9001). Se evaluó la alternativa de habilitar
`per_listener_settings` para tener políticas distintas por listener (por
ejemplo, una ACL más laxa en el listener interno), y se descartó: el
proyecto tiene exactamente dos clientes conocidos y una única superficie de
confianza (la red local de la demostración), por lo que una política
uniforme es más simple de auditar y no sacrifica seguridad real frente a la
complejidad adicional de mantener dos configuraciones distintas.

### 3.3 Logging

```conf
log_dest file C:/mosquitto-broker/logs/mosquitto.log
log_type error
log_type warning
log_type notice
log_type information
connection_messages true
log_timestamp true
```

**Decisión:** se registran los niveles `error`, `warning`, `notice` e
`information`, con mensajes de conexión/desconexión (`connection_messages`)
y marca de tiempo en cada línea. Deliberadamente **no** se habilita
`log_type debug`: ese nivel expone contenido de paquetes MQTT, lo cual en
este proyecto podría filtrar detalles de payload al log en disco sin
necesidad real — el nivel `information` ya es suficiente para diagnosticar
conexiones fallidas o rechazos de ACL durante el desarrollo y la demo.

Esta configuración atiende directamente la categoría OWASP de fallas de
logging y monitoreo: sin este registro, un rechazo de autenticación o de
ACL sería invisible para el operador, dificultando distinguir "el ESP32
está apagado" de "el ESP32 está enviando credenciales incorrectas".

### 3.4 Persistencia

```conf
persistence true
persistence_location C:/mosquitto-broker/data/
autosave_interval 60
```

**Justificación:** el firmware se conecta con `clean_session=False` y se
suscribe a `robot/cmd` con calidad de servicio QoS 1 (ver `firmware/mqtt.py`,
función `connect_mqtt()`). Esta combinación solo cumple su promesa —
comandos entregados incluso si el ESP32 estuvo desconectado al momento del
envío— si el broker persiste la sesión y la cola de mensajes en disco entre
reinicios del propio proceso de Mosquitto. Sin `persistence true`, un
reinicio del broker (no del ESP32) perdería cualquier mensaje QoS 1 en cola.
`autosave_interval 60` acota la ventana de pérdida ante un corte de energía
a un máximo de 60 segundos de mensajes no persistidos, sin la sobrecarga de
escribir a disco en cada mensaje individual.

### 3.5 Keepalive

```conf
max_keepalive 120
```

**Decisión:** el firmware negocia `keepalive=60` en su propia configuración
(`MQTT["keepalive"]`, en `firmware/config.py`). `max_keepalive 120` en el
broker es un techo, no el valor operativo: permite que un cliente pida hasta
120 segundos sin rechazar la conexión, pero no obliga a ese valor. Se fija
este techo para evitar que un cliente mal configurado negocie un keepalive
excesivamente largo, lo que retrasaría la detección de una desconexión real
por parte del broker.

---

## 4. `acl.conf` — control de acceso por mínimo privilegio

### 4.1 Modelo de amenaza mitigado

Sin lista de control de acceso, cualquier credencial válida habilita lectura
y escritura en **cualquier** tópico. Concretamente, sin este archivo:

- Un cliente `esp32` comprometido (o con un error de firmware) podría
  publicar directamente en `robot/cmd` y enviarse comandos a sí mismo,
  saltando por completo la lógica de decisión de la interfaz y del operador
  humano.
- Un cliente `gui_operator` comprometido podría publicar eventos falsos en
  `robot/log` — por ejemplo, simular `box_collected` o `pallet_full` sin que
  el hardware real haya ejecutado ninguna acción, engañando al operador que
  confía en la consola de eventos de la interfaz.

### 4.2 Reglas aplicadas

| Usuario | `robot/log` | `robot/cmd` | Rol |
|---|---|---|---|
| `esp32` | `write` (solo) | `read` (solo) | Fuente de verdad del hardware: publica telemetría, recibe comandos. |
| `gui_operator` | `read` (solo) | `write` (solo) | Consola del operador: visualiza telemetría, envía comandos. |

Cada usuario tiene permisos **estrictamente complementarios y no
superpuestos**: ninguno de los dos puede leer lo que él mismo escribe, y
ninguno puede escribir lo que solo debería leer. Un diseño con `readwrite`
en ambos tópicos para ambos usuarios habría sido más simple de escribir,
pero habilitaría exactamente los dos vectores de suplantación descritos en
el modelo de amenaza anterior.

### 4.3 Denegación por defecto

Mosquitto, al detectar un `acl_file` configurado, deniega automáticamente
cualquier tópico no listado explícitamente para un usuario — no existe (ni
es necesaria) una regla `deny all` al final del archivo. Esto aplica
directamente la política de "denegar todo por defecto, permitir lo
necesario": si en el futuro se agrega un tercer tópico al sistema (por
ejemplo, `robot/diagnostico` para telemetría extendida) y se omite declarar
quién puede usarlo, el comportamiento por defecto es bloquear el acceso, no
otorgarlo — el modo de fallo seguro correcto para un sistema con actuadores
físicos.

---

## 5. `passwd` — credenciales de aplicación

### 5.1 Generación de credenciales

El archivo `passwd` se genera con el script `generar_passwd.py`, incluido en
este directorio, que produce las entradas de `esp32` y `gui_operator`
utilizando la función `sha512_crypt` de la librería `passlib`. Se optó por
un script propio en lugar de invocar `mosquitto_passwd.exe` manualmente por
dos motivos: mantiene la generación de credenciales dentro del mismo
ecosistema de herramientas Python usado en el resto del proyecto (backend
incluido), y deja el procedimiento reproducible y versionable como código
(no como una secuencia de comandos manuales documentada solo en prosa). En
ambos casos —herramienta oficial o script propio— el hash se delega a una
implementación de biblioteca estándar y auditada; en ningún punto del
proyecto se implementa una primitiva criptográfica propia, consistente con
el criterio aplicado en `backend/app/core/security.py`.

> **Nota de verificación:** el formato de hash producido por
> `sha512_crypt` (prefijo `$6$`) no es necesariamente idéntico al formato
> nativo que genera `mosquitto_passwd.exe` en todas las versiones de
> Mosquitto (versiones recientes usan PBKDF2-SHA512, prefijo `$7$`). Antes
> de dar por definitivo este mecanismo, se debe confirmar —con una prueba
> real de conexión autenticada contra el broker instalado (ver §7.2)— que
> la versión de Mosquitto en uso reconoce el formato producido por el
> script. Si no lo reconoce, `mosquitto_passwd.exe` queda como alternativa
> directa: genera un archivo `passwd` en el formato garantizado para
> cualquier versión del broker, al costo de un paso manual adicional fuera
> del control de versiones del script.

### 5.2 Dos identidades, no una

`esp32` y `gui_operator` son credenciales de **aplicación**, no de persona.
Autentican "qué software se está conectando al broker" (el firmware o la
interfaz), no "qué operador humano está usando el sistema". Esa segunda
pregunta —quién es el humano detrás del panel— la responde una capa
completamente distinta: el token JWT emitido por el backend FastAPI
(`backend/`) tras el inicio de sesión en `login.html`.

### 5.3 Por qué estas dos capas no se fusionan

Es una decisión deliberada, no una limitación pendiente de resolver:

- Las credenciales MQTT (`gui_operator`) identifican **a la interfaz como
  aplicación** ante el broker — son las mismas para cualquier operador que
  use el panel, y viven en el código fuente del frontend
  (`gui/robot_script.js`), visibles para cualquiera con acceso al
  navegador.
- El JWT identifica **al operador humano individual** ante el backend,
  tiene expiración, y nunca se envía al broker MQTT — el brazo no necesita
  saber qué persona específica lo está operando, solo que el mensaje
  proviene de la interfaz autorizada.

Fusionar ambas capas exigiría que Mosquitto validara JWTs mediante un
complemento de autenticación externo, lo cual agrega una dependencia y una
superficie de configuración adicional sin necesidad real dentro del alcance
de este proyecto académico. La limitación aceptada —cualquier operador con
sesión JWT activa comparte la misma identidad MQTT `gui_operator`— se
documenta aquí explícitamente como una decisión de costo/beneficio, no como
un descuido.

### 5.4 Limitación de alcance: credenciales visibles en el código fuente

Tanto `firmware/config.py` como `gui/robot_script.js` contienen las
credenciales MQTT en texto plano dentro del código fuente. Para el
firmware, esto es consecuencia de que MicroPython no dispone de un
mecanismo estándar de variables de entorno equivalente al de un proceso de
servidor. Para la interfaz web, cualquier credencial embebida en JavaScript
servido al navegador es, por definición, visible para quien inspeccione el
código fuente de la página — no existe un mecanismo de "secreto de cliente"
verdaderamente oculto en una aplicación web sin backend intermediario para
esa operación puntual. Ambas limitaciones son aceptables en el alcance
declarado (identidades de *aplicación*, no de *persona*, en una red de
confianza) y quedan documentadas como tales.

### 5.5 Limitación de alcance: sin TLS

Ni el listener 1883 ni el 9001 utilizan TLS. Las credenciales viajan en
texto plano dentro de la red local de la demostración. Esto es aceptable en
el alcance declarado del proyecto (red local aislada, entorno académico
controlado) pero **no** sería aceptable en un despliegue fuera de una red
de confianza. Mitigación futura documentada: `listener 8883` con
`certfile`/`keyfile` (TLS) para el listener TCP, y `wss://` (WebSocket sobre
TLS) para el listener del navegador — trabajo futuro fuera del alcance de
esta entrega, no un vector ignorado por descuido.

### 5.6 Limitación de alcance: sin mitigación de fuerza bruta a nivel de broker

A diferencia del backend de autenticación (`backend/app/core/rate_limit.py`),
Mosquitto no limita por defecto la cantidad de intentos de conexión
fallidos por origen. En el alcance de este proyecto se acepta esta
limitación porque el broker no es alcanzable fuera de la red local de
confianza (ver §5.5) y porque el sistema de autenticación de operadores
humanos —donde sí importa este vector, al estar potencialmente expuesto a
una red más amplia— ya implementa dicha mitigación. Si el broker se
expusiera fuera de una red controlada, correspondería evaluar un mecanismo
de limitación de conexión (por ejemplo, a nivel de firewall o mediante un
proxy) antes de ese despliegue.

---

## 6. Checklist de seguridad aplicado (OWASP, resumen)

| # | Categoría OWASP | Estado en este broker |
|---|---|---|
| 1 | Control de acceso roto | Mitigado — ACL de mínimo privilegio por usuario y tópico (§4). |
| 2 | Fallas criptográficas | Parcial — hash de contraseñas mediante biblioteca auditada (§5.1); **sin TLS en tránsito** (limitación aceptada, §5.5). |
| 5 | Configuración de seguridad incorrecta | Mitigado — `allow_anonymous false` explícito, sin valores por defecto inseguros. |
| 6 | Componentes vulnerables | Pendiente de verificación periódica — fijar y auditar la versión de Mosquitto en uso antes de cada entrega. |
| 7 | Fallas de identificación y autenticación | Parcial — autenticación obligatoria por usuario/contraseña (§3.2); **sin límite de intentos de conexión a nivel de broker** (limitación aceptada, §5.6). |
| 9 | Fallas de logging y monitoreo | Mitigado — registro de conexiones y rechazos habilitado (§3.3). |

---

## 7. Puesta en marcha (Windows)

### 7.1 Arranque manual

```powershell
# Desde la carpeta mosquitto-broker/
mosquitto.exe -c mosquitto.conf -v
```

El flag `-v` (verbose) imprime en consola, además de escribir en
`logs/mosquitto.log` — útil durante el desarrollo para ver en vivo los
intentos de conexión rechazados por ACL o por credenciales inválidas.

### 7.2 Arranque automatizado

El script `ARRANCAR_SISTEMA.ps1`, en la raíz del repositorio, automatiza la
puesta en marcha completa del entorno de demostración: detiene cualquier
instancia previa de Mosquitto, inicia el proceso con este `mosquitto.conf`,
verifica que los puertos 1883 y 9001 queden efectivamente en escucha, y
luego levanta el backend de autenticación. Se recomienda su uso antes de
cada sesión de trabajo o defensa, en lugar del arranque manual, para
reducir el riesgo de olvidar algún paso de la secuencia.

### 7.3 Generar o regenerar credenciales

```powershell
python generar_passwd.py
```

El script sobrescribe por completo el archivo `passwd` con las entradas de
`esp32` y `gui_operator`. Si por algún motivo se prefiere generar las
credenciales con la herramienta oficial del proyecto Mosquitto (ver nota de
§5.1):

```powershell
mosquitto_passwd.exe -c passwd esp32
mosquitto_passwd.exe passwd gui_operator
```

El flag `-c` **crea** el archivo desde cero (sobrescribe cualquier usuario
existente) — usarlo solo para la primera credencial. El resto se agrega sin
`-c`, o el archivo se pierde.

En cualquiera de los dos casos, tras regenerar `passwd` es necesario
actualizar la contraseña correspondiente en `MQTT["password"]`
(`firmware/config.py`) y en `CFG.mqttPassword` (`gui/robot_script.js`) — no
existe sincronización automática entre el broker y sus clientes.

### 7.4 Verificación funcional (pruebas positivas y negativas)

```powershell
# Positiva: gui_operator puede leer robot/log
mosquitto_sub -h localhost -p 1883 -u gui_operator -P <password> -t robot/log

# Negativa: gui_operator NO debe poder escribir en robot/log (debe fallar/no publicar)
mosquitto_pub -h localhost -p 1883 -u gui_operator -P <password> -t robot/log -m "test"

# Negativa: cliente anónimo debe ser rechazado
mosquitto_sub -h localhost -p 1883 -t robot/log
```

Las pruebas negativas son tan importantes como las positivas: confirman que
la ACL efectivamente deniega, no solo que las reglas permitidas funcionan.
Esta misma secuencia sirve para confirmar la nota de verificación de §5.1:
si la prueba positiva con las credenciales generadas por
`generar_passwd.py` se conecta y autentica correctamente, el formato de
hash es compatible con la versión de Mosquitto instalada.

---

## 8. Referencias cruzadas

- Configuración del cliente ESP32: `firmware/config.py` (diccionario
  `MQTT`) y `firmware/mqtt.py` (función `connect_mqtt()`).
- Configuración del cliente de la interfaz: `gui/robot_script.js`, objeto
  `CFG`.
- Capa de autenticación de operador humano (independiente de este broker):
  `backend/app/core/security.py`, `backend/app/routers/auth.py`.
- Script de arranque completo del entorno: `ARRANCAR_SISTEMA.ps1` (raíz del
  repositorio).
- Decisiones de diseño ampliadas y proceso de verificación: `planificacion.md`
  (raíz del repositorio), sección correspondiente al broker privado.