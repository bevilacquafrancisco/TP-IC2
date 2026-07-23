# Diagramas Técnicos — Sistema Pick & Place (IC2)

> Proyecto: Brazo Robótico Pick & Place — Ingeniería en Computación II (UNRAF)
> Autor: Francisco Bevilacqua.



## Índice

1. [Diagrama de Flujo](#1-diagrama-de-flujo-flowchart) — lógica de decisión de sensor/modos + secuencia interna de `pick_and_place`
2. [Diagrama de Máquina de Estados](#2-diagrama-de-máquina-de-estados) — ciclo de vida operativo del firmware
3. [Diagrama de Secuencia](#3-diagrama-de-secuencia) — interacción completa Operador → GUI → Backend → Broker → ESP32
4. [Diagrama de Dependencias de Módulos](#4-diagrama-de-dependencias-de-módulos-component-diagram) — macro (sistema completo) y micro (firmware)
5. [Diagrama de Despliegue y Red](#5-diagrama-de-despliegue-y-red-deployment-diagram) — nodos físicos, procesos y protocolos

---

## 1. Diagrama de Flujo (Flowchart)

### Por qué estos dos flujos y no otros

Un flowchart documenta **un algoritmo paso a paso**, con sus puntos de decisión —
es la herramienta correcta para explicar *cómo* piensa el sistema ante un evento,
no *qué estados* atraviesa (eso es la máquina de estados, diagrama 2) ni *quién le
habla a quién* (eso es la secuencia, diagrama 3). Elegí el algoritmo más denso en
decisiones de negocio de todo el proyecto: **la lógica de `process_sensor_event()`**
(`firmware/commands.py`), porque es donde convergen los tres modos de operación
(MANUAL / SEMI_AUTO / AUTOMÁTICO) y las reglas de seguridad (`arm_busy`,
`pallet_full`). Se separó en dos diagramas para que cada uno quepa en una sola
captura de pantalla legible:

- **1A** — decisión de alto nivel: desde que el sensor confirma una detección hasta
  que se decide *si* y *hacia dónde* se dispara un `pick_and_place`.
- **1B** — el detalle interno de `pick_and_place(dest_pallet)` (`firmware/servos.py`):
  los 9 pasos mecánicos de la secuencia de recolección y depósito.

### 1A — Decisión de sensor y modos de operación

```mermaid
flowchart TD
    Start([Tick del loop principal]) --> ChkBusy{arm_busy?}
    ChkBusy -->|Sí| Skip([Saltar lectura de sensor este tick])
    ChkBusy -->|No| Debounce[poll_debounced acumula una muestra]
    Debounce --> ChkConfirm{5 muestras<br/>consecutivas?}
    ChkConfirm -->|No| Skip
    ChkConfirm -->|Sí, detección confirmada| PubSensor[mqtt_publish evento sensor detected]
    PubSensor --> ChkMode{state.mode actual}

    ChkMode -->|MANUAL| SoloLog[Solo se registra el evento<br/>ninguna acción automática]
    SoloLog --> Fin([Fin del ciclo])

    ChkMode -->|SEMI_AUTO| ChkPend{semi_pending<br/>ya estaba activo?}
    ChkPend -->|Sí| Fin
    ChkPend -->|No| SetPend[semi_pending = true]
    SetPend --> PubDet[mqtt_publish box_detected]
    PubDet --> Espera[[Esperar cmd semi_decision desde la GUI]]
    Espera --> ChkDest{destino elegido<br/>por el operador}
    ChkDest -->|P1| CallPP1[[pick_and_place dest=1<br/>ver Diagrama 1B]]
    ChkDest -->|P2| CallPP2[[pick_and_place dest=2<br/>ver Diagrama 1B]]
    ChkDest -->|ignorar| PubIgn[mqtt_publish box_ignored]
    PubIgn --> Fin
    CallPP1 --> Fin
    CallPP2 --> Fin

    ChkMode -->|AUTOMATICO| ChkP1{Pallet 1 lleno?}
    ChkP1 -->|No| CallPPA1[[pick_and_place dest=1<br/>ver Diagrama 1B]]
    ChkP1 -->|Sí| ChkP2{Pallet 2 lleno?}
    ChkP2 -->|No| CallPPA2[[pick_and_place dest=2<br/>ver Diagrama 1B]]
    ChkP2 -->|Sí, ambos llenos| PubFull[mqtt_publish all_pallets_full]
    PubFull --> Fin
    CallPPA1 --> Fin
    CallPPA2 --> Fin
```

**Decisiones que este diagrama justifica visualmente:**
- El guard `arm_busy` corta el flujo *antes* de leer el sensor — evita procesar una
  nueva detección mientras el brazo todavía resuelve la anterior (condición de
  carrera evitada por diseño, no por casualidad).
- El debounce de 5 muestras (`TIMING["sensor_debounce"]`) es una decisión de
  robustez de hardware (filtra ruido eléctrico), documentada en `sensor.py`.
- El modo MANUAL comparte el mismo camino de detección que los otros dos modos
  (telemetría siempre se publica), pero **deliberadamente no dispara ninguna
  acción automática** — el operador mantiene control total.
- AUTOMÁTICO prioriza Pallet 1 sobre Pallet 2 de forma determinística (regla de
  negocio explícita, no arbitraria).

### 1B — Secuencia interna de `pick_and_place(dest_pallet)`

```mermaid
flowchart TD
    In([pick_and_place dest_pallet]) --> ChkFull{pallet_full<br/>dest_pallet?}
    ChkFull -->|Sí| PubFull[mqtt_publish pallet_full]
    PubFull --> Ret1([return False])
    ChkFull -->|No| SetBusy[arm_busy = true]
    SetBusy --> Transito1["Paso 1: move_transito<br/>(levantar hombro/codo ANTES de girar)"]
    Transito1 --> IrRecol["Paso 2: ir a zona de recolección<br/>(base=180°, pinza abierta)"]
    IrRecol --> Cerrar["Paso 3: cerrar pinza<br/>(caja recolectada)"]
    Cerrar --> Transito2["Paso 4: move_transito<br/>(levantar brazo con carga)"]
    Transito2 --> Girar["Paso 5: girar base hacia<br/>el pallet destino"]
    Girar --> Bajar["Paso 6: bajar al nivel de apilado<br/>(pallet_count + 1)"]
    Bajar --> Abrir["Paso 7: abrir pinza<br/>(caja depositada)"]
    Abrir --> Transito3["Paso 8: move_transito<br/>(salir del pallet)"]
    Transito3 --> Volver["Paso 9: volver a<br/>zona de recolección"]
    Volver --> Incr[pallet_count += 1]
    Incr --> ChkMax{pallet_count >= 3?}
    ChkMax -->|Sí| MarkFull["pallet_full = true<br/>mqtt_publish pallet_full"]
    ChkMax -->|No| PubCollected[mqtt_publish box_collected]
    MarkFull --> PubCollected
    PubCollected --> ClearBusy[arm_busy = false]
    ClearBusy --> Ret2([return True])
```

**Por qué el "tránsito seguro" aparece 3 veces (pasos 1, 4 y 8):** no es
redundancia — cada tránsito ocurre en un momento mecánico distinto (antes de ir a
recolectar, después de agarrar la caja, y al salir del pallet), y cada uno previene
un accidente físico específico: tumbar una caja ya colocada en el pallet al girar
la base con el brazo bajo. Este es exactamente el tipo de decisión de diseño que
conviene señalar en la defensa como evidencia de que la secuencia no se escribió
"a prueba y error", sino con un análisis de riesgo mecánico explícito.

---

## 2. Diagrama de Máquina de Estados

### Por qué esta máquina y no una por "modo"

En vez de modelar tres máquinas de estado separadas (una por modo de operación),
se modela **una sola máquina compuesta**: un nivel superior de **conectividad**
(arrancando → conectando → online) y, dentro del estado `Online`, un nivel de
**operación** (inactivo / ejecutando movimiento / esperando decisión / bloqueado
por pallet lleno). Esto refleja fielmente cómo está implementado el firmware: la
lógica de negocio (`commands.py`) solo tiene sentido *dentro* de una sesión MQTT
activa, y el propio `main.py` reconecta automáticamente si esa condición deja de
cumplirse — modelarlo como un único diagrama jerárquico (estados compuestos UML)
es más preciso que tres diagramas planos desconectados entre sí.

```mermaid
stateDiagram-v2
    [*] --> Arrancando
    Arrancando --> ConectandoWiFi: init_servos() + init_sensor() OK
    ConectandoWiFi --> ConectandoWiFi: timeout, reintenta cada 500ms
    ConectandoWiFi --> ConectandoMQTT: WiFi conectado
    ConectandoMQTT --> ConectandoWiFi: WiFi caído durante el intento
    ConectandoMQTT --> Online: MQTT conectado\n+ evento 'online' publicado

    Online --> ConectandoWiFi: safe_poll() falla\n(WiFi/MQTT caído)

    state Online {
        [*] --> Inactivo

        Inactivo --> EjecutandoMovimiento: cmd servo / move\n(modo MANUAL)
        EjecutandoMovimiento --> Inactivo: evento move_done

        Inactivo --> EsperandoDecision: sensor detecta caja\n(modo SEMI_AUTO)
        EsperandoDecision --> EjecutandoPickPlace: cmd semi_decision\n(P1 o P2)
        EsperandoDecision --> Inactivo: cmd semi_decision\n(ignorar)

        Inactivo --> EjecutandoPickPlace: sensor detecta caja\n(modo AUTOMATICO)

        EjecutandoPickPlace --> Inactivo: evento box_collected
        EjecutandoPickPlace --> PalletLlenoBloqueado: pallet alcanzó\nMAX_CAJAS_PALLET
        PalletLlenoBloqueado --> Inactivo: cmd pallet_clear
    }

    Online --> [*]: excepción no manejada\n(bloque finally: apagado seguro)
```

**Puntos defendibles de este diagrama:**
- `EjecutandoMovimiento` y `EjecutandoPickPlace` son estados **mutuamente
  excluyentes** con `Inactivo` — el guard `arm_busy` en el código es, ni más ni
  menos, la variable booleana que materializa "¿estoy en uno de estos dos estados
  o no?". El diagrama de estados y esa única variable de `state.py` son la misma
  información en dos representaciones distintas.
- `PalletLlenoBloqueado` es un estado explícito y no un simple `if` disperso en el
  código: el sistema queda ahí hasta un `pallet_clear` externo, sin ninguna
  transición de timeout — es una decisión de negocio (requiere confirmación humana
  de que el pallet fue vaciado físicamente, no se asume automáticamente).
- El estado `Online → ConectandoWiFi` demuestra la resiliencia ante cortes de red:
  no hay ningún estado terminal de "error" del que el sistema no pueda recuperarse
  solo, salvo el apagado explícito (`[*]` final, correspondiente al bloque
  `finally` de `main.py`).

---

## 3. Diagrama de Secuencia

### Por qué un único diagrama de punta a punta

En vez de partirlo en "secuencia de login" + "secuencia de operación MQTT" por
separado, se armó **un solo diagrama continuo** que atraviesa los cinco
participantes reales del sistema (Operador, Navegador/GUI, Backend, Broker,
ESP32). La razón: la pregunta más común en una defensa de este tipo de proyecto es
*"mostrame de punta a punta qué pasa desde que abro el navegador hasta que la caja
cae en el pallet"* — y ese recorrido cruza las dos capas de seguridad (JWT +
MQTT/ACL) documentadas en los README de `backend/`, `gui/` y `mosquitto-broker/`.
Partirlo en dos diagramas rompería esa narrativa continua.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operador
    participant GUI as Navegador (GUI)
    participant BE as Backend FastAPI :8000
    participant MQ as Broker Mosquitto
    participant ESP as ESP32 (firmware)

    Note over ESP,MQ: El ESP32 ya está conectado y suscripto a robot/cmd (QoS 1)

    Op->>GUI: Abre login.html
    GUI->>BE: GET /health
    BE-->>GUI: 200 OK

    Op->>GUI: Ingresa usuario/contraseña, submit
    GUI->>BE: POST /auth/login {username, password}
    BE->>BE: rate_limit.is_rate_limited(ip)?
    BE->>BE: verify_password() contra hash bcrypt

    alt credenciales válidas
        BE->>BE: create_access_token(username)
        BE-->>GUI: 200 {access_token, expires_in_minutes}
        GUI->>GUI: sessionStorage.setItem(jwt)
        GUI->>Op: redirect a index.html
    else credenciales inválidas
        BE-->>GUI: 401 "Usuario o contraseña incorrectos"
        GUI->>Op: muestra error genérico (sin distinguir causa)
    end

    Op->>GUI: index.html carga (body.auth-pending)
    GUI->>GUI: requireSession()
    GUI->>BE: GET /auth/verify (Authorization Bearer)
    BE-->>GUI: 200 {username, valid:true}
    GUI->>GUI: remove .auth-pending → panel visible

    GUI->>MQ: CONNECT gui_operator/*** (WebSocket :9001)
    MQ-->>GUI: CONNACK
    GUI->>MQ: SUBSCRIBE robot/log (QoS 1)
    GUI->>MQ: PUBLISH robot/cmd {cmd:status}
    MQ->>ESP: forward status
    ESP-->>MQ: PUBLISH robot/log {event:status, pallets, servos, mode}
    MQ-->>GUI: forward status
    GUI->>Op: refresca sliders / pallets / badge de modo

    Note over ESP: Sensor KY-032 confirma detección (5 muestras consecutivas)
    ESP-->>MQ: PUBLISH robot/log {event:box_detected}
    MQ-->>GUI: forward box_detected
    GUI->>Op: muestra alerta "CAJA DETECTADA"

    Op->>GUI: Click "Pallet 1"
    GUI->>MQ: PUBLISH robot/cmd {cmd:semi_decision, dest:P1, msg_id} (QoS 1)
    MQ->>ESP: forward semi_decision

    ESP->>ESP: deduplicación por msg_id
    ESP->>ESP: pick_and_place(1) — 9 pasos (ver Diagrama 1B)
    ESP-->>MQ: PUBLISH robot/log {event:pick_start, dest:P1}
    MQ-->>GUI: forward pick_start
    GUI->>Op: botón Pallet 1 en estado "pending" (⏳)

    ESP-->>MQ: PUBLISH robot/log {event:box_collected, dest:P1, count:1}
    MQ-->>GUI: forward box_collected

    GUI->>GUI: updatePallet(1, 1, false)
    GUI->>GUI: resolvePending("semi-p1")
    GUI->>Op: refleja caja depositada en Pallet 1
```

**Detalles del protocolo que este diagrama hace explícitos:**
- El `alt/else` del login es la representación formal de la rama de éxito/fracaso
  descrita en `backend/README.md` (incluyendo la mitigación de timing attack: el
  camino de "credenciales inválidas" pasa igual por `verify_password()` con un
  hash *dummy*, aunque el diagrama simplifica ese detalle interno por legibilidad —
  se puede ampliar como nota si el docente pide el detalle exacto).
- El campo `msg_id` viaja en el mensaje 25/26 y se consume explícitamente en el
  mensaje 27 (`ESP->>ESP: deduplicación`) — la trazabilidad extremo a extremo de la
  garantía QoS 1 queda visible en una sola línea de tiempo.
- El `Note over ESP,MQ` inicial deja claro que el ESP32 no espera al operador para
  conectarse — es autónomo y ya está operativo antes de que exista sesión alguna en
  la GUI, coherente con que la GUI es un cliente más del broker, no un intermediario
  obligatorio para que el sistema funcione.

---

## 4. Diagrama de Dependencias de Módulos (Component Diagram)

### Por qué dos niveles (macro y micro)

Un componente único que mezclara "todo el sistema" con "cada archivo `.py` del
firmware" sería ilegible. Se separa en:

- **Macro**: los cuatro subsistemas del repositorio (`gui/`, `backend/`,
  `mosquitto-broker/`, `firmware/`) y cómo se conectan entre sí — para mostrar la
  arquitectura general del TP en una sola lámina.
- **Micro**: el grafo de dependencias interno del firmware modularizado — el punto
  más defendible del proyecto en términos de Ingeniería de Software (SRP aplicado
  a nivel de módulo, sin ciclos de import), documentado en detalle en
  `firmware/README.md`.

### 4A — Macro: componentes del sistema completo

```mermaid
flowchart LR
    subgraph Cliente["Cliente — Navegador Web"]
        direction TB
        login[login.html]
        indexHtml[index.html]
        authjs["auth.js<br/>única capa que conoce<br/>backend / JWT"]
        robotjs["robot_script.js<br/>lógica MQTT + control del robot"]
        login --> authjs
        indexHtml --> authjs
        indexHtml --> robotjs
    end

    subgraph Backend["Backend — FastAPI :8000"]
        direction TB
        mainpy["main.py<br/>app, CORS, exception handler"]
        authrouter["routers/auth.py"]
        config_be["core/config.py"]
        security_be["core/security.py"]
        deps_be["core/dependencies.py"]
        ratelimit_be["core/rate_limit.py"]
        schemas_be["schemas/auth_schemas.py"]

        mainpy --> authrouter
        authrouter --> deps_be
        authrouter --> security_be
        authrouter --> ratelimit_be
        authrouter --> config_be
        authrouter --> schemas_be
        deps_be --> security_be
        security_be --> config_be
    end

    subgraph Broker["Broker MQTT — Mosquitto :1883 / :9001"]
        direction TB
        conf_mq[mosquitto.conf]
        passwd_mq[passwd]
        acl_mq[acl.conf]
        conf_mq --> passwd_mq
        conf_mq --> acl_mq
    end

    subgraph Firmware["Firmware ESP32 — MicroPython"]
        direction TB
        main_fw[main.py]
        commands_fw[commands.py]
        servos_fw[servos.py]
        sensor_fw[sensor.py]
        mqtt_fw[mqtt.py]
        wifi_fw[wifi.py]
        state_fw[state.py]
        config_fw[config.py]

        main_fw --> commands_fw
        main_fw --> wifi_fw
        main_fw --> mqtt_fw
        main_fw --> servos_fw
        main_fw --> sensor_fw
        commands_fw --> state_fw
        commands_fw --> mqtt_fw
        commands_fw --> servos_fw
        commands_fw --> sensor_fw
        commands_fw --> wifi_fw
        servos_fw --> mqtt_fw
        servos_fw --> state_fw
        mqtt_fw --> wifi_fw
        mqtt_fw --> state_fw
        wifi_fw --> state_fw
    end

    robotjs -- "MQTT sobre WebSocket :9001<br/>usuario gui_operator" --> Broker
    authjs -- "HTTPS REST<br/>POST /auth/login, GET /auth/verify" --> Backend
    Firmware -- "MQTT sobre TCP :1883<br/>usuario esp32" --> Broker
```

### 4B — Micro: grafo de dependencias interno del firmware (sin ciclos)

```mermaid
flowchart BT
    config[config.py]
    state[state.py]

    wifi[wifi.py] --> config
    wifi --> state

    mqtt[mqtt.py] --> config
    mqtt --> state
    mqtt --> wifi

    sensor[sensor.py] --> config
    sensor --> state

    servos[servos.py] --> config
    servos --> state
    servos --> mqtt

    commands[commands.py] --> state
    commands --> mqtt
    commands --> servos
    commands --> sensor
    commands --> wifi

    main[main.py] --> config
    main --> state
    main --> wifi
    main --> mqtt
    main --> servos
    main --> sensor
    main --> commands
```

**Cómo leer este grafo (dirección `BT`, de abajo hacia arriba):** las flechas
salen del módulo que **depende** hacia el módulo del que **depende** — por eso
`config.py` y `state.py` quedan abajo de todo (son hojas, no dependen de nadie) y
`main.py` arriba de todo (conoce a todos los demás). La ausencia de una flecha
`mqtt.py → commands.py` es la prueba visual de que **no hay ciclo de imports**:
`commands.py` sí depende de `mqtt.py`, pero `mqtt.py` recibe las funciones de
`commands.py` como parámetros (`connect_mqtt(on_message_cb, status_cb)`), nunca
las importa — la técnica de inyección de dependencias documentada en
`firmware/mqtt.py` y en `firmware/README.md` (sección 2.1).

### 4C — Alternativa en PlantUML (notación UML formal con `<<component>>`)

```plantuml
@startuml Componentes_Sistema
skinparam componentStyle rectangle
skinparam wrapWidth 200
skinparam defaultTextAlignment center

package "Cliente (Navegador)" {
  [login.html] as login
  [index.html] as indexHtml
  [auth.js] as authjs
  [robot_script.js] as robotjs
  login --> authjs
  indexHtml --> authjs
  indexHtml --> robotjs
}

package "Backend FastAPI :8000" {
  [main.py] as mainpy
  [routers/auth.py] as authrouter
  [core/config.py] as configbe
  [core/security.py] as securitybe
  [core/dependencies.py] as depsbe
  [core/rate_limit.py] as ratelimitbe
  [schemas/auth_schemas.py] as schemasbe

  mainpy --> authrouter
  authrouter --> depsbe
  authrouter --> securitybe
  authrouter --> ratelimitbe
  authrouter --> configbe
  authrouter --> schemasbe
  depsbe --> securitybe
  securitybe --> configbe
}

package "Broker MQTT (Mosquitto)" {
  [mosquitto.conf] as mqconf
  [passwd] as mqpasswd
  [acl.conf] as mqacl
  mqconf --> mqpasswd
  mqconf --> mqacl
}

package "Firmware ESP32 (MicroPython)" {
  [main.py] as mainfw
  [commands.py] as commandsfw
  [servos.py] as servosfw
  [sensor.py] as sensorfw
  [mqtt.py] as mqttfw
  [wifi.py] as wififw
  [state.py] as statefw
  [config.py] as configfw

  mainfw --> commandsfw
  mainfw --> wififw
  mainfw --> mqttfw
  mainfw --> servosfw
  mainfw --> sensorfw
  commandsfw --> statefw
  commandsfw --> mqttfw
  commandsfw --> servosfw
  commandsfw --> sensorfw
  servosfw --> mqttfw
  mqttfw --> wififw
}

robotjs ..> mqacl : MQTT/WS :9001\nusuario gui_operator
authjs ..> authrouter : HTTPS REST + JWT
mainfw ..> mqconf : MQTT/TCP :1883\nusuario esp32

@enduml
```

---

## 5. Diagrama de Despliegue y Red (Deployment Diagram)

### Por qué importa distinguir "componente" de "despliegue"

El diagrama 4 responde *"quién depende de quién en el código"*. Este diagrama 5
responde una pregunta distinta: *"qué proceso corre en qué máquina física, en qué
puerto, y con qué protocolo de red"*. Acá se ve, por ejemplo, que **todos los procesos de
software (broker, backend, servidor estático, navegador) corren en la misma PC**,
mientras que el ESP32 es el único nodo físicamente distinto, conectado por WiFi.

### 5A — Mermaid

```mermaid
flowchart TB
    subgraph PC["PC del Operador — Windows 10/11 (misma LAN)"]
        direction TB
        proc_mosquitto["Proceso: mosquitto.exe<br/>:1883 TCP / :9001 WebSocket"]
        proc_uvicorn["Proceso: uvicorn (FastAPI)<br/>:8000 HTTP"]
        proc_static["Proceso: servidor estático GUI<br/>(Live Server) :5500"]
        browser["Navegador Web<br/>login.html / index.html"]

        browser -->|HTTP :5500| proc_static
        browser -->|HTTPS/HTTP REST :8000<br/>JWT| proc_uvicorn
        browser -->|MQTT sobre WebSocket :9001<br/>usuario gui_operator| proc_mosquitto
    end

    subgraph Red["Red WiFi Local"]
        router(("Router / Access Point"))
    end

    subgraph Campo["Dispositivo de Campo"]
        esp32["ESP32 NodeMCU<br/>Firmware MicroPython"]
        servos4["4x Servo SG90<br/>GPIO 25 / 26 / 27 / 32"]
        sensor_ky["Sensor KY-032<br/>GPIO 33"]
        esp32 ---|PWM| servos4
        esp32 ---|Digital IN| sensor_ky
    end

    proc_mosquitto <-->|MQTT sobre TCP :1883| router
    router <-->|WiFi 2.4GHz| esp32
    esp32 -.usuario esp32<br/>credenciales sha512_crypt.- proc_mosquitto
```

### 5B — Alternativa en PlantUML (notación UML de despliegue: `node` / `artifact`)

```plantuml
@startuml Despliegue_Red
skinparam wrapWidth 200
skinparam defaultTextAlignment center

node "PC del Operador\n(Windows 10/11)" as PC {
  node "Proceso: mosquitto.exe" as Broker {
    artifact "mosquitto.conf"
    artifact "passwd"
    artifact "acl.conf"
  }
  node "Proceso: uvicorn (FastAPI)" as Backend {
    artifact "app/main.py"
  }
  node "Proceso: servidor estático\n(Live Server :5500)" as StaticGUI {
    artifact "login.html"
    artifact "index.html"
  }
  node "Navegador Web" as Browser {
    artifact "auth.js"
    artifact "robot_script.js"
  }
}

node "Router / Access Point\n(Red WiFi Local)" as AP

node "ESP32 NodeMCU" as ESP {
  artifact "firmware/*.py\n(MicroPython)"
}
node "4x Servo SG90" as Servos
node "Sensor KY-032" as Sensor

ESP -down-> Servos : PWM\n(GPIO 25/26/27/32)
ESP -down-> Sensor : Digital IN\n(GPIO 33)

Browser -down-> StaticGUI : HTTP :5500
Browser -right-> Backend : HTTPS REST :8000\n(JWT)
Browser -down-> Broker : MQTT/WebSocket :9001\n(usuario gui_operator)
Broker -right-> AP : MQTT/TCP :1883
AP -right-> ESP : MQTT/TCP :1883\n(usuario esp32)

@enduml
```

**Puntos defendibles de este diagrama:**
- Los **cuatro procesos de software** (Mosquitto, uvicorn, servidor estático,
  navegador) corriendo en un único nodo físico (la PC) es una simplificación
  deliberada para una demo académica en LAN — en un despliegue de producción real
  cada uno podría vivir en una máquina/contenedor distinto, y el diagrama deja
  explícito ese punto de escalabilidad futura sin necesidad de implementarlo.
- El ESP32 es el **único nodo verdaderamente distribuido** del sistema — de ahí
  que sea también el único punto con seguridad de transporte reforzada por
  broker (usuario `esp32` con ACL de mínimo privilegio, ver
  `mosquitto-broker/README.md`).
- Los tres puertos distintos en la misma PC (`5500`, `8000`, `1883`/`9001`) están
  explícitos porque un desajuste de puerto es, en la práctica, el error más común
  al levantar la demo — este diagrama funciona también como checklist de arranque,
  coherente con `ARRANCAR_SISTEMA.ps1`.

---

## Resumen

| Pregunta... | Ver |
|---|---|
| "¿Cómo decide el robot qué hacer cuando detecta una caja?" | Diagrama 1A |
| "¿Cómo funciona mecánicamente el pick & place, paso a paso?" | Diagrama 1B |
| "¿Qué estados puede tener el sistema y cómo se recupera de una desconexión?" | Diagrama 2 |
| "Mostrar todo el flujo desde que abro el navegador hasta que se mueve el brazo" | Diagrama 3 |
| "¿Por qué modularizar el firmware así, y no hay riesgo de import circular?" | Diagrama 4B |
| "¿Cómo se relacionan los cuatro subsistemas del repositorio entre sí?" | Diagrama 4A |
| "¿Dónde corre cada cosa físicamente, y por qué puerto se comunica?" | Diagrama 5 |