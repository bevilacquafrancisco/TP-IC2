"""
Archivo: dependencies.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Dependencias inyectables de FastAPI. Centraliza la lógica de
             "¿este request trae un JWT válido?" en un solo lugar reutilizable
             por cualquier endpoint que necesite protegerse — hoy es solo
             /auth/verify, pero esta misma dependencia es la que se reusaría
             en futuros endpoints (ej. si el backend creciera para incluir
             un WebSocket relay autenticado, como menciona la planificación
             original de Fase 2 extendida).
             Centraliza la extracción y validación del JWT en una única dependencia de FastAPI
Dependencias: fastapi

Responsabilidad única (SRP): este módulo SOLO resuelve "¿quién es el
operador autenticado de este request?". No conoce reglas de negocio.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.security import TokenValidationError, decode_access_token

# [SEC] HTTPBearer exige el header "Authorization: Bearer <token>".
# FastAPI documenta esto automáticamente en /docs como esquema de
# seguridad, lo cual sirve para la demo (podés mostrar el botón
# "Authorize" en Swagger UI durante la defensa).
bearer_scheme = HTTPBearer()


def get_current_username(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """
    Dependencia de FastAPI: extrae y valida el JWT del header Authorization,
    devolviendo el username del operador si el token es válido.

    Cualquier endpoint que declare este parámetro queda automáticamente
    protegido: FastAPI ejecuta esta función ANTES del cuerpo del endpoint,
    y si lanza HTTPException, el cuerpo del endpoint nunca se ejecuta.

    [SEC] Las dos causas de fallo (token expirado, token inválido) se
    distinguen internamente en security.py (TokenValidationError.reason)
    pero acá se devuelve el MISMO código y mensaje genérico 401 para
    ambos casos. Esto es deliberado: no se le da al cliente información
    para distinguir "tu token venció" de "tu token está corrupto/forjado",
    lo cual no aporta valor al operador legítimo (en ambos casos la
    acción correcta es la misma: volver a loguearse) y sí podría dar
    pistas a un atacante que esté probando tokens manipulados.

    Args:
        credentials: Inyectado automáticamente por FastAPI desde el
            header "Authorization: Bearer <token>".

    Returns:
        str: Username del operador autenticado.

    Raises:
        HTTPException: 401 si el token es inválido, expiró, o no se
            proporcionó correctamente.
    """
    try:
        return decode_access_token(credentials.credentials)
    except TokenValidationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado. Inicie sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
