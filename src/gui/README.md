# `src/gui/` — Panel de Control Web (HTML + CSS + JavaScript vanilla)

> Proyecto: Brazo Robótico Pick & Place — Ingeniería en Computación II (UNRAF)  
> Autor: Francisco Bevilacqua  
> Versión documentada: Fase 3 — GUI con autenticación de operador integrada  
> Rol en el sistema: interfaz humano-máquina (HMI) que traduce clics/sliders en
> comandos MQTT hacia la ESP32, y eventos MQTT del ESP32 en estado visual.

---

## 1. Panorama general

La GUI es una aplicación web **sin build step ni framework** (HTML + CSS + JS
vanilla, sin React/Vue/npm): se sirve como archivos estáticos y corre enteramente en
el navegador del operador. Se conecta a dos servicios backend independientes, cada
uno resolviendo una preocupación distinta (ver también `backend/README.md`, sección
1, para la justificación completa de por qué son dos sistemas separados):

```
┌───────────────┐   HTTP (REST + JWT)   ┌──────────────────────┐
│  login.html   │ ────────────────────► │  backend/ (FastAPI)  │  ¿quién sos vos?
│  index.html   │ ◄──────────────────── │  puerto 8000         │
└───────┬───────┘                       └──────────────────────┘
        │
        │  MQTT sobre WebSocket (Paho MQTT)
        ▼
┌──────────────────────┐
│ mosquitto-broker/     │  ¿qué le decís al robot?
│ puerto 9001 (WS)      │
└───────────┬───────────┘
            │  MQTT sobre TCP
            ▼
      ┌──────────┐
      │  ESP32    │
      │ firmware/ │
      └──────────┘
```

## 2. Estructura del directorio

```
gui/
├── login.html          # Pantalla de login — punto de entrada real de la aplicación
├── login_script.js     # Lógica de la pantalla de login
├── login_style.css     # Estilos específicos del login (hereda variables de robot_style.css)
├── index.html           # Panel de control principal — protegido por sesión
├── robot_script.js      # Lógica del panel: MQTT + control del robot + integración de sesión
├── auth.js               # Módulo de autenticación — único archivo que conoce el backend/JWT
├── robot_style.css       # Hoja de estilos compartida por login.html e index.html
└── parches/              # Historial de parches incrementales aplicados durante Fase 3
```

### 2.1 Por qué `login.html` es el punto de entrada, no `index.html`

Un operador que abre la GUI por primera vez debe entrar por `login.html`. Si en
cambio abriera `index.html` directamente sin sesión, el *guard* de sesión
(`requireSession()`, ver sección 4) lo redirige automáticamente a `login.html` antes
de que el panel se vuelva interactivo — así que técnicamente da igual por dónde
"entre" el operador, pero `login.html` es el flujo esperado y el que documenta
`ARRANCAR_SISTEMA.ps1` (`http://localhost:5500/login.html`).

### 2.2 `parches/`

Esta carpeta conserva los archivos de parche incrementales que se fueron aplicando
sobre la GUI durante la Fase 3 del proyecto (integración de `auth.js` y del guard de
sesión sobre la GUI ya existente de las fases anteriores). Se mantienen como
**evidencia de proceso de desarrollo incremental**.

---

## 3. Los dos archivos HTML: qué hace cada uno

### 3.1 `login.html`

Formulario simple (usuario + contraseña) con:

- **Indicador de estado del backend** (`#backend-status`), verificado en vivo al
  cargar la página contra `GET /health` (ver `login_script.js`,
  `checkBackendStatus()`). Reusa visualmente el patrón `.badge` del panel principal.
  Existe para que, si el backend FastAPI no está corriendo, el operador vea
  inmediatamente "Backend Auth: No disponible" en vez de intentar loguearse y recibir
  un error de conectividad confuso sin contexto — un problema de UX real detectado
  durante las pruebas de la demo (arrancar los tres servicios en el orden correcto es
  el paso que más falla en una presentación en vivo).
- **Área de error** (`#login-error`) que muestra el mensaje **exacto** devuelto por
  el backend, sin reinterpretarlo (ver sección 5.3 sobre por qué esto es una decisión
  de seguridad, no solo de UX).
- Carga `robot_style.css` **antes** que `login_style.css`: `login_style.css` no
  redefine ninguna variable CSS propia (`--bg-dark`, `--color-primary`,
  `--f-display`, etc.) — todas provienen de `:root` en `robot_style.css`. Esto
  garantiza coherencia visual total entre login y panel: si mañana cambia la paleta
  del proyecto, se edita un solo archivo y ambas pantallas la heredan.

### 3.2 `index.html`

El panel de control real. Diferencias clave respecto a versiones anteriores del
proyecto (previas a la Fase 3):

