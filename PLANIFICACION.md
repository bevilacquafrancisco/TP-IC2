# PLANIFICACION.md — Refactorización v5.0

> Brazo Robótico Pick & Place — Proyecto Final, Ingeniería en Computación II  
> Autor: Francisco Bevilacqua | Universidad Nacional de Rafaela (UNRAF)  
> Documento vivo del proceso de ingeniería — historial de decisiones, verificación y alcance.  
> Estado: **Proyecto cerrado — todas las fases completas y verificadas.**  
> Última actualización: 22/07/2026

---

## 1. Objetivo de la refactorización

La versión 4.0 del sistema (brazo robótico pick & place controlado por ESP32 vía
MQTT) cumplía su función de control en un entorno de laboratorio, pero presentaba
tres limitaciones que la volvían inadecuada como entregable final de una materia
con eje en ingeniería de software y seguridad informática:

1. Dependía de un broker MQTT público (`test.mosquitto.org`), sin autenticación
   ni control de acceso — cualquier cliente en Internet podía publicar comandos
   arbitrarios en el tópico de control del brazo.
2. No existía ningún mecanismo de identificación del operador humano: cualquier
   persona con acceso a la interfaz gráfica podía operar el sistema sin
   distinción de usuarios ni trazabilidad de acciones.
3. La interfaz web no contaba con una capa de autorización que precediera al
   panel de control.

Se definió como objetivo migrar el sistema a una arquitectura v5.0 con:

1. Un **broker Mosquitto privado**, alojado en la red local, con autenticación
   por usuario/contraseña y control de acceso (ACL) por rol.
2. Un **backend de autenticación** (FastAPI) que emite credenciales de sesión
   (JWT) a operadores humanos previamente dados de alta.
3. Una **interfaz gráfica con pantalla de login**, que exige una sesión válida
   antes de revelar el panel de control del brazo.
4. Una **reestructuración del firmware** del ESP32, pasando de un único archivo
   monolítico a una arquitectura modular con responsabilidades separadas,
   manteniendo intacta la lógica de control ya validada en producción.

El desarrollo siguió un proceso disciplinado de ingeniería de software (esquema
general: especificación de requerimientos → diseño y decisiones de arquitectura
→ implementación documentada → verificación funcional en cada etapa →
documentación final), evitando avanzar a una fase sin haber cerrado la
anterior con evidencia de que funciona según lo especificado.

---

## 2. Estado general del proyecto

| Fase | Descripción | Estado |
|---|---|---|
| Fase 1 | Broker Mosquitto privado (autenticación + ACL) | 🟢 Completa y verificada |
| Fase 2 | Backend de autenticación (FastAPI + JWT) | 🟢 Completa y verificada |
| Fase 3 | Interfaz gráfica: login + integración de sesión | 🟢 Completa y verificada |
| Fase 4 | Modularización del firmware ESP32 (v5.0) | 🟢 Completa y verificada |

Leyenda: ⚪ No iniciada · 🟡 En curso · 🟢 Completa · 🔴 Bloqueada

Las cuatro fases fueron implementadas, probadas de forma funcional (no solo
revisadas por lectura de código) y verificadas de manera cruzada entre capas
(firmware ↔ broker ↔ backend ↔ interfaz) antes de considerarse cerradas. El
comportamiento de control del brazo (los tres modos de operación: MANUAL,
SEMI_AUTO y AUTOMATICO) se mantuvo funcionalmente idéntico a la versión 4.0
en todo momento — la refactorización agregó capas de seguridad y reorganizó
la estructura del código, pero no modificó la lógica de negocio ya validada.

---

## 3. FASE 1 — Broker Mosquitto privado

### 3.1 Trabajo realizado

- Diseño de la arquitectura de red y del flujo de autenticación antes de
  tocar cualquier archivo de configuración.
