"""
Archivo: main.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Punto de entrada de la API de autenticación del sistema
             Pick & Place. Configura la app FastAPI, CORS, manejo global
             de excepciones, y registra los routers.
Dependencias: fastapi, uvicorn

Para ejecutar (desde la carpeta backend/):
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.routers import auth

# -----------------------------------------------------------------------------
# Logging — auditabilidad (categoría OWASP #9)
# -----------------------------------------------------------------------------
# [SEC] Se configura un logger explícito en vez de dejar el default de
# uvicorn sin estructura. NUNCA se loguea el campo "password" de ningún
# request — solo eventos a nivel de aplicación (errores no manejados).
logging.basicConfig(
    level=logging.INFO if not settings.debug_mode else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("robot_auth_api")


app = FastAPI(
    title="Robot Pick & Place — Auth API",
    description=(
        "Backend de autenticación JWT para el panel de control del brazo "
        "robótico. Proyecto Final IC2 — Francisco Bevilacqua, UNRAF."
    ),
    version="1.0.0",
    # [SEC] /docs y /redoc quedan deshabilitados si DEBUG_MODE=False.
    # Mantenerlos habilitados en un despliegue real expone la superficie
    # completa de la API a cualquiera que la encuentre.
    docs_url="/docs" if settings.debug_mode else None,
    redoc_url="/redoc" if settings.debug_mode else None,
)


# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
# [SEC] allow_origins viene de settings.cors_origins (parseado desde .env),
# NUNCA "*". El diseño preliminar de este backend usaba allow_origins=["*"],
# lo cual permitiría que CUALQUIER sitio web hiciera peticiones autenticadas
# contra este backend desde el navegador de un operador logueado — esto es
# la categoría OWASP #5 ("Configuración de seguridad incorrecta"). Corregido
# en esta implementación final. """allow_origins=["*"],"""
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# -----------------------------------------------------------------------------
# Manejo global de excepciones no controladas
# -----------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Captura cualquier excepción no manejada explícitamente por un endpoint.

    [SEC] Categoría OWASP #5: nunca devolver al cliente un stack trace,
    el tipo de excepción Python, ni el mensaje interno de la excepción
    (podría revelar rutas de archivos, nombres de librerías, estructura
    interna). El detalle real se loguea server-side para diagnóstico;
    el cliente solo recibe un mensaje genérico 500.

    Args:
        request (Request): request que disparó la excepción.
        exc (Exception): excepción capturada.

    Returns:
        JSONResponse: error 500 genérico, sin detalles internos.
    """
    logger.error(f"Excepción no manejada en {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Error interno del servidor."},
    )


# -----------------------------------------------------------------------------
# Routers
# -----------------------------------------------------------------------------
app.include_router(auth.router)


# -----------------------------------------------------------------------------
# Endpoint de salud — para verificar rápidamente que el backend está arriba
# (útil al iniciar la demo: confirmar el backend antes de abrir la GUI)
# -----------------------------------------------------------------------------
@app.get("/health", tags=["Salud"])
async def health() -> dict:
    """
    Verifica que el backend está operativo. No requiere autenticación.

    Returns:
        dict: estado del servicio.
    """
    return {"status": "ok", "service": "robot-auth-api", "version": "1.0.0"}
