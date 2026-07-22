"""
Archivo: auth.py (router)
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Endpoints de autenticación del sistema Pick & Place.
             POST /auth/login  → valida credenciales, emite JWT.
             GET  /auth/verify → valida un JWT existente (usado por la GUI
                                  al recargar la página, para no perder la
                                  sesión sin tener que loguearse de nuevo).
Dependencias: fastapi

Responsabilidad: este router orquesta security.py + rate_limit.py + config.py,
pero no implementa criptografía ni parsing de configuración él mismo —
aplica SRP delegando cada responsabilidad a su módulo correspondiente.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.dependencies import get_current_username
from app.core.rate_limit import (
    is_rate_limited,
    register_failed_attempt,
    reset_attempts,
)
from app.core.security import create_access_token, verify_password
from app.schemas.auth_schemas import LoginRequest, LoginResponse, VerifyResponse

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"description": "Credenciales incorrectas"},
        429: {"description": "Demasiados intentos fallidos — IP bloqueada temporalmente"},
    },
)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """
    Autentica a un operador del brazo robótico y emite un JWT de sesión.

    Flujo de seguridad aplicado, en orden:
      1. Verificar rate limit de la IP origen (categoría OWASP #7).
      2. Verificar que el usuario exista en AUTH_USERS.
      3. Verificar la contraseña contra el hash bcrypt almacenado.
      4. Si falla 2 o 3, registrar intento fallido y devolver el MISMO
         mensaje genérico — nunca distinguir "usuario no existe" de
         "contraseña incorrecta" (mitiga enumeración de usuarios válidos).
      5. Si todo es correcto, resetear el contador de intentos fallidos
         de esa IP y emitir el JWT.

    Args:
        body (LoginRequest): username y password en texto plano (solo en
            tránsito; nunca se almacena el password recibido).
        request (Request): usado únicamente para obtener la IP origen
            (rate limiting), no se inspecciona ningún otro dato del request.

    Returns:
        LoginResponse: JWT firmado + metadata de sesión.

    Raises:
        HTTPException 429: si la IP excedió el máximo de intentos fallidos.
        HTTPException 401: si usuario o contraseña son incorrectos.
    """
    client_ip = request.client.host if request.client else "unknown"

    # [SEC] Paso 1 — Rate limiting ANTES de tocar bcrypt. Verificar el
    # límite primero evita gastar el costo computacional de bcrypt
    # (deliberadamente caro) en una IP que ya está siendo bloqueada.
    if is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intente nuevamente en un minuto.",
        )

    stored_hash = settings.auth_users.get(body.username)

    # [SEC] Paso 2+3 fusionados deliberadamente: si el usuario no existe,
    # igual se ejecuta una verificación bcrypt contra un hash "dummy" en
    # vez de retornar inmediatamente. Esto mitiga un timing attack donde
    # un atacante mide cuánto tarda la respuesta para inferir si el
    # usuario existe (verify_password con bcrypt real tarda ~decenas de ms;
    # un return inmediato tarda microsegundos — esa diferencia es medible).
    _DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO/d3PvW0mz1.K2EQfRTzwQzVT5Ck/fHO"
    hash_to_check = stored_hash if stored_hash else _DUMMY_HASH
    password_ok = verify_password(body.password, hash_to_check)

    if not stored_hash or not password_ok:
        register_failed_attempt(client_ip)
        # [SEC] Mensaje idéntico al de credenciales incorrectas por
        # contraseña — nunca "usuario no encontrado" (categoría OWASP #7).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
        )

    reset_attempts(client_ip)
    token = create_access_token(body.username)

    return LoginResponse(
        access_token=token,
        username=body.username,
        expires_in_minutes=settings.jwt_expire_minutes,
    )


@router.get("/verify", response_model=VerifyResponse)
async def verify(username: str = Depends(get_current_username)) -> VerifyResponse:
    """
    Verifica que el JWT enviado en el header Authorization sea válido.

    Usado por la GUI al cargar index.html: si el operador ya tiene un
    token en sessionStorage de una sesión previa, este endpoint confirma
    que sigue siendo válido sin forzar un nuevo login. Si decode_access_token
    falla, la dependencia get_current_username ya lanzó el 401 antes de
    que este cuerpo se ejecute — por eso el cuerpo del endpoint es trivial.

    Args:
        username (str): Inyectado por la dependencia get_current_username
            tras validar el JWT exitosamente.

    Returns:
        VerifyResponse: username confirmado y valid=True.
    """
    return VerifyResponse(username=username)