- `mosquitto.conf` configurado con:
  - Listener TCP en el puerto 1883 (consumido por el ESP32).
  - Listener WebSocket en el puerto 9001 (consumido por la interfaz gráfica
    desde el navegador, que no puede abrir sockets TCP directos).
  - `allow_anonymous false` en ambos listeners — ningún cliente sin
    credenciales puede conectarse.
  - `password_file` y `acl_file` configurados y cargados por el servicio.
  - `connection_messages true`, para dejar registro de auditoría de
    conexiones y desconexiones.
  - Persistencia de sesión habilitada, requisito técnico dado que el
    firmware trabaja con `clean_session=False` y QoS 1 en la suscripción de
    comandos (ver Fase 4), lo que exige que el broker pueda reencolar
    mensajes no confirmados durante una desconexión del ESP32.
  - `message_size_limit` configurado como mitigación básica ante mensajes
    anormalmente grandes.
- `acl.conf` definida bajo el principio de mínimo privilegio:
  - Usuario `esp32`: permiso de escritura en `robot/log`, lectura en
    `robot/cmd`.
  - Usuario `gui_operator`: permiso de lectura en `robot/log`, escritura en
    `robot/cmd`.
  - Cualquier tópico no listado queda denegado por defecto (comportamiento
    nativo de Mosquitto ante una ACL explícita).
- Generación de credenciales de acceso al broker mediante un script Python
  dedicado (`generar_passwd.py`), que produce el archivo de credenciales
  consumido por Mosquitto sin dejar contraseñas en texto plano en ningún
  archivo versionado.
- `.gitignore` de la carpeta del broker excluyendo el archivo de
  credenciales, los registros de conexión y los datos de persistencia —
  ninguno de estos artefactos de entorno de ejecución se versiona.
- Verificación cruzada con clientes de línea de comandos (publicador y
  suscriptor independientes) para confirmar:
  - Que un cliente sin credenciales es rechazado por el broker.
  - Que el usuario `gui_operator` no puede publicar en `robot/log` (valida
    que la ACL está efectivamente activa, no solo el archivo de
    contraseñas).
  - Que ambos usuarios operan correctamente dentro de los permisos
    asignados.
- Verificación de extremo a extremo: interfaz gráfica ↔ broker privado ↔
  ESP32, confirmando que los tres modos de operación funcionan de forma
  idéntica a la versión con broker público.
- Revisión del registro del broker posterior a la prueba de extremo a
  extremo, sin errores de autenticación ni desconexiones inesperadas.

### 3.2 Decisiones de diseño y justificación

| Decisión | Justificación |
|---|---|
| Sin cifrado TLS en esta fase | El sistema opera dentro de una red local de confianza (laboratorio / demostración académica), no expuesta a Internet. Se documenta como límite de alcance deliberado, no como una omisión — la incorporación de TLS queda planteada como trabajo futuro si el sistema migrara a un entorno de red no controlado. |
| Dos usuarios separados (`esp32`, `gui_operator`) en vez de uno compartido | Aplica el principio de mínimo privilegio: cada rol accede solo a los tópicos que necesita, y revocar el acceso de un rol no afecta al otro. |
| ACL explícita además del archivo de contraseñas | Defensa en profundidad: si un conjunto de credenciales quedara comprometido, el daño potencial queda acotado a los tópicos permitidos para ese usuario. |
| Persistencia de sesión habilitada en el broker | Requisito derivado del uso de `clean_session=False` y QoS 1 en el firmware: sin persistencia, el broker no podría garantizar la reentrega de comandos emitidos mientras el ESP32 estaba desconectado. |
| Exclusión de credenciales y datos de ejecución del control de versiones | Ninguna credencial, aunque esté hasheada, se sube al repositorio. |

### 3.3 Riesgos identificados y mitigaciones aplicadas

- **Bloqueo por firewall:** se verificó que el firewall del sistema
  operativo permitiera tráfico entrante en los puertos 1883, 9001 y 8000;
  de lo contrario, tanto el ESP32 como la interfaz gráfica fallarían en
  conectar sin ningún mensaje de error explícito del lado del cliente.
- **Cambio de dirección IP local:** al depender de una IP asignada por
  DHCP, se recomienda verificarla cada vez que se hace uso del sistema,
  dado que un cambio de IP produce fallas silenciosas (el ESP32 reintenta
  indefinidamente sin indicar la causa raíz). Como mitigación adicional se
  sugiere reservar la IP en el router.
- **Compatibilidad de librería MQTT en MicroPython:** se confirmó que la
  versión de `umqtt.simple` utilizada admite los parámetros de usuario y
  contraseña en la conexión al broker.