- El `<body>` arranca con la clase `auth-pending`, que en `robot_style.css` oculta
  `.layout`, `.footer` y `.mode-selector` mediante `visibility: hidden` (ver 4.3) —
  el panel completo permanece invisible hasta que `robot_script.js` confirma una
  sesión válida y remueve esa clase.
- Se agregó un badge `#badge-operator` en el header, con el nombre del operador
  autenticado y un botón de logout (`⏏`) que llama a `logout()` (expuesta por
  `auth.js`).
- Carga `auth.js` **antes** que `robot_script.js` — el orden importa: `auth.js`
  define `requireSession()`, que `robot_script.js` necesita invocar como primera
  operación de `window.onload` (ver 4.2).
- El footer ya no muestra `test.mosquitto.org:8080` (broker público de versiones
  anteriores) sino `192.168.x.x:9001 (privado)` y agrega una línea `Auth: JWT + MQTT
  ACL`, documentando de un vistazo las dos capas de seguridad activas.

---

## 4. `auth.js` — el único módulo que conoce el backend

### 4.1 Responsabilidad única y por qué está aislado

`auth.js` es deliberadamente el **único** archivo de la GUI que sabe que existe un
backend FastAPI, conoce la forma de un JWT, o llama a `fetch()` contra `/auth/*`.
`robot_script.js` (la lógica de control del robot) **no** importa `jose`, no
decodifica tokens, no sabe que `localhost:8000` existe — solo consume 4 funciones
públicas expuestas al final de `auth.js`: `getToken()`, `getOperatorName()`,
`requireSession()`, `logout()`.

Esta separación es deliberada (documentada como ADR-06 en `planificacion.md`): si el
día de mañana el mecanismo de autenticación cambiara por completo (por ejemplo, un
SSO institucional de la UNRAF), **solo `auth.js` se reescribiría** — toda la lógica
de control del brazo robótico en `robot_script.js` quedaría intacta, porque nunca
dependió de los detalles internos de cómo se obtiene o valida una sesión.

### 4.2 Guard de sesión — `requireSession()`

```javascript
window.onload = async () => {
    const operatorName = await requireSession();   // bloquea hasta confirmar sesión
    document.body.classList.remove('auth-pending'); // recién ahora se revela el panel
    // ... connectMQTT() se llama DESPUÉS, nunca antes
};
```

Estrategia de dos pasos, documentada en el propio código:

1. **Chequeo local instantáneo** (`_isLocallyExpired()`): compara `Date.now()`
   contra un timestamp de expiración calculado al momento del login y guardado en
   `sessionStorage`. Si ya venció (o no hay token), redirige inmediatamente a
   `login.html` **sin esperar ninguna respuesta de red** — mejor UX para el caso más
   común (sesión vencida hace rato).
2. **Confirmación remota** (`verifySessionRemote()`): si el chequeo local pasa, igual
   se llama a `GET /auth/verify` contra el backend antes de considerar la sesión
   definitivamente válida. Esto es la única verificación de seguridad real — el
   chequeo local es una optimización de UX, no una garantía, porque tanto el reloj
   del navegador como el propio `sessionStorage` pueden manipularse desde DevTools.

**Fail-closed, no fail-open**: si el backend es inalcanzable durante la verificación
remota (`catch` de la llamada `fetch`), `verifySessionRemote()` retorna `null` — se
trata como sesión inválida, no como sesión válida por defecto. Dejar pasar al
operador cuando no se puede confirmar nada sería el patrón de seguridad incorrecto
(fail-open); acá se prioriza negar el acceso ante la duda.

`requireSession()`, si la sesión no es válida, llama a `_redirectToLogin()` y
**nunca resuelve la promesa** (`return new Promise(() => {})`). Esto es intencional:
garantiza que ningún código posterior de `robot_script.js` (en particular
`connectMQTT()`) llegue a ejecutarse sin sesión confirmada, porque JavaScript nunca
sigue de largo un `await` sobre una promesa que no resuelve — el `await
requireSession()` efectivamente detiene la ejecución del script en ese punto.

### 4.3 Por qué `sessionStorage` y no `localStorage` para el JWT

```javascript
sessionStorage.setItem(AUTH_CFG.storageKeyToken, token);
```

Decisión de seguridad documentada como ADR-05: `sessionStorage` se borra
automáticamente al cerrar la pestaña o el navegador, a diferencia de `localStorage`,
que persiste indefinidamente hasta que algo lo borre explícitamente. Esto reduce la
ventana de exposición si la PC usada para la demo queda desatendida con el navegador
abierto — al cerrar la pestaña, la sesión desaparece sola. El estado de los pallets
(`palletState`, en `robot_script.js`), en cambio, sí usa `localStorage`
deliberadamente: no es información sensible y tiene sentido que sobreviva a un
refresh de página o un reinicio del navegador, para no perder el conteo visual de
cajas ya depositadas en una demo larga.

