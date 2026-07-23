# `src/backend/` — API de Autenticación (FastAPI + JWT)

> Proyecto: Brazo Robótico Pick & Place — Ingeniería en Computación II (UNRAF)  
> Autor: Francisco Bevilacqua  
> Versión documentada: Fase 2, v1.0.0 (backend) — coherente con Fase 3 (integración GUI)  
> Rol en el sistema: capa de **autenticación de operadores humanos** del panel de control.
> No controla el brazo robótico ni habla MQTT — es una API REST independiente y agnóstica
> del protocolo de control del hardware.

---

## 1. Por qué existe este backend (contexto de arquitectura)

El sistema completo tiene **dos capas de seguridad independientes y con propósitos distintos**:

| Capa | Qué protege | Quién/qué se autentica | Mecanismo | Vive en |
|---|---|---|---|---|
| **Transporte MQTT** | El canal de comandos hacia el ESP32 | La *aplicación* GUI ante el *broker* (Mosquitto) | Usuario/contraseña MQTT (`gui_operator`, `esp32`) | `mosquitto-broker/` |
| **Aplicación (este backend)** | El panel de control web | El *operador humano* ante el *sistema* | Login con usuario/contraseña → JWT | `src/backend/` |

Sin este backend, cualquiera que abriera `index.html` en un navegador y conociera (o
adivinara) las credenciales MQTT embebidas en el JavaScript de la GUI (ver
`gui/robot_script.js`) podría operar el brazo. El backend agrega una identidad de
**persona operadora**, auditable y revocable, por encima de esa capa de transporte.
Esta separación de responsabilidades es una decisión de arquitectura documentada como
ADR en `planificacion.md` (ADR-06)  *"¿por qué dos
sistemas de autenticación y no uno solo?"* — porque son dos perímetros de confianza
distintos (aplicación vs. transporte) y OWASP ASVS trata la autenticación de aplicación
como una capa separada del control de acceso de infraestructura.

Este backend **no controla el robot**. No importa `paho-mqtt` ni conoce los tópicos
`robot/cmd` / `robot/log`. Su única responsabilidad (Single Responsibility Principle) es:
**dado un usuario y una contraseña, decir si esa persona puede operar el sistema, y por
cuánto tiempo**.

---

## 2. Estructura del directorio

```
backend/
├── app/
│   ├── core/
│   │   ├── config.py          # Carga y validación de configuración (.env)
│   │   ├── security.py        # Hashing bcrypt + ciclo de vida de JWT
│   │   ├── dependencies.py    # Dependencia FastAPI: extracción/validación de JWT
│   │   └── rate_limit.py      # Rate limiting en memoria contra fuerza bruta
│   ├── routers/
│   │   └── auth.py            # Endpoints POST /auth/login y GET /auth/verify
│   ├── schemas/
│   │   └── auth_schemas.py    # Modelos Pydantic de entrada/salida (contratos de API)
│   └── main.py                # Punto de entrada: FastAPI app, CORS, manejo de errores
├── scripts/
│   └── generar_hash_password.py  # Utilitario CLI para generar hashes bcrypt
├── venv/                      # Entorno virtual Python (NO versionado, ver .gitignore)
├── .env                       # Secretos reales (NO versionado, ver .gitignore)
├── .env.example                # Plantilla de variables de entorno (SÍ versionado)
└── requirements.txt            # Dependencias con versiones fijas
```

La estructura sigue el patrón de capas recomendado para APIs REST: **schemas** (qué forma
tienen los datos) → **routers** (qué endpoints existen y qué orquestan) → **core**
(cómo se implementa cada pieza de lógica reutilizable: criptografía, configuración,
rate limiting). Cada capa depende solo de la de abajo, nunca al revés — `core/` no
importa nada de `routers/`.

### 2.1 Por qué `core/` está separado en 4 archivos y no en uno solo

Cada archivo de `core/` tiene una única razón para cambiar (SRP), lo cual es la prueba
de que la separación es correcta y no arbitraria:

- **`config.py`** cambia si cambia *qué* se configura (nueva variable de entorno).
- **`security.py`** cambia si cambia *cómo* se hashean contraseñas o se firman JWT
  (ej. migrar de HS256 a RS256, o de bcrypt a argon2).
- **`rate_limit.py`** cambia si cambia *la política* de fuerza bruta (ventana, máximo
  de intentos, o si se migra a un store distribuido como Redis).
- **`dependencies.py`** cambia si cambia *cómo* un endpoint exige autenticación (ej. si
  mañana se agrega un esquema de API Key adicional para un servicio interno).