---

## 4. FASE 2 — Backend de autenticación (FastAPI + JWT)

### 4.1 Trabajo realizado

- Definición de las decisiones de arquitectura antes de implementar
  (registradas como ADR — Architecture Decision Record — dentro del propio
  código, en comentarios `[SEC]` junto a cada decisión relevante):
  - Hash de contraseñas con `bcrypt` (12 rounds), nunca texto plano ni
    algoritmos de hash de propósito general (MD5/SHA-1).
  - Tokens JWT sin mecanismo de refresco, con expiración configurable
    (60 minutos por defecto) — alcance acotado a sesiones de operación de
    duración acotada.
  - Usuarios operadores definidos en variables de entorno, no en una base
    de datos — decisión proporcional al volumen real de usuarios del
    sistema (dos a tres operadores).
  - Secretos (clave de firma JWT, credenciales de operadores) cargados
    exclusivamente desde variables de entorno, nunca escritos en el código
    fuente.
- Estructura del backend organizada por incumbencias (`core/` para
  configuración y primitivas de seguridad, `routers/` para los endpoints,
  `schemas/` para los modelos de entrada/salida), evitando concentrar
  lógica heterogénea en un único archivo.
- `config.py`: carga de configuración con validación estricta al arrancar
  (comportamiento de fallo temprano: si falta una variable obligatoria o
  tiene un formato inválido, el proceso no arranca, en vez de hacerlo con
  una configuración parcial o insegura por defecto).
- `security.py`: único módulo del backend con acceso a las primitivas
  criptográficas (hash de contraseñas y ciclo de vida completo del JWT:
  emisión, decodificación, y distinción explícita entre un token expirado
  y un token inválido o manipulado).
- `rate_limit.py`: limitador de intentos de inicio de sesión por dirección
  IP de origen (5 intentos fallidos por minuto), como mitigación de
  ataques de fuerza bruta sobre el endpoint de login.
- `dependencies.py`: mecanismo reutilizable de verificación de sesión,
  aplicable a cualquier endpoint que requiera un operador autenticado.
- `auth_schemas.py`: modelos de entrada con límites explícitos de longitud
  en usuario y contraseña, evitando que un cliente malicioso fuerce
  cómputo excesivo de `bcrypt` (deliberadamente costoso en CPU) con
  payloads de gran tamaño.
- `auth.py` (router): endpoints `POST /auth/login` y `GET /auth/verify`,
  con un mismo mensaje de error genérico ante usuario inexistente y ante
  contraseña incorrecta, para no permitir enumerar usuarios válidos del
  sistema por diferencia de respuesta.
- `main.py`: orígenes CORS restringidos explícitamente (nunca un comodín
  abierto a cualquier origen), manejo global de excepciones no controladas
  que nunca expone detalles internos (rutas de archivo, tipo de excepción,
  traza) al cliente, y documentación interactiva (`/docs`) condicionada a
  un modo de depuración desactivable.
- Script utilitario para la generación de hashes `bcrypt` de forma
  interactiva, sin que la contraseña en texto plano quede expuesta en
  ningún historial de terminal ni archivo.
- Dependencias fijadas a versiones exactas (no rangos abiertos), para
  garantizar reproducibilidad del entorno y facilitar la auditoría de
  vulnerabilidades conocidas antes de cada entrega.

### 4.2 Verificación funcional realizada

Se ejecutó una batería de pruebas funcionales de extremo a extremo sobre
el backend en ejecución (no una simple revisión estática del código):

- Arranque del servidor sin advertencias.
- `GET /health` responde correctamente sin requerir autenticación.
- `POST /auth/login` con credenciales válidas devuelve un token válido.
- `GET /auth/verify` con ese token confirma la identidad del operador.
- Login con contraseña incorrecta devuelve `401` con mensaje genérico.
- Login con usuario inexistente devuelve el **mismo** código y mensaje que
  el caso anterior.
- `GET /auth/verify` sin token devuelve `403` (comportamiento estándar del
  esquema de autenticación HTTP Bearer).
- `GET /auth/verify` con un token manipulado devuelve `401` con mensaje
  genérico.