### 4.4 `attemptLogin()` — no reinterpreta los errores del backend

```javascript
if (!response.ok) {
    throw new Error(data.detail || `Error de autenticación (HTTP ${response.status}).`);
}
```

El mensaje de error que el backend decide mostrar (ver `backend/README.md`, sección
3, sobre por qué "usuario incorrecto" y "contraseña incorrecta" devuelven el mismo
mensaje genérico) se propaga **tal cual** hacia `login_script.js`, que lo muestra sin
agregar lógica adicional del tipo "si el status es 401 y el username tiene tal
formato...". Esto es deliberado: el backend ya invirtió trabajo en decidir
cuidadosamente qué exponer para no permitir enumeración de usuarios válidos — que la
GUI reinterprete o enriquezca ese mensaje del lado del cliente deshace ese trabajo.

### 4.5 Relación entre el JWT y las credenciales MQTT — dos identidades distintas

El JWT emitido por el backend **nunca se envía al broker MQTT**. Identifica al
**operador humano** ante el backend de aplicación. Las credenciales MQTT
(`gui_operator` / contraseña, ver 5) identifican **a la GUI como aplicación** ante el
broker de transporte — no a la persona individual. Son dos capas de autenticación
completamente independientes, con propósitos distintos, tal como se explica en
`backend/README.md` sección 1.*"si
ya se tiene el JWT, ¿por qué el broker MQTT necesita sus propias credenciales?"* — porque
resuelven perímetros de confianza distintos (quién puede *usar el panel* vs. quién
puede *publicar en el canal de comandos del robot*), y no hay forma de que un
navegador presente un JWT como credencial de un protocolo MQTT nativo sin agregar
infraestructura adicional (un puente de autenticación) fuera del alcance de este TP.

---

## 5. `robot_script.js` — control MQTT del robot (capa de negocio de la GUI)

### 5.1 Credenciales MQTT embebidas — limitación de alcance declarada

```javascript
const CFG = {
    broker: '192.168.x.x',
    port: 9001,               // WebSocket de Mosquitto
    mqttUser: 'gui_operator',
    mqttPassword: 'contraseña',
    // ...
};
```

**Nota de seguridad documentada explícitamente en el propio archivo**: estas
credenciales quedan visibles en el código fuente servido al navegador — cualquiera
con acceso a "Ver código fuente" puede leerlas. Esto es aceptable en el alcance
actual porque: (a) la autenticación **real** de la persona operadora se resuelve en
la capa de aplicación vía JWT (ver sección 4); (b) estas credenciales MQTT
identifican a "la GUI" como aplicación cliente ante el broker, un control de acceso a
nivel de transporte/infraestructura, no un control de acceso por persona. Es la misma
limitación, y la misma justificación, documentada para las credenciales del ESP32 en
`firmware/config.py`. La mitigación real de producción sería no servir estas
credenciales al cliente en absoluto, sino mediar el acceso MQTT a través de un
backend/proxy que las inyecte del lado del servidor — fuera del alcance de este TP
académico de demo en LAN.

### 5.2 QoS y por qué cada comando usa el nivel que usa

| Comando | QoS | Justificación |
|---|---|---|
| `servo` (slider) | 0 | Alta frecuencia (se envía en cada `onchange` del slider). El valor más reciente siempre reemplaza al anterior; perder un mensaje intermedio no importa porque el próximo movimiento del slider lo corrige. |
| `status` (consulta) | 0 | Solo lectura, sin efecto de negocio irreversible. |
| `set_mode`, `move`, `semi_decision`, `pallet_clear` | 1 | Comandos de **acción crítica**: perderlos silenciosamente dejaría al operador pensando que algo pasó cuando no pasó (ej. creer que se vació un pallet que en realidad sigue lleno). QoS 1 + `msg_id` (deduplicación, ver `firmware/commands.py`) garantiza entrega sin ejecución duplicada. |

### 5.3 Sistema de "pending state" en botones críticos

Cuando se envía un comando QoS 1 (ej. `clearPallet(1)`), el botón correspondiente se
deshabilita inmediatamente y muestra un indicador `⏳`, hasta que:

- llega la confirmación del ESP32 (`pallet_cleared`, `mode_changed`, `move_done`,
  etc.), **o**
- expira un timeout de seguridad de `CFG.pending_timeout_ms` (18 segundos).

Esto previene dos problemas simultáneamente: que el operador haga doble clic y envíe
el mismo comando crítico dos veces mientras espera, y que un botón quede bloqueado
para siempre si la ESP32 nunca responde (ej. se quedó sin batería a mitad de
camino) — el timeout garantiza que la GUI siempre vuelve a un estado operable.