Concentrar los cuatro en un solo archivo `security.py` de 300 líneas sería una violación
de SRP a nivel de archivo, difícil de auditar en una revisión de seguridad.

---

## 3. Flujo de autenticación completo

```
┌─────────────┐        POST /auth/login          ┌──────────────────┐
│  login.html │ ───── {username, password} ────► │  routers/auth.py │
└─────────────┘                                  └────────┬─────────┘
                                                          │
                  1. rate_limit.is_rate_limited(ip)?      │
                    └─ Sí → 429 Too Many Requests         │
                 2. settings.auth_users.get(username)     │
                 3. security.verify_password(pwd, hash)   │
                        └─ Falla → 401 (mensaje genérico) │
                     4. security.create_access_token(user)│
                                                          │
┌─────────────┐   { access_token, expires_in_minutes }    │
│  auth.js     │ ◄────────────────────────────────────────┘
│ sessionStorage│
└──────┬───────┘
       │  GET /auth/verify
       │  Authorization: Bearer <JWT>
       ▼
┌──────────────────┐   dependencies.get_current_username()
│  routers/auth.py │   → security.decode_access_token()
└──────────────────┘   → 200 {username, valid:true}  ó  401
```

**Paso a paso, con el porqué de cada decisión:**

1. **Rate limiting primero, antes de tocar bcrypt** (`auth.py`, comentario `[SEC]`).
   bcrypt es deliberadamente costoso en CPU (ese es su diseño de seguridad). Si se
   verificara la contraseña *antes* de chequear el límite, un atacante podría forzar
   trabajo de CPU en el servidor incluso estando bloqueado — un vector de Denial of
   Service trivial. Verificar el límite primero es más barato (una consulta a un dict
   en memoria) y corta el ataque antes de gastar el recurso caro.

2. **Comparación contra hash *dummy* si el usuario no existe** (`auth.py`). Este es el
   punto más sutil de todo el backend: si el código hiciera `if user not in auth_users: return 401` de forma
   inmediata, la respuesta para "usuario inexistente" tardaría microsegundos, mientras
   que la respuesta para "usuario existe, contraseña incorrecta" tardaría las decenas
   de milisegundos que tarda bcrypt en verificar. Esa diferencia de tiempo es un
   **timing side-channel**: un atacante que mide el tiempo de respuesta puede enumerar
   qué usernames existen sin necesitar la contraseña correcta. La mitigación es
   ejecutar `verify_password()` contra un hash bcrypt válido *pero falso*
   (`_DUMMY_HASH`) incluso cuando el usuario no existe, de modo que el tiempo de
   respuesta es indistinguible en ambos casos.

3. **Mensaje de error idéntico para "no existe" y "contraseña incorrecta"**
   (`"Usuario o contraseña incorrectos."`). Mismo objetivo que el punto anterior pero
   a nivel de contenido de la respuesta en vez de tiempo: nunca se le da a un atacante
   información para distinguir cuál de las dos causas fue (categoría OWASP A07:2021 —
   Identification and Authentication Failures, y mitigación específica contra
   enumeración de usuarios).

4. **JWT con claims estándar RFC 7519** (`sub`, `iat`, `exp`) firmado con HMAC-SHA256,
   usando `timezone.utc` explícito para evitar ambigüedades de horario de verano o
   husos horarios distintos entre servidor y cliente.

5. **`GET /auth/verify`** existe porque la GUI necesita distinguir, al recargar
   `index.html`, entre "no hay sesión" y "hay una sesión guardada en `sessionStorage`
   pero podría haber vencido o ser inválida". Verificar contra el backend (no solo
   localmente) es la única fuente de verdad real, porque el reloj del navegador o el
   propio `sessionStorage` pueden manipularse desde DevTools — ver la nota `[SEC]` en
   `auth.js`, función `_isLocallyExpired()`.

---

## 4. Manejo de credenciales, contraseñas y hashes

### 4.1 Modelo de usuarios: **sin roles diferenciados (decisión de alcance)**

El sistema actual define usuarios en la variable de entorno `AUTH_USERS`, con el
formato `usuario1:hash_bcrypt1,usuario2:hash_bcrypt2,...` (parseado por la property
`Settings.auth_users` en `config.py`). **Todos los usuarios autenticados tienen el
mismo nivel de privilegio: "operador".** No existe un rol "administrador" con
permisos adicionales, ni un endpoint de registro de usuarios nuevos vía API.