- El limitador de intentos bloquea el sexto intento fallido consecutivo
  con `429`, incluso si ese sexto intento usa la contraseña correcta —
  confirmando que el límite se evalúa antes de validar la contraseña, no
  después.

### 4.3 Hallazgos durante la implementación

Dos incidencias surgieron durante la verificación funcional y quedaron
resueltas antes del cierre de la fase, documentadas como evidencia del
proceso de verificación aplicado en cada etapa:

1. **Mapeo de variable de entorno:** el mecanismo de configuración,
   por convención, esperaba que la variable `AUTH_USERS` mapeara a un
   campo con el mismo nombre en minúsculas. Al renombrar internamente ese
   campo para diferenciarlo de la propiedad que lo interpreta, el sistema
   falló al arrancar con un error explícito de campo faltante — el
   comportamiento de fallo temprano funcionó exactamente como estaba
   previsto. Se corrigió declarando el alias de validación
   correspondiente.
2. **Incompatibilidad de versiones entre las librerías de hashing:** al
   generar el primer hash de prueba apareció una advertencia interna de
   detección de versión entre la librería de gestión de contraseñas y la
   implementación de `bcrypt` utilizada. No representaba una falla de
   seguridad — el hash se generaba y verificaba correctamente — pero se
   corrigió fijando una versión específica de `bcrypt` en las
   dependencias, dejando el comportamiento libre de advertencias y
   completamente determinístico.

### 4.4 Decisiones de diseño y justificación

| Decisión | Justificación |
|---|---|
| `bcrypt` (12 rounds) para contraseñas | Incluye salt automático por hash, mitigando ataques de tabla precomputada; evita algoritmos de hash de propósito general inadecuados para contraseñas. |
| JWT sin mecanismo de refresco | Alcance acotado a sesiones de operación de duración corta; documentado como ampliación posible si el sistema pasara a uso continuo. |
| Usuarios definidos en variables de entorno, no en base de datos | El volumen de usuarios (2-3 operadores) no justifica la complejidad operativa de una base de datos dedicada. |
| Mensaje de error idéntico ante usuario inexistente y contraseña incorrecta | Evita la enumeración de cuentas válidas del sistema. |
| Verificación de contraseña contra un hash señuelo aunque el usuario no exista | Evita que la diferencia de tiempo de respuesta permita inferir la existencia de una cuenta. |
| Limitador de intentos en memoria de proceso, no distribuido | El backend se ejecuta como un único proceso durante la operación y demostración del sistema; una solución distribuida (por ejemplo, con un almacén externo) sería sobredimensionada para este alcance y queda documentada como límite conocido. |
| CORS restringido a orígenes explícitos | Un origen abierto permitiría que cualquier sitio web realizara solicitudes autenticadas contra el backend desde el navegador de un operador con sesión iniciada. |
| Documentación interactiva condicionada a modo de depuración | Fuera de un entorno de desarrollo, exponerla amplía innecesariamente la superficie de ataque. |

---

## 5. FASE 3 — Interfaz gráfica: login e integración de sesión

### 5.1 Trabajo realizado

- Decisiones de arquitectura definidas antes de implementar:
  - Persistencia del token de sesión en almacenamiento de sesión del
    navegador (no en almacenamiento persistente), reduciendo la ventana de
    exposición ante un eventual ataque de scripting entre sitios, y sin
    requerir que la sesión sobreviva al cierre del navegador.
  - Separación estricta entre el módulo de autenticación y el módulo de
    control del brazo: el primero no conoce el protocolo MQTT y el segundo
    no conoce el formato de los tokens de sesión — cada módulo tiene una
    única razón de cambio.
  - Verificación de sesión sincronizada con el pintado del panel: el
    contenido operativo permanece oculto hasta confirmar una sesión válida
    contra el backend, evitando cualquier ventana en la que el panel de
    control resulte visible o interactivo antes de completarse la
    verificación.
- La pantalla de login hereda íntegramente la identidad visual ya
  existente del panel de control (paleta de colores, tipografía, patrón
  visual de indicadores de estado), sin introducir variables de estilo
  nuevas — mantiene coherencia con el resto del sistema en lugar de
  presentar una pantalla de acceso genérica y desconectada visualmente
  del producto.
