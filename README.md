# 🤖 Sistema Pick & Place — Brazo Robótico Industrial IoT

**Trabajo Práctico Final — Ingeniería en Computación II**   
Universidad Nacional de Rafaela (UNRAF)   
Autor: Francisco Bevilacqua

---

## Índice

1. [Descripción general](#descripción-general)
2. [Arquitectura del sistema](#arquitectura-del-sistema)
3. [Stack tecnológico](#stack-tecnológico)
4. [Estructura del repositorio](#estructura-del-repositorio)
5. [Comunicación bidireccional vía MQTT](#comunicación-bidireccional-vía-mqtt)
6. [Módulo 1 — Broker MQTT (Mosquitto privado)](#módulo-1--broker-mqtt-mosquitto-privado)
7. [Módulo 2 — Firmware (ESP32 / MicroPython)](#módulo-2--firmware-esp32--micropython)
8. [Módulo 3 — Backend de autenticación (FastAPI + JWT)](#módulo-3--backend-de-autenticación-fastapi--jwt)
9. [Módulo 4 — GUI (Panel de control web)](#módulo-4--gui-panel-de-control-web)
10. [Seguridad: visión integral del sistema](#seguridad-visión-integral-del-sistema)
11. [Puesta en marcha](#puesta-en-marcha)
12. [Modos de operación](#modos-de-operación)
13. [Limitaciones conocidas y trabajo futuro](#limitaciones-conocidas-y-trabajo-futuro)
14. [Documentación adicional](#documentación-adicional)

---

## Descripción general

Este proyecto implementa un **sistema de control industrial IoT completo** para un brazo
robótico pick & place de 4 grados de libertad, desarrollado como trabajo final de la
materia Ingeniería en Computación II.

El sistema integra tres capas independientes que se comunican entre sí de forma
**bidireccional y en tiempo real** mediante el protocolo **MQTT**:

- Un **microcontrolador ESP32** que gobierna físicamente el brazo (servomotores + sensor
  de proximidad) y publica telemetría/eventos.
- Un **broker MQTT privado** (Mosquitto) que actúa como intermediario autenticado entre el
  firmware y la interfaz de usuario.
- Una **GUI web** que un operador humano usa para controlar el brazo en tres modos
  distintos, protegida por un **backend de autenticación con JWT**.

El requisito central de la cátedra —protocolo MQTT como capa de comunicación, con
intercambio bidireccional de mensajes entre el microcontrolador y el usuario a través de
una interfaz gráfica— es el eje de todo el diseño: **cada decisión de arquitectura gira
en torno a garantizar que ese canal sea confiable, ordenado y seguro**, no solo funcional.

---

## Arquitectura del sistema

```
┌─────────────────┐        MQTT (TCP:1883)        ┌──────────────────────┐
│   ESP32          │ ◄─────────────────────────►  │                       │
│  (MicroPython)    │   robot/cmd  (sub, QoS 1)     │   Mosquitto Broker    │
│  Firmware v4.0    │   robot/log  (pub, QoS 0)     │   (privado, con auth) │
└─────────────────┘                                │   ACL por usuario      │
                                                    └──────────┬────────────┘
                                                               │ MQTT sobre
                                                               │ WebSocket (9001)
                                                               ▼
┌──────────────────┐      HTTP/JSON (REST)         ┌──────────────────────┐
│   Backend Auth     │ ◄─────────────────────────►  │       GUI Web          │
│   FastAPI + JWT     │   POST /auth/login            │  login.html            │
│   bcrypt + rate-     │   GET  /auth/verify           │  index.html            │
│   limit + CORS       │                               │  auth.js + robot_      │
└──────────────────┘                                │  script.js             │
                                                    └──────────────────────┘
                                                          Operador humano
```

Son **dos planos de autenticación independientes y deliberadamente desacoplados**:

| Plano | Qué autentica | Mecanismo | Dónde vive |
|---|---|---|---|
| Aplicación | Al **operador humano** frente al panel de control | JWT (login con usuario/contraseña) | Backend FastAPI |
| Transporte | A **la GUI y al ESP32 como clientes** frente al broker | Usuario/contraseña MQTT + ACL | Mosquitto |

Esta separación es intencional (ver [ADR-06 en `auth.js`](src/gui/auth.js)): si el
mecanismo de autenticación de operadores cambiara mañana (por ejemplo, SSO institucional),
solo se reescribe la capa de aplicación — la capa de transporte MQTT permanece intacta.

---

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Firmware | MicroPython v1.20.0 sobre ESP32 |
| Comunicación | MQTT 3.1.1 (TCP y WebSocket) |
| Broker | Eclipse Mosquitto (Windows, instancia privada) |
| Backend | Python 3 + FastAPI + Uvicorn |
| Autenticación | JWT (HS256, `python-jose`) + bcrypt (`passlib`) |
| Frontend | HTML5 + CSS3 + JavaScript vanilla (sin frameworks) |
| Cliente MQTT (browser) | Paho MQTT JS (vía WebSocket) |
| Configuración | Pydantic Settings (`.env`) |

---

## Estructura del repositorio

```
TP-IC2/
├── docs/
│   └── BevilacquaFrancisco_IC-II.docx     # Informe técnico completo de la cátedra
├── src/
│   ├── backend/                            # API de autenticación (FastAPI)
│   │   ├── app/
│   │   │   ├── core/                       # config, security, rate_limit, dependencies
│   │   │   ├── routers/                    # endpoints /auth/*
│   │   │   ├── schemas/                    # modelos Pydantic de request/response
│   │   │   └── main.py                     # entry point de la app
│   │   ├── scripts/
│   │   │   └── generar_hash_password.py    # utilidad CLI para generar hashes bcrypt
│   │   ├── .env.example                    # plantilla de configuración (sin secretos)
│   │   ├── generar_passwd.py               # utilidad para el archivo passwd de Mosquitto
│   │   └── requirements.txt
│   ├── firmware/                           # Firmware ESP32 (MicroPython, modular)
│   │   ├── main.py / robot_main.py         # entry point
│   │   ├── config.py                       # pines, WiFi, credenciales MQTT
│   │   ├── mqtt.py                         # cliente MQTT + reconexión
│   │   ├── servos.py / sensor.py           # control de hardware
│   │   ├── commands.py                     # router de comandos entrantes
│   │   └── state.py / wifi.py
│   ├── gui/                                # Panel de control web
│   │   ├── login.html / login_script.js / login_style.css
│   │   ├── index.html
│   │   ├── auth.js                         # sesión JWT (login, verify, guard)
│   │   ├── robot_script.js                 # cliente MQTT + lógica de control
│   │   └── robot_style.css
│   ├── mosquitto-broker/
│   │   ├── mosquitto.conf                  # listeners TCP/WS, auth obligatoria
│   │   └── acl.conf                        # control de acceso por usuario/tópico
│   └── ARRANCAR_SISTEMA.ps1                # script de arranque para Windows
├── .gitignore
└── README.md
```

---

## Comunicación bidireccional vía MQTT

Toda la interacción de control ocurre sobre dos tópicos, con roles de publicador/suscriptor
invertidos entre sí — es decir, un canal genuinamente bidireccional, no un simple
request/response disfrazado:

| Tópico | Publica | Suscribe | Contenido |
|---|---|---|---|
| `robot/cmd` | GUI (operador) | ESP32 | Comandos: `set_mode`, `servo`, `move`, `semi_decision`, `pallet_clear`, `status` |
| `robot/log` | ESP32 | GUI | Eventos: `online`, `status`, `sensor`, `box_detected`, `box_collected`, `pallet_full`, `error`, `offline`, etc. |

### Calidad de servicio (QoS) y confiabilidad

- **`robot/cmd`** se suscribe con **QoS 1** en el firmware y `clean_session=False`: si el
  ESP32 estuvo offline, el broker retiene y reentrega los comandos pendientes al
  reconectar.
- Los comandos de acción crítica (`set_mode`, `move`, `semi_decision`, `pallet_clear`) se
  publican con **QoS 1** desde la GUI. Los comandos de alta frecuencia (`servo`, `status`)
  usan **QoS 0** — el valor más reciente siempre reemplaza al anterior, y no vale la pena
  el overhead de confirmación.
- **Deduplicación por `msg_id`**: como QoS 1 garantiza entrega *al menos una vez* (no
  *exactamente una vez*), cada comando crítico incluye un identificador único
  (`timestamp + random`). Tanto el firmware como la GUI descartan mensajes repetidos con
  el mismo `msg_id`, evitando que, por ejemplo, una caja se deposite dos veces por un
  reenvío del broker.
- **Re-sincronización activa**: al reconectar (de cualquiera de los dos lados), se solicita
  un `status` completo en lugar de esperar el próximo heartbeat (hasta 20 s), para que la
  GUI y el ESP32 nunca queden con estados desincronizados tras un corte.
- **Watchdog de actividad en la GUI**: dos temporizadores independientes distinguen entre
  "ESP32 online pero sin tráfico reciente" (informativo) y "conexión perdida" (que dispara
  la detección de offline en ~3 s en vez de esperar un timeout largo).

---

## Módulo 1 — Broker MQTT (Mosquitto privado)

El broker corre **localmente** (`src/mosquitto-broker/`), reemplazando al broker público
`test.mosquitto.org` usado en prototipos tempranos del proyecto. Esta migración fue el
primer hito de la refactorización final, porque un broker público sin autenticación es
inviable para cualquier escenario que no sea una prueba de concepto trivial.

Características de la configuración (`mosquitto.conf` + `acl.conf`):

- **Doble listener**: TCP estándar (1883) para el ESP32, y WebSocket (9001) para que el
  navegador —que no puede abrir sockets TCP crudos— se conecte desde la GUI.
- **`allow_anonymous false`**: ningún cliente se conecta sin credenciales.
- **Dos usuarios de aplicación**, generados con `mosquitto_passwd` (hash, nunca texto
  plano en el archivo `passwd`):
  - `esp32` → usado exclusivamente por el firmware.
  - `gui_operator` → usado exclusivamente por la GUI.
- **ACL de mínimo privilegio** por usuario y tópico: `esp32` solo puede publicar en
  `robot/log` y suscribirse a `robot/cmd`; `gui_operator`, exactamente al revés. Ningún
  cliente tiene permisos más amplios que los que su rol requiere.
- El script `generar_passwd.py` (en `src/backend/`) es una utilidad de apoyo para generar
  el archivo de credenciales del broker durante la puesta en marcha en Windows.

> **Nota de alcance declarada**: esta instancia no usa TLS (MQTT sobre TCP/WS en texto
> plano dentro de la LAN de la demo). Es una limitación aceptada y documentada, no un
> descuido — ver sección de [Limitaciones conocidas](#limitaciones-conocidas-y-trabajo-futuro).

---

## Módulo 2 — Firmware (ESP32 / MicroPython)

El firmware (`src/firmware/`) controla el hardware físico y actúa como cliente MQTT
autenticado. Estructura modular (evolución del `robot_main.py` monolítico original,
conservado como referencia):

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Punto de entrada: inicialización de hardware, WiFi, MQTT, loop principal |
| `config.py` | Pines GPIO, credenciales WiFi/MQTT, posiciones calibradas, timings |
| `wifi.py` | Conexión y reconexión de WiFi |
| `mqtt.py` | Cliente MQTT (conexión, suscripción QoS 1, publicación, reconexión) |
| `servos.py` | Control PWM de los 4 servomotores SG90 (movimiento suave por pasos) |
| `sensor.py` | Lectura del sensor infrarrojo KY-032 con debounce |
| `commands.py` | Router de comandos entrantes desde `robot/cmd` + deduplicación |
| `state.py` | Estado global: modo activo, pallets, brazo ocupado, contadores |

### Hardware controlado

- **4x servomotores SG90** (Base, Hombro, Codo, Pinza) sobre GPIO 25/26/27/32, alimentados
  externamente a 5V/2A (nunca desde el 3.3V del ESP32).
- **1x sensor KY-032** (detector IR de obstáculos) sobre GPIO 33, con pull-up interno y
  debounce por muestras consecutivas para evitar falsos positivos.

### Robustez del firmware

- **Watchdog Timer (WDT)** configurable, deshabilitado en desarrollo (Thonny) y habilitado
  en producción.
- **`clean_session=False` + suscripción QoS 1**: ningún comando se pierde si el ESP32
  estuvo momentáneamente desconectado.
- **Deduplicación por `msg_id`** en el callback `on_message`, simétrica a la de la GUI.
- **Reset causes diagnosticados**: el firmware reporta la causa del último reinicio
  (power-on, watchdog, brownout, etc.) al reconectar, útil para detectar problemas de
  alimentación durante la demo.
- **GC periódico y monitoreo de memoria libre**, reportado en la telemetría.

---

## Módulo 3 — Backend de autenticación (FastAPI + JWT)

El backend (`src/backend/`) es una API mínima cuya única responsabilidad es autenticar al
**operador humano** frente a la GUI — no participa en el tráfico MQTT.

### Endpoints

| Endpoint | Método | Descripción |
|---|---|---|
| `/auth/login` | `POST` | Valida usuario/contraseña, emite JWT |
| `/auth/verify` | `GET` | Valida un JWT existente (usado al recargar `index.html`) |
| `/health` | `GET` | Healthcheck sin autenticación, usado por `login.html` |

### Diseño de seguridad del backend (detalle por capa)

- **Hashing de contraseñas con bcrypt** (`passlib`), nunca texto plano ni algoritmos
  débiles (MD5/SHA1). Las contraseñas se cargan como hashes en la variable de entorno
  `AUTH_USERS`, generados con `scripts/generar_hash_password.py` (usa `getpass`, nunca
  quedan en el historial de la shell).
- **JWT firmado con HMAC-SHA256**, claims estándar RFC 7519 (`sub`, `iat`, `exp`), con
  `timezone.utc` explícito para evitar ambigüedades de horario servidor/cliente.
- **Rate limiting en memoria** sobre `/auth/login`: máximo 5 intentos fallidos por IP en
  una ventana de 60 s (mitiga fuerza bruta — OWASP #7). Solo cuenta intentos fallidos; un
  login exitoso no penaliza a un operador que se equivocó una vez.
- **Mitigación de enumeración de usuarios**: si el usuario no existe, igual se ejecuta una
  verificación bcrypt contra un hash *dummy*, para que el tiempo de respuesta sea
  indistinguible del caso "usuario válido, contraseña incorrecta" (mitigación de timing
  attack). El mensaje de error es siempre genérico ("Usuario o contraseña incorrectos").
- **Fail-fast en la configuración**: si falta `JWT_SECRET_KEY` o `AUTH_USERS`, el proceso
  no arranca — nunca lo hace con un secreto de ejemplo o sin usuarios cargados.
- **CORS restringido** a los orígenes declarados en `CORS_ORIGINS` (nunca `"*"`).
- **Manejo global de excepciones**: cualquier error no controlado devuelve un 500 genérico
  al cliente; el detalle real (stack trace) solo se escribe en el log del servidor, nunca
  se expone.
- **`/docs` y `/redoc` deshabilitables** vía `DEBUG_MODE=False` para no exponer la
  superficie completa de la API en un despliegue no controlado.

---

## Módulo 4 — GUI (Panel de control web)

La interfaz (`src/gui/`) es HTML/CSS/JS vanilla, sin frameworks ni build step, pensada para
correr como archivos estáticos servidos localmente.

### Flujo de uso

1. `login.html` verifica en vivo el estado del backend (`/health`) antes de que el
   operador intente loguearse, para distinguir "backend caído" de "credenciales
   incorrectas".
2. Al loguearse, `auth.js` guarda el JWT en **`sessionStorage`** (no `localStorage`): se
   borra automáticamente al cerrar la pestaña, reduciendo la ventana de exposición.
3. `index.html` ejecuta `requireSession()` de forma **síncrona respecto al pintado del
   panel**: el panel de control permanece oculto (`.auth-pending`) hasta confirmar la
   sesión contra el backend. Si no hay sesión válida, redirige a `login.html` y el resto
   del script nunca se ejecuta — la conexión MQTT jamás se abre sin sesión confirmada.
4. Una vez autenticado, `robot_script.js` toma el control: conecta al broker MQTT vía
   WebSocket con las credenciales de aplicación (`gui_operator`), suscribe a `robot/log`
   y habilita los controles del panel.

### Separación de responsabilidades (`auth.js` vs `robot_script.js`)

`auth.js` es la **única** parte de la GUI que conoce el backend REST y el formato JWT.
`robot_script.js` no importa librerías de JWT ni sabe que existe un backend HTTP — solo
consume las funciones públicas expuestas (`requireSession`, `getOperatorName`, `logout`).
Si el mecanismo de autenticación cambiara, solo `auth.js` se reescribe.

### Tres modos de control (ver [Modos de operación](#modos-de-operación))

El panel expone control manual por slider, movimientos preconfigurados, un modo
semi-automático con decisión del operador ante cada caja detectada, y un modo totalmente
automático.

### Resiliencia de la UI

- **Persistencia de estado de pallets** en `localStorage`, restaurada al recargar la
  página (sobrevive a un refresh, no a un cambio de contraseña de sesión).
- **Pending state en botones críticos**: al enviar un comando QoS 1, el botón queda
  deshabilitado con indicador visual hasta recibir confirmación del ESP32, con timeout de
  seguridad de 8 s para no quedar bloqueado si la confirmación se pierde.
- **Bloqueo de pallets llenos** en modo semi-automático: si el ESP32 reporta un pallet
  lleno, el botón correspondiente se deshabilita hasta recibir `pallet_cleared`, evitando
  enviar una decisión que el firmware rechazaría silenciosamente.

---

## Seguridad: visión integral del sistema

La seguridad se trató como **propiedad transversal**, no como una fase final agregada al
código. Resumen por capa (OWASP Top 10 como referencia):

| Capa | Amenaza mitigada | Mecanismo aplicado |
|---|---|---|
| MQTT | Acceso no autorizado al broker | `allow_anonymous false` + usuario/contraseña por cliente |
| MQTT | Escalación de privilegios entre roles | ACL de mínimo privilegio (`esp32` y `gui_operator` con permisos disjuntos) |
| Backend | Fuerza bruta sobre login | Rate limiting por IP (5 intentos / 60 s) |
| Backend | Enumeración de usuarios válidos | Verificación bcrypt contra hash *dummy* + mensaje de error genérico |
| Backend | Contraseñas comprometidas en reposo | Hashing bcrypt (nunca texto plano ni hashes débiles) |
| Backend | Secretos hardcodeados | Todo secreto vive en `.env` (excluido de Git); config fail-fast si falta algo |
| Backend | CSRF / origen no autorizado | CORS restringido a orígenes explícitos |
| Backend | Fuga de información en errores | Handler global de excepciones con mensaje 500 genérico; detalle solo en logs de servidor |
| GUI | Robo de sesión persistente | JWT en `sessionStorage`, no `localStorage` |
| GUI | Acceso al panel sin sesión válida | Guard síncrono `requireSession()` antes de renderizar el panel |
| Git/Repo | Filtración de secretos | `.gitignore` excluye `.env`, `passwd`, entornos virtuales y artefactos de build |

### Decisiones de alcance documentadas (no descuidos)

Como corresponde a una entrega académica defendible, las limitaciones de seguridad
aceptadas están **declaradas explícitamente**, no ocultas:

- Sin TLS en el broker MQTT ni en el backend HTTP (tráfico en texto plano dentro de la LAN
  de la demo).
- Las credenciales MQTT de la GUI (`gui_operator`) son visibles en el código fuente
  JavaScript del navegador. Esto es aceptable porque identifican **a la aplicación GUI**
  ante el broker (capa de transporte), no al operador humano individual — ese control de
  acceso por persona vive en la capa de aplicación (JWT), que sí es secreta y personal.
- Rate limiting en memoria de un solo proceso (no distribuido): válido para un backend
  uvicorn de instancia única sirviendo una demo en LAN; no escalaría a múltiples workers
  sin migrar a un store compartido (Redis).
- Sin refresh tokens: la sesión JWT expira y requiere nuevo login, sin renovación
  silenciosa — simplificación deliberada para el alcance de esta entrega.

---

## Puesta en marcha

### 1. Broker MQTT (Mosquitto)

```powershell
# Instalar Mosquitto para Windows y ubicar mosquitto.conf / acl.conf
# (ver src/mosquitto-broker/) en la carpeta de instalación.

# Generar el archivo de credenciales del broker:
mosquitto_passwd -c passwd esp32
mosquitto_passwd passwd gui_operator

# Levantar el broker con la configuración del proyecto:
mosquitto -c mosquitto.conf -v
```

### 2. Backend de autenticación

```bash
cd src/backend
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt

copy .env.example .env           # completar JWT_SECRET_KEY y AUTH_USERS

# Generar el hash bcrypt de cada operador:
python scripts/generar_hash_password.py

# Levantar el backend:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verificar que responde en `http://localhost:8000/health`.

### 3. Firmware (ESP32)

1. Abrir `src/firmware/` en Thonny (o el IDE preferido con soporte MicroPython).
2. Editar `config.py`: SSID/contraseña de WiFi, IP del broker, credenciales MQTT (`esp32`).
3. Cargar los módulos al ESP32 y ejecutar `main.py`.
4. Confirmar en la consola serie que WiFi y MQTT conectan correctamente.

### 4. GUI

1. Editar `AUTH_CFG.apiBase` en `auth.js` y `CFG.broker` en `robot_script.js` con las
   IPs reales de la demo (por defecto apuntan a `localhost` / la IP de la LAN de pruebas).
2. Servir `src/gui/` como contenido estático (Live Server, `python -m http.server`, o
   similar) y abrir `login.html`.

### Arranque combinado

`src/ARRANCAR_SISTEMA.ps1` automatiza el levantamiento del broker y el backend en Windows
para no tener que repetir los pasos manuales en cada demo.

---

## Modos de operación

| Modo | Comportamiento |
|---|---|
| **Manual** | El operador controla cada servo individualmente con sliders, o dispara movimientos preconfigurados (home, recolectar, abrir/cerrar pinza) |
| **Semi-automático** | El sensor detecta una caja → la GUI alerta al operador → el operador decide el destino (Pallet 1, Pallet 2 o ignorar) |
| **Automático** | El sensor detecta una caja → el sistema ejecuta el pick & place sin intervención, llenando primero Pallet 1 y luego Pallet 2; si ambos están llenos, se detiene hasta que el operador vacíe uno vía GUI |

---

## Limitaciones conocidas y trabajo futuro

- Migrar el broker y el backend a TLS para uso fuera de una LAN controlada.
- Externalizar las credenciales MQTT del firmware a un archivo `secrets.py` separado
  (MicroPython no tiene un mecanismo estándar de variables de entorno como Python de
  servidor).
- Rate limiting distribuido (Redis) si el backend escalara a múltiples workers.
- Refresh tokens para evitar que el operador deba reloguearse al expirar la sesión.

---

## Documentación adicional

El informe técnico completo entregado a la cátedra —con especificación de requerimientos,
decisiones de arquitectura (ADRs), diagramas de pines, protocolo de comandos y resultados
de pruebas— está disponible en [`docs/BevilacquaFrancisco_IC-II.docx`](docs/BevilacquaFrancisco_IC-II.docx).