### 5.4 Watchdog de actividad del ESP32 — dos timers independientes

Documentado extensamente en el propio archivo (mejora v4.2 sobre v4.1): un diseño
anterior usaba un único timer que se reiniciaba con **cada** mensaje recibido, lo
que impedía que el aviso de "inactividad" apareciera mientras hubiera cualquier
tráfico de fondo (heartbeat, sensor, etc.) — es decir, nunca se disparaba realmente.
La solución final separa dos responsabilidades:

- **`_activityTimer`**: heartbeat visual informativo. Se reinicia con cada mensaje;
  si pasan 60 s sin ninguno, loguea "ESP32 ONLINE — esperando comandos" y se
  reprograma. No cambia badges, es puramente informativo.
- **`_offlineDetectTimer`**: se arma **únicamente** al perder la conexión MQTT
  (`onConnectionLost`). Espera 3 segundos a que llegue el evento `'offline'`
  explícito del firmware (que trae la causa exacta de desconexión). Si no llega,
  loguea una desconexión genérica y marca el badge en rojo — garantiza que, al cortar
  Thonny durante una demo, el operador vea el estado de error en 1–3 segundos, no
  recién a los 60 s del timer de actividad.

### 5.5 Bloqueo de decisiones hacia un pallet lleno (SEMI_AUTO)

Al recibir `pallet_full`, el botón `btn-semi-p{N}` correspondiente se deshabilita y
se marca con `dataset.palletFull = '1'`, distinguible del `dataset.pending` del
sistema de pending state (5.3) — son dos motivos de deshabilitación independientes
que `evaluarBotonesSemiauto()` combina con un AND lógico. Esto evita que el operador
pueda enviar una decisión `semi_decision` hacia un pallet que el firmware va a
rechazar de todas formas, en vez de dejar que el rechazo ocurra silenciosamente del
lado del ESP32 sin feedback claro en la UI.

---

## 6. `robot_style.css` — sistema de diseño compartido

Variables CSS centralizadas en `:root` (colores, tipografías `Rajdhani`/`Share Tech
Mono`, radios de borde) consumidas por `login_style.css` y por todo `index.html`. La
clase `.auth-pending` (sección 4.2) es el mecanismo CSS que sostiene el guard de
sesión de `auth.js`: `visibility: hidden` (no `display: none`) se eligió
deliberadamente porque preserva el layout/dimensiones del documento mientras el
contenido permanece invisible — evita un "salto" visual (*layout shift*) en el
instante en que la clase se remueve tras confirmar la sesión.

---

## 7. Cómo servir la GUI

Los archivos de `gui/` son estáticos: cualquier servidor HTTP simple sirve. La
convención del proyecto (ver `ARRANCAR_SISTEMA.ps1`) es Live Server u otro servidor
estático en `http://localhost:5500`. **No requiere Node.js, npm, ni build step.**

Orden de arranque para una demo completa (automatizado por `ARRANCAR_SISTEMA.ps1` en
la raíz del repo):

1. Broker Mosquitto (`mosquitto-broker/`) — puertos 1883 (TCP, para ESP32) y 9001
   (WebSocket, para la GUI).
2. Backend FastAPI (`backend/`) — puerto 8000.
3. Abrir `http://localhost:5500/login.html` en el navegador.

Si cualquiera de los dos servicios (broker o backend) no está arriba, la GUI lo
comunica de forma explícita: `login.html` vía el indicador de estado del backend
(sección 3.1), e `index.html` vía el badge `MQTT: Desconectado` con reintento
automático cada `CFG.reconnect_ms` (4 s).

---

## 8. Resumen de superficie de seguridad de la GUI

| Aspecto | Estado actual | Justificación / limitación |
|---|---|---|
| Autenticación de operador | JWT vía backend, `sessionStorage` | Se borra al cerrar pestaña; sin refresh token (re-login cada 60 min) |
| Guard de sesión | `requireSession()`, fail-closed | Bloquea render del panel hasta confirmación remota |
| Credenciales MQTT | Hardcodeadas en `robot_script.js` | Visibles en código fuente cliente — limitación de alcance declarada, mitigada por el JWT como capa de aplicación |
| Mensajes de error de login | Propagados sin reinterpretar | Preserva la mitigación anti-enumeración de usuarios del backend |
| Persistencia de estado de pallets | `localStorage` (no sensible) | Separado deliberadamente de `sessionStorage` (sí sensible, usado solo para el JWT) |
| Comandos críticos | QoS 1 + `msg_id` + pending state con timeout | Evita ejecución duplicada y botones bloqueados permanentemente |