- Módulo de autenticación (`auth.js`) completo y autocontenido, con las
  funciones necesarias para iniciar sesión, verificar una sesión existente
  contra el backend, cerrar sesión y exponer el token y el nombre del
  operador a otros módulos. Los mensajes de error devueltos por el backend
  se propagan sin reinterpretación, preservando el criterio de
  no-enumeración de usuarios ya aplicado en el backend.
- Pantalla de login con indicador en vivo del estado del backend de
  autenticación, visible antes de intentar iniciar sesión — convierte un
  eventual backend caído en información explícita en lugar de un error de
  red poco claro para el operador.
- Hoja de estilos de login sin declarar ninguna variable CSS nueva: toda
  la apariencia deriva de las propiedades ya definidas en el panel
  principal. Incluye soporte de accesibilidad (foco visible por teclado,
  respeto a la preferencia de reducción de movimiento del sistema
  operativo).
- Panel principal (`index.html`) actualizado con: ocultamiento del
  contenido operativo hasta confirmar sesión, indicador del operador
  autenticado con botón de cierre de sesión, orden de carga de scripts
  ajustado para que el módulo de autenticación se ejecute antes que el
  módulo de control, y actualización de la información de pie de página
  para reflejar el broker privado.
- Módulo de control del brazo actualizado para exigir una sesión válida
  antes de establecer la conexión MQTT, revelar el panel una vez
  confirmada la sesión, y mostrar el nombre del operador autenticado.

### 5.2 Verificación funcional realizada

- Validación de sintaxis de los módulos JavaScript nuevos, sin errores.
- Validación funcional del módulo de autenticación contra una instancia
  real del backend: inicio de sesión con credenciales correctas (token y
  nombre de operador accesibles), verificación remota de la sesión recién
  creada, rechazo de credenciales incorrectas con el mensaje exacto del
  backend, cierre de sesión con limpieza del token y redirección, e
  intento de acceso sin sesión activa con redirección a la pantalla de
  login.
- Verificación de extremo a extremo en navegador: acceso directo al panel
  sin sesión iniciada (redirige a login), backend caído (indicador en
  rojo antes de intentar loguearse), login exitoso (panel revelado sin
  parpadeo, indicador de operador correcto, conexión MQTT establecida con
  las credenciales del rol correspondiente), cierre de sesión (regreso a
  login, con el acceso directo posterior nuevamente bloqueado), y
  expiración real del token verificada reduciendo temporalmente su tiempo
  de vida.

### 5.3 Hallazgo durante la implementación

Durante el armado del entorno de prueba del módulo de autenticación fuera
de un navegador, un primer intento de ejecución produjo un error de
referencia inexistente sobre una función que sí estaba correctamente
definida. La causa no fue un defecto del código: el modo estricto de
JavaScript (activado deliberadamente en el módulo, como buena práctica) no
expone declaraciones de función al contexto que invoca al evaluar el
código de forma directa. Se corrigió ejecutando el módulo en un contexto
persistente que replica con fidelidad cómo un navegador interpreta un
script clásico, lo cual permitió validar la lógica del módulo de forma
aislada y reproducible antes de la integración final en navegador. Esta
incidencia es de naturaleza metodológica (cómo se diseñó el arnés de
prueba), no un defecto del entregable.

### 5.4 Decisiones de diseño y justificación

| Decisión | Justificación |
|---|---|
| Almacenamiento de sesión (no persistente) para el token | Reduce la ventana de exposición ante scripting entre sitios; no se requiere que la sesión sobreviva al cierre del navegador. |
| Separación estricta entre el módulo de autenticación y el de control del brazo | Cambiar el mecanismo de autenticación no debe afectar la lógica de control, y viceversa. |
| Verificación de sesión sincronizada con el pintado del panel | Evita exponer visual o funcionalmente un panel operativo antes de confirmar la autenticación. |
| Indicador de estado del backend en la pantalla de login | Convierte un backend no disponible en información explícita antes de intentar iniciar sesión. |
| Identidad visual heredada, sin paleta nueva | Mantiene coherencia con un sistema que ya cuenta con una dirección visual definida. |
| Propagación de mensajes de error del backend sin reinterpretación | Preserva el criterio de no-enumeración de usuarios ya resuelto en el backend. |