Esto es una **limitación de alcance declarada**, no un descuido — ver ADR-03 en
`planificacion.md`. Para un proyecto académico de demo con un puñado de operadores
conocidos (Francisco y, eventualmente, un profesor que quiera probar el sistema), un
esquema de dos niveles habría añadido complejidad (tabla de roles, endpoint de gestión
de usuarios, middleware de autorización por rol) sin aportar valor demostrable al
objetivo del TP, que es el control seguro del brazo robótico, no un sistema de gestión
de identidades. **"¿cómo se agregarían roles?"**, la lógica seria: agregar un claim `role` al payload del JWT en
`create_access_token()`, y una nueva dependencia FastAPI (ej. `require_role("admin")`)
que se apile sobre `get_current_username` en los endpoints que lo requieran — la
arquitectura actual ya soporta esa extensión sin refactor mayor, precisamente porque
`dependencies.py` está separado y es composable.

### 4.2 Cómo se generan las credenciales — `scripts/generar_hash_password.py`

Este script es la **única** forma soportada de generar el hash que se pega en
`AUTH_USERS`. Es un script interactivo de línea de comandos, ejecutado manualmente por
el operador que da de alta una cuenta (no hay endpoint de registro, ver 4.1):

```bash
cd backend/
python scripts/generar_hash_password.py
```

Decisiones de seguridad en este script:

- Usa `getpass.getpass()` en vez de `input()`: la contraseña no se muestra en pantalla
  mientras se tipea **y** no queda registrada en el historial de comandos de la
  terminal (a diferencia de pasarla como argumento: `python script.py mi_contraseña`,
  que sí quedaría en `.bash_history` o `PSReadLine`).
- Pide la contraseña dos veces y compara, para detectar errores de tipeo antes de
  generar un hash de una contraseña que el operador no podría reproducir después.
- Advierte (sin bloquear) si la contraseña tiene menos de 8 caracteres, siguiendo el
  RNF de "longitud sobre complejidad forzada" (NIST SP 800-63B recomienda priorizar
  longitud mínima razonable sobre reglas de complejidad — mayúscula+número+símbolo
  obligatorios generan contraseñas más predecibles y frustran al usuario sin mejorar
  la seguridad real).
- **Nunca** escribe la contraseña en texto plano a ningún archivo ni log — solo
  imprime el hash final, listo para copiar a `.env`.

El hash resultante usa **bcrypt** vía `passlib.context.CryptContext` (ver
`security.py`, función `hash_password()`), con el cost factor por defecto de passlib
(12 rounds). bcrypt incluye salt automático embebido en el propio hash — dos usuarios
con la misma contraseña producen hashes completamente distintos, lo que neutraliza
ataques de tabla precomputada (rainbow tables) sin que el desarrollador tenga que
gestionar el salt manualmente.

### 4.3 Dónde viven las credenciales — `.env`, nunca en el código

