"""
Archivo: config.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Carga centralizada de configuración del backend desde variables
             de entorno (.env). Punto único de acceso a secretos — ningún
             otro módulo del backend lee os.environ directamente.
Dependencias: python-dotenv, pydantic-settings
"""

import sys
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuración del backend, validada con Pydantic al arrancar.

    [SEC] Fail-fast: si una variable de entorno obligatoria falta o tiene
    un formato inválido, la aplicación NO debe arrancar silenciosamente
    con un valor por defecto insegura (ej. un JWT_SECRET_KEY de ejemplo).
    Pydantic lanza ValidationError inmediatamente en ese caso, lo cual es
    el comportamiento deseado: un backend de auth con un secreto débil es
    peor que un backend que no arranca.

    Attributes:
        jwt_secret_key (str): Clave de firma HMAC para los JWT.
        jwt_algorithm (str): Algoritmo de firma (HS256 por defecto).
        jwt_expire_minutes (int): Minutos de validez de cada token emitido.
        auth_users_raw (str): String crudo "user:hash,user:hash" desde .env.
        cors_origins_raw (str): String crudo de orígenes permitidos, separados por coma.
        app_host (str): Host de bind de uvicorn.
        app_port (int): Puerto de bind de uvicorn.
        debug_mode (bool): Si True, habilita /docs y logs detallados.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    auth_users_raw: str = Field(validation_alias="AUTH_USERS")
    cors_origins_raw: str = Field(
        default="http://localhost:5500", validation_alias="CORS_ORIGINS"
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug_mode: bool = True

    @property
    def auth_users(self) -> dict[str, str]:
        """
        Parsea AUTH_USERS ("user1:hash1,user2:hash2") a un diccionario.

        Returns:
            dict[str, str]: Mapeo username -> hash bcrypt.

        Raises:
            ValueError: Si el formato del string es inválido (protege contra
                un .env mal editado a mano que rompería el login silenciosamente).
        """
        users: dict[str, str] = {}
        for entry in self.auth_users_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ValueError(
                    f"[SEC] Entrada inválida en AUTH_USERS: '{entry}'. "
                    "Formato esperado 'usuario:hash_bcrypt'."
                )
            username, password_hash = entry.split(":", 1)
            users[username.strip()] = password_hash.strip()
        return users

    @property
    def cors_origins(self) -> list[str]:
        """Parsea CORS_ORIGINS a una lista de orígenes permitidos."""
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


def load_settings() -> Settings:
    """
    Carga y valida la configuración. Punto de entrada único usado por main.py.

    [SEC] Si falta el .env o una variable obligatoria, el proceso termina
    con un mensaje claro en vez de arrancar en un estado parcialmente
    configurado (ej. sin usuarios cargados, lo que dejaría el login
    siempre devolviendo 401 sin ninguna pista de por qué).

    Returns:
        Settings: instancia validada de configuración.
    """
    try:
        return Settings()
    except Exception as exc:
        print("=" * 70)
        print("[CRITICAL] Error cargando configuración desde .env")
        print(f"Detalle: {exc}")
        print("Verificá que existe backend/.env (copiado desde .env.example)")
        print("y que AUTH_USERS / JWT_SECRET_KEY están completos.")
        print("=" * 70)
        sys.exit(1)


# Instancia única reutilizada por toda la app (patrón Singleton de facto,
# ver SKILLS.md sección 3 — patrones de diseño aplicables).
settings = load_settings()
