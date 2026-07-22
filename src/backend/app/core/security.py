"""
Archivo: security.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Primitivas de seguridad del backend: verificación de contraseñas
             con bcrypt y ciclo de vida completo de JWT (creación, decodificación,
             manejo de expiración e invalidez).
Dependencias: passlib[bcrypt], python-jose[cryptography]

[SEC] Este módulo es intencionalmente el único lugar del backend que importa
bcrypt y jose. Concentrar la criptografía en un solo archivo facilita la
auditoría de seguridad: revisar este
archivo es revisar toda la superficie criptográfica del sistema.

[SEC] Regla no negociable aplicada: nunca se implementan algoritmos de
hash o firma propios. Se usan exclusivamente librerías estándar y
auditadas (passlib/bcrypt, python-jose).
"""

from datetime import datetime, timedelta, timezone

from jose import JWTError, ExpiredSignatureError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# -----------------------------------------------------------------------------
# Hashing de contraseñas
# -----------------------------------------------------------------------------
# [SEC] bcrypt con cost factor por defecto de passlib (12 rounds): "contraseñas con hash bcrypt,
# mínimo 12 rounds". bcrypt incluye salt automático por hash — dos usuarios
# con la misma contraseña producen hashes distintos, lo que mitiga ataques
# de tabla precomputada (rainbow tables).
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifica una contraseña en texto plano contra su hash bcrypt almacenado.

    [SEC] La comparación la realiza passlib internamente de forma segura
    contra timing attacks (no se usa "==" de Python sobre strings, que
    filtraría tiempo de ejecución proporcional a cuántos caracteres
    coinciden desde el inicio).

    Args:
        plain_password (str): Contraseña ingresada por el operador en el login.
        hashed_password (str): Hash bcrypt almacenado en AUTH_USERS (.env).

    Returns:
        bool: True si la contraseña es correcta, False en caso contrario.
    """
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(plain_password: str) -> str:
    """
    Genera el hash bcrypt de una contraseña. Usado únicamente por el script
    utilitario scripts/generar_hash_password.py — el backend en ejecución
    normal NUNCA hashea contraseñas nuevas (no hay endpoint de registro
    en el alcance de esta fase, ver ADR-03 en planificacion.md).

    Args:
        plain_password (str): Contraseña en texto plano a hashear.

    Returns:
        str: Hash bcrypt completo (incluye salt y cost factor), listo para
             pegar en la variable de entorno AUTH_USERS.
    """
    return pwd_context.hash(plain_password)


# -----------------------------------------------------------------------------
# JWT — creación y verificación
# -----------------------------------------------------------------------------

def create_access_token(username: str) -> str:
    """
    Genera un JWT firmado (HMAC-SHA256) que identifica al operador autenticado.

    El payload sigue las claims estándar de RFC 7519:
      - sub (subject): username del operador.
      - iat (issued at): timestamp de emisión, en UTC.
      - exp (expiration): timestamp de vencimiento, en UTC.

    [SEC] Se usa timezone.utc explícitamente. Un error común es usar
    datetime.now() sin timezone, lo que produce tokens con expiración
    ambigua si el servidor y el cliente están en zonas horarias distintas
    o si el servidor cambia de horario de verano.

    Args:
        username (str): Identificador del operador autenticado exitosamente.

    Returns:
        str: Token JWT compacto (header.payload.signature) codificado en Base64URL.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


class TokenValidationError(Exception):
    """
    Excepción de dominio para fallas de validación de JWT.

    [SEC] Se distingue deliberadamente de las excepciones crudas de
    python-jose (JWTError, ExpiredSignatureError) para que las capas
    superiores (routers) no necesiten conocer la librería de JWT
    subyacente — aplica DIP (Dependency Inversion Principle, SOLID).
    Si en el futuro se cambia python-jose por PyJWT, solo este módulo
    se modifica.

    Attributes:
        reason (str): Motivo legible de la falla, usado en logs internos.
                      NUNCA se expone directamente al cliente (ver router
                      de auth: el mensaje HTTP es siempre genérico).
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def decode_access_token(token: str) -> str:
    """
    Decodifica y valida un JWT. Si es válido y no expiró, retorna el username.

    [SEC] Manejo de excepciones específico, no un except genérico:
      - ExpiredSignatureError: el token es válido criptográficamente pero
        venció. Se distingue de un token inválido porque en un sistema más
        grande ameritaría una respuesta distinta (ej. "tu sesión expiró,
        volvé a loguearte" vs "token corrupto/manipulado").
      - JWTError: cubre firma inválida, payload malformado, algoritmo no
        soportado — cualquier intento de manipular el token.

    Args:
        token (str): Token JWT recibido en el header Authorization.

    Returns:
        str: Username extraído del claim "sub".

    Raises:
        TokenValidationError: Si el token expiró, es inválido, o no
            contiene un claim "sub" (estructura inesperada).
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError:
        raise TokenValidationError("token_expired")
    except JWTError:
        raise TokenValidationError("token_invalid")

    username = payload.get("sub")
    if not username:
        # [SEC] Un token sin "sub" es estructuralmente inválido aunque la
        # firma sea correcta — no debería ocurrir con tokens emitidos por
        # create_access_token(), pero se valida defensivamente por si
        # alguien intenta forjar un token con un secreto filtrado.
        raise TokenValidationError("token_missing_subject")

    return username