`config.py` carga toda la configuración sensible desde un archivo `.env` (vía
`pydantic-settings` + `python-dotenv`), y es el **único** módulo del backend que
accede a variables de entorno — ningún otro archivo llama a `os.environ` directamente
(principio de punto único de acceso a secretos, facilita auditar "¿de dónde puede
salir un secreto?").

```
.env (NO versionado — está en .gitignore)
├── JWT_SECRET_KEY=...            # clave HMAC para firmar los JWT
├── AUTH_USERS=francisco:$2b$12$...,otro_usuario:$2b$12$...
└── CORS_ORIGINS=http://localhost:5500

.env.example (SÍ versionado — plantilla sin secretos reales)
├── JWT_SECRET_KEY=cambiar_esto_por_una_clave_larga_y_aleatoria
├── AUTH_USERS=usuario:hash_generado_con_el_script
└── CORS_ORIGINS=http://localhost:5500
```

**Fail-fast en el arranque**: `config.py` usa Pydantic para validar la configuración
al arrancar `uvicorn`. Si falta `JWT_SECRET_KEY` o `AUTH_USERS`, o el formato de
`AUTH_USERS` está mal editado a mano (falta un `:`), la aplicación **no arranca** —
imprime un mensaje `[CRITICAL]` claro y termina con `sys.exit(1)`. Esto es deliberado:
un backend de autenticación que arrancara "a medias" (por ejemplo, sin usuarios
cargados) dejaría el login devolviendo 401 para *todo el mundo*, sin ninguna pista de
por qué — un fallo silencioso mucho peor que no arrancar.

### 4.4 Qué pasa si `JWT_SECRET_KEY` se filtra

Es el secreto más crítico del sistema: cualquiera que lo conozca puede **forjar** un
JWT válido para cualquier `username`, sin necesitar ninguna contraseña, porque la
firma HMAC es lo único que `decode_access_token()` verifica. Por eso:

- Nunca se hardcodea.
- Debe ser una cadena larga y aleatoria (no una palabra), generable por ejemplo con
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- Rotarlo invalida instantáneamente **todos** los tokens emitidos hasta ese momento —
  es el mecanismo de "cerrar sesión a todo el mundo" de emergencia, aunque no hay un
  endpoint dedicado a esto en el alcance actual (ver 6, Limitaciones conocidas).

---

## 5. `rate_limit.py` — mitigación de fuerza bruta

Implementación **en memoria de proceso** (un `dict` Python con `defaultdict(list)`),
no distribuida:

```python
_failed_attempts: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 60
```

- Solo cuentan los intentos **fallidos**; un login exitoso no penaliza a un operador
  que se equivocó una vez antes de acertar (`reset_attempts()` se llama tras un login
  correcto).
- Ventana deslizante de 60 segundos: `_purge_old_attempts()` descarta timestamps
  viejos en cada verificación, así el contador nunca crece indefinidamente ni requiere
  un job de limpieza aparte.
- **Limitación de alcance declarada y documentada explícitamente en el propio
  código** (docstring del módulo): al ser en memoria de un solo proceso, si el backend
  se escalara a múltiples workers de `uvicorn` (`--workers 4`) o a múltiples
  instancias detrás de un balanceador, **cada worker tendría su propio contador
  independiente**, lo que debilitaría el límite real (un atacante repartiendo
  peticiones entre workers podría intentar 5×N veces en vez de 5). Para un backend de
  un solo proceso sirviendo una demo académica en LAN, esto es aceptable y evita la
  dependencia de infraestructura de un store compartido como Redis. La migración
  documentada como trabajo futuro es reemplazar el `dict` por operaciones `INCR` +
  `EXPIRE` en Redis.

---

## 6. CORS y manejo global de errores — `main.py`

### 6.1 CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], // primeras demo de prueba, NO se usa
    allow_origins=settings.cors_origins, // version final
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
```

> **⚠ Observación de seguridad detectada en esta revisión (checklist OWASP,
> categoría A05:2021 — Configuración de Seguridad Incorrecta):** el comentario `[SEC]`
> inmediatamente encima de este bloque documenta correctamente que `allow_origins`
> **debería** leerse de `settings.cors_origins` (la lista parseada desde la variable
> de entorno `CORS_ORIGINS`) y que usar `["*"]` fue el diseño preliminar, ya corregido.
> 

### 6.2 Manejo global de excepciones

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc) -> JSONResponse:
    logger.error(..., exc_info=True)          # detalle completo → servidor
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})
```

Cualquier excepción no capturada explícitamente por un endpoint cae acá. El cliente
**nunca** ve el stack trace, el tipo de excepción Python, ni mensajes que revelen
rutas de archivos o estructura interna (categoría OWASP A05) — todo el detalle útil
para diagnóstico se loguea del lado del servidor con `exc_info=True`.

### 6.3 `/docs` y `/redoc` condicionados a `DEBUG_MODE`

```python
docs_url="/docs" if settings.debug_mode else None,
redoc_url="/redoc" if settings.debug_mode else None,
```

Con `DEBUG_MODE=False` (recomendado fuera de la demo controlada), la documentación
interactiva de Swagger/ReDoc queda deshabilitada — no tiene sentido exponer el mapa
completo de la API a cualquiera que la encuentre en un despliegue que no sea la
propia demo del TP.

### 6.4 Logging — auditabilidad (OWASP A09)

`main.py` configura un logger explícito con nivel `DEBUG` si `debug_mode=True`, `INFO`
en caso contrario. **Regla no negociable, cumplida en todo el backend**: en ningún
punto del código se loguea el campo `password` de un request — ni en `auth.py`, ni en
el logger global. Solo se registran eventos a nivel de aplicación (errores no
manejados, con su excepción completa, pero nunca el payload crudo del login).

---

## 7. `auth_schemas.py` — contratos de la API (Pydantic)

| Modelo | Uso | Detalle de seguridad |
|---|---|---|
| `LoginRequest` | Body de `POST /auth/login` | `username`/`password` con `max_length` (64/128). Mitiga un vector trivial de DoS: sin este límite, un cliente podría enviar un payload de varios MB como `"password"` para forzar trabajo de cómputo extra en bcrypt (que es deliberadamente costoso). |
| `LoginResponse` | Respuesta exitosa de login | Expone `access_token`, `token_type`, `username`, `expires_in_minutes`. Nunca expone el hash almacenado ni ningún dato interno de configuración. |
| `VerifyResponse` | Respuesta de `GET /auth/verify` | Solo `username` + `valid: bool`. |
| `ErrorResponse` | Forma estándar de error | Siempre `{"detail": "mensaje genérico"}` — nunca detalles internos (ver 6.2). |

La separación de estos modelos respecto a la lógica de negocio en `routers/auth.py`
aplica el Interface Segregation Principle (ISP, SOLID): cada modelo expone
exactamente los campos que su contexto necesita, ni más ni menos.

---

## 8. Cómo ejecutar el backend

```bash
cd backend/
python -m venv venv
# Windows:
.\venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt

# Copiar la plantilla y completar con datos reales:
copy .env.example .env          # Windows
# cp .env.example .env          # Linux/Mac

# Generar al menos un usuario:
python scripts/generar_hash_password.py
# → pegar la línea "usuario:hash" resultante en AUTH_USERS dentro de .env

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verificación rápida de que está arriba (usado también por `login.html`, ver
`gui/README.md`):

```
GET http://localhost:8000/health
→ {"status": "ok", "service": "robot-auth-api", "version": "1.0.0"}
```

En una sesión de demo completa, este backend se levanta automáticamente vía
`ARRANCAR_SISTEMA.ps1` (raíz del repo), que también arranca el broker Mosquitto antes
de indicarle al operador que abra la GUI.

---

## 9. Dependencias fijadas (`requirements.txt`) y por qué

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
pydantic-settings==2.7.1
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
bcrypt==4.0.1
python-dotenv==1.0.1
python-multipart==0.0.20
```

- **Versiones exactas (`==`), nunca rangos (`>=`)** — categoría OWASP A06:2021
  (Componentes Vulnerables/Desactualizados) exige dependencias conocidas y
  reproducibles, no "lo último disponible al momento de instalar", que podría
  introducir un breaking change o una vulnerabilidad nueva sin que el equipo se
  entere.
- **`bcrypt==4.0.1` fijado deliberadamente** por debajo de la última versión
  disponible: `passlib==1.7.4` tiene un bug conocido de detección de versión con
  `bcrypt>=4.1`, que dispara un warning interno (`"error reading bcrypt version"`) al
  hashear — no es una falla de seguridad (el hash se genera y verifica
  correctamente), pero es ruido evitable en logs y en la consola durante la demo.
  Verificado en este proyecto: `bcrypt==4.0.1` + `passlib==1.7.4` = sin warnings. Este
  fue uno de los dos bugs reales encontrados y documentados durante la validación de
  la Fase 2 (el otro fue el `validation_alias` de Pydantic Settings en `config.py`,
  necesario porque el nombre del atributo Python `auth_users_raw` no coincide
  literalmente con el nombre de la variable de entorno `AUTH_USERS`).
- **`python-multipart`** está incluido aunque no se usa en la versión actual (login
  recibe JSON, no `multipart/form-data`) — queda documentado por si se migra en el
  futuro a `OAuth2PasswordRequestForm`, el flujo "estándar" de FastAPI para forms de
  login, que sí requiere este paquete.

---

## 10. Limitaciones de alcance conocidas

Estas son decisiones **aceptadas y documentadas**, no descuidos — el criterio para
distinguir una cosa de la otra es precisamente que estén explicadas acá:

1. **Sin roles/RBAC** (ver 4.1). Extensión futura: claim `role` en el JWT + dependencia
   FastAPI adicional.
2. **Sin refresh tokens.** El JWT tiene una expiración fija (`JWT_EXPIRE_MINUTES`,
   default 60 min) y al vencer, el operador debe volver a loguearse — no hay un
   mecanismo de renovación silenciosa. Trade-off aceptado: para sesiones de demo de
   duración acotada, la simplicidad de no gestionar un segundo tipo de token (con su
   propio ciclo de vida y superficie de revocación) supera el costo de tener que
   volver a loguearse cada hora.
3. **Rate limiting en memoria de proceso, no distribuido** (ver 5).
4. **Sin endpoint de registro/gestión de usuarios vía API** — el alta de operadores es
   manual, vía `scripts/generar_hash_password.py` + edición de `.env`.
5. **Sin mecanismo de revocación de tokens individuales** (ej. logout del lado del
   servidor / blacklist de JWT). El logout actual (`auth.js`, función `logout()`) es
   client-side: borra el token de `sessionStorage`, pero un token robado antes del
   logout seguiría siendo válido hasta su expiración natural. Mitigación parcial:
   el tiempo de vida corto del JWT (60 min) acota la ventana de exposición.

