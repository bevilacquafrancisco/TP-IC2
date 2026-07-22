"""
Archivo: auth_schemas.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Modelos Pydantic de entrada/salida para el router de autenticación.
             Separados de la lógica de negocio (app/routers/auth.py) aplicando
             ISP (Interface Segregation Principle) — cada modelo expone
             exactamente los campos que su contexto necesita, ni más ni menos.
             Define los modelos de entrada y salida con límites explícitos de longitud, 
             evitando que un payload desmedido fuerce trabajo de cómputo innecesario en bcrypt.
Dependencias: pydantic
"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """
    Cuerpo esperado en POST /auth/login.

    [SEC] Se limita la longitud máxima de username/password con Field(...).
    Esto no es una validación de seguridad robusta por sí sola, pero mitiga
    un vector trivial de abuso: un cliente enviando un payload de varios
    megabytes como "password" para forzar trabajo de cómputo en bcrypt
    (bcrypt es deliberadamente costoso en CPU — es parte de su diseño,
    pero eso lo vuelve también un vector de DoS si no se acota el input).
    """

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """Cuerpo de respuesta exitosa de POST /auth/login."""

    access_token: str
    token_type: str = "bearer"
    username: str
    expires_in_minutes: int


class VerifyResponse(BaseModel):
    """Cuerpo de respuesta exitosa de GET /auth/verify."""

    username: str
    valid: bool = True


class ErrorResponse(BaseModel):
    """
    Forma estándar de error usada en todas las respuestas 4xx/5xx del backend.

    [SEC] Mensaje siempre genérico hacia el cliente (categoría OWASP #5,
    "Configuración de seguridad incorrecta": nunca exponer detalles internos
    como stack traces, nombres de excepción de librerías, o si el error fue
    "usuario no existe" vs "contraseña incorrecta" — eso permitiría
    enumerar usuarios válidos por fuerza bruta).
    """

    detail: str