---

## 6. FASE 4 — Modularización del firmware ESP32 (v5.0)

### 6.1 Motivación

El firmware original concentraba en un único archivo (aproximadamente mil
líneas) la configuración, el estado global del sistema, la gestión de
WiFi, la gestión de MQTT, la lectura del sensor, el control de los
servomotores, el enrutamiento de comandos y el bucle principal. Esta
estructura, si bien funcional, dificultaba la localización de cualquier
cambio puntual (recalibrar una posición, ajustar un temporizador, agregar
un comando) y aumentaba el riesgo de introducir un efecto no deseado al
modificar una sección del archivo.

### 6.2 Trabajo realizado

- División del firmware en ocho módulos con responsabilidad única:
  - `config.py`: constantes de configuración (credenciales de red,
    pines, temporización, posiciones calibradas), sin lógica ni
    dependencias — única fuente de verdad para recalibración.
  - `state.py`: estado mutable compartido del sistema, encapsulado en una
    única instancia reutilizada por todos los módulos (evita el error
    clásico de reasignación de variables globales al modularizar un
    programa originalmente escrito como un único archivo), junto con las
    utilidades de registro de eventos.
  - `wifi.py`: conexión y verificación del enlace WiFi.
  - `mqtt.py`: capa de transporte MQTT (conexión, publicación,
    verificación de mensajes entrantes), desacoplada del significado de
    los comandos del robot mediante inyección de las funciones de
    callback correspondientes, evitando una dependencia circular entre
    módulos.
  - `sensor.py`: lectura del sensor de detección de cajas con
    antirrebote por muestreo consecutivo, sin conocimiento de la lógica
    de negocio que dispara.
  - `servos.py`: conversión de ángulos a señal de control, movimiento
    suavizado, y las secuencias de movimiento predefinidas (incluida la
    secuencia completa de recolección y depósito).
  - `commands.py`: interpretación de los comandos entrantes desde la
    interfaz gráfica, lógica de decisión según el modo de operación
    activo, y armado del reporte de telemetría completo.
  - `main.py`: punto de entrada — inicialización de hardware y
    conectividad, y bucle principal no bloqueante coordinado por
    comparación de marcas de tiempo.
- Preservación exacta de la lógica de negocio ya validada en producción:
  el comportamiento ante cada comando, cada modo de operación y cada
  evento del sensor es idéntico al de la versión previa — la
  modularización es un refactor estructural, no un cambio de
  comportamiento.
- Incorporación de las credenciales de acceso al broker privado (definido
  en la Fase 1) dentro de la configuración del firmware, documentando
  explícitamente la limitación de que MicroPython no dispone de un
  mecanismo estándar de variables de entorno equivalente al de un sistema
  operativo de propósito general, por lo que dichas credenciales
  permanecen en el archivo de configuración del dispositivo.
- Verificación de que el grafo de dependencias entre módulos no contiene
  ciclos (requisito de importación de MicroPython), documentado
  explícitamente en la cabecera del punto de entrada.

### 6.3 Decisiones de diseño y justificación

| Decisión | Justificación |
|---|---|
| Estado compartido encapsulado en una única instancia, no variables globales sueltas | En un programa dividido en varios módulos, la reasignación de una variable importada no modifica el valor original — el patrón de instancia única evita este error de forma estructural. |
| Inyección de funciones de callback en la capa de transporte MQTT | Permite que la capa de transporte no dependa del módulo de lógica de comandos, evitando una dependencia circular entre ambos. |
| Separación entre lectura de hardware (sensor, servos) y decisión de negocio (comandos) | Permite razonar y, eventualmente, probar cada capa de forma aislada. |
| Credenciales del broker en el archivo de configuración del firmware | Limitación de alcance aceptada y documentada: no existe en MicroPython un mecanismo estándar de variables de entorno inyectadas en tiempo de ejecución equivalente al de un proceso de servidor. |

### 6.4 Verificación funcional realizada

- Prueba de extremo a extremo con el firmware modularizado cargado en el
  dispositivo: conexión WiFi, conexión y autenticación contra el broker
  privado, recepción de comandos desde la interfaz gráfica y publicación
  de telemetría, con los tres modos de operación funcionando de forma
  equivalente a la versión monolítica previa.
- Verificación de reconexión automática ante pérdida de enlace WiFi y ante
  pérdida de conexión con el broker, incluyendo la re-sincronización activa
  del estado completo del sistema hacia la interfaz gráfica al reconectar.
- Verificación de deduplicación de comandos críticos (cambio de modo,
  movimientos preconfigurados, decisión en modo semiautomático, vaciado de
  pallet) ante reenvíos del broker propios del nivel de calidad de
  servicio utilizado.

---

## 7. Alcance funcional del sistema entregado

- Control manual de los cuatro servomotores del brazo mediante controles
  deslizantes individuales, con movimiento suavizado.
- Movimientos preconfigurados: posición de reposo, recolección, apertura y
  cierre de pinza.
- Modo semiautomático: detección de caja mediante sensor infrarrojo, con
  alerta a la interfaz gráfica y decisión del operador sobre el destino
  (pallet 1, pallet 2, o ignorar).
- Modo automático: detección y ejecución completa de la secuencia de
  recolección y depósito sin intervención del operador, con prioridad de
  llenado del pallet 1 sobre el pallet 2 y detención controlada cuando
  ambos pallets alcanzan su capacidad máxima.
- Telemetría en tiempo real: memoria libre, intensidad de señal WiFi,
  cantidad de reconexiones, comandos recibidos, y causa del último
  reinicio del microcontrolador.
- Persistencia del estado de los pallets en el navegador, restaurado
  automáticamente al recargar la interfaz.
- Autenticación de operadores mediante usuario y contraseña, con emisión
  de credenciales de sesión de duración acotada.
- Control de acceso al broker MQTT por rol, independiente del control de
  acceso de operadores humanos a la interfaz.
- Consola de eventos en la interfaz gráfica, con distinción visual por
  origen (ESP32, interfaz, sistema) y por nivel de severidad.

---

## 8. Limitaciones conocidas y justificación

Las siguientes limitaciones son decisiones de alcance deliberadas,
documentadas para su defensa académica, y no representan omisiones del
proceso de desarrollo:

| Limitación | Justificación / alcance de la mejora futura |
|---|---|
| Sin cifrado TLS en las conexiones MQTT | El sistema opera en una red local de confianza, no expuesta a Internet. La incorporación de TLS es la extensión natural si el sistema se desplegara en una red no controlada. |
| Tokens de sesión sin mecanismo de refresco | Alcance acotado a sesiones de operación de duración corta; ampliable si el sistema pasara a un régimen de uso continuo. |
| Limitador de intentos de login en memoria de un único proceso, no distribuido | El backend se ejecuta como un solo proceso durante la operación del sistema; una solución distribuida sería sobredimensionada para este alcance. |
| Usuarios operadores definidos en variables de entorno, sin base de datos ni panel de administración | El volumen de operadores (dos a tres personas) no justifica la complejidad de una capa de persistencia dedicada. |
| Credenciales de red y de broker en el archivo de configuración del firmware | MicroPython no dispone de un mecanismo estándar de variables de entorno inyectadas en tiempo de ejecución; la alternativa (un archivo de secretos separado, excluido del control de versiones) queda documentada como mejora incremental de bajo costo. |
| Sin panel de administración de usuarios ni de auditoría histórica de acciones por operador | Fuera del alcance funcional definido para esta entrega; el sistema sí deja registro de conexión en el broker y en la consola de eventos de la interfaz. |

---

## 9. Estructura final del repositorio

```
TP-IC2/
│
├── planificacion.md
│
├── mosquitto-broker/
│   ├── mosquitto.conf
│   ├── acl.conf
│   ├── generar_passwd.py
│   ├── .gitignore
│   ├── README.md
│   ├── passwd/                 (generado localmente, no versionado)
│   ├── logs/                   (generado localmente, no versionado)
│   └── data/                   (generado localmente, no versionado)
│
├── firmware/
│   ├── main.py
│   ├── config.py
│   ├── state.py
│   ├── wifi.py
│   ├── mqtt.py
│   ├── sensor.py
│   ├── servos.py
│   ├── commands.py
│   └── README.md
│
├── backend/
│   ├── .env.example
│   ├── .gitignore
│   ├── README.md
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   ├── rate_limit.py
│   │   │   └── dependencies.py
│   │   ├── routers/
│   │   │   └── auth.py
│   │   └── schemas/
│   │       └── auth_schemas.py
│   └── scripts/
│       └── generar_hash_password.py
│
├── gui/
│   ├── index.html
│   ├── login.html
│   ├── robot_style.css
│   ├── login_style.css
│   ├── auth.js
│   ├── login_script.js
│   ├── robot_script.js
│   └── README.md
│
└── ARRANCAR_SISTEMA.ps1
```

Cada subcarpeta con manejo de secretos o datos de ejecución (`mosquitto-broker/`,
`backend/`) cuenta con su propio archivo de exclusión de control de versiones.
El firmware no maneja archivos de secretos separados por la limitación de
plataforma documentada en la sección 8.

El script `ARRANCAR_SISTEMA.ps1`, incluido en la raíz del repositorio, automatiza
la puesta en marcha del entorno de demostración completo: reinicia el proceso
del broker con su configuración privada, verifica que los puertos necesarios
estén activos, e inicia el backend de autenticación, dejando el sistema listo
para que la interfaz gráfica se sirva y se conecte.

---

## 10. Checklist global de verificación (cerrado)

### Infraestructura

- [x] Broker Mosquitto en ejecución con configuración privada propia.
- [x] Backend de autenticación en ejecución, con configuración de entorno
      completa y credenciales de operadores generadas.
- [x] Dirección de red local verificada y coherente entre la configuración
      del firmware y la de la interfaz gráfica.
- [x] Reglas de firewall habilitadas para los puertos utilizados por el
      broker (MQTT y WebSocket) y por el backend.
- [x] Orígenes permitidos por CORS coincidentes con el origen real desde
      el que se sirve la interfaz gráfica.

### Funcional

- [x] Inicio de sesión con credenciales correctas, de extremo a extremo
      (interfaz → backend → panel revelado).
- [x] Conexión MQTT establecida tras un inicio de sesión exitoso.
- [x] Conexión con el ESP32 confirmada, con telemetría real visible en la
      interfaz.
- [x] Los tres modos de operación (manual, semiautomático, automático)
      funcionan de forma equivalente a la versión previa a la
      refactorización.
- [x] El cierre de sesión invalida efectivamente el acceso al panel, no
      solo su visualización.

### Documentación y proceso

- [x] Decisiones de arquitectura registradas junto con su justificación en
      cada fase.
- [x] Límites de alcance documentados explícitamente como decisiones, no
      como pendientes ocultos.
- [x] Hallazgos de la verificación funcional documentados como evidencia
      del proceso de validación en cada etapa.

---

## 11. Conclusiones y trabajo futuro

El sistema resultante conserva la totalidad de la funcionalidad de control
del brazo robótico validada en la versión previa, e incorpora una capa de
seguridad de dos niveles independientes: control de acceso a nivel de
transporte (broker MQTT, por rol de aplicación) y control de acceso a nivel
de aplicación (autenticación de operadores humanos, por sesión). La
reestructuración del firmware en módulos de responsabilidad única mejora la
mantenibilidad del código sin alterar el comportamiento operativo del
sistema.

Como líneas de trabajo futuro, quedan identificadas: la incorporación de
cifrado de transporte en las conexiones MQTT, un mecanismo de refresco de
sesión para operación continua, persistencia de usuarios operadores en una
capa de datos si el número de operadores creciera, y externalización de las
credenciales de red del firmware mediante un archivo de secretos dedicado
si la plataforma de firmware lo permitiera.

---

## 12. Notas sobre este documento

Este documento reconstruye, a modo de registro de proceso, las decisiones
de arquitectura, los hallazgos de verificación y el alcance final del
proyecto, con el objetivo de acompañar el código fuente publicado y servir
como respaldo para la instancia de defensa oral. Cada fase fue cerrada
únicamente después de contar con verificación funcional concreta —no solo
inspección de código— de que el comportamiento especificado se cumplía.