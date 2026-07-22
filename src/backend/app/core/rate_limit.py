"""
Archivo: rate_limit.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Rate limiting en memoria para POST /auth/login, mitigando
             ataques de fuerza bruta sobre credenciales.

[SEC] Decisión de alcance: este limitador es en memoria de proceso (un
diccionario Python), no distribuido (no usa Redis). Para un backend de
un solo proceso uvicorn sirviendo una demo académica en LAN, esto es
suficiente y evita una dependencia de infraestructura adicional. Si el
backend escalara a múltiples workers/instancias, este mecanismo NO
funcionaría correctamente (cada worker tendría su propio contador) y
se debería migrar a un store compartido (Redis). Documentado como
limitación conocida en README.md.
"""

import time
from collections import defaultdict

# [SEC] Máximo de intentos fallidos permitidos por IP en la ventana de tiempo.
MAX_ATTEMPTS = 5

# [SEC] Ventana deslizante en segundos. 5 intentos fallidos cada 60s es
# suficientemente permisivo para un operador que se equivoca tipeando,
# pero corta de raíz un script de fuerza bruta automatizado.
WINDOW_SECONDS = 60

# Estructura: { "ip_origen": [timestamp1, timestamp2, ...] }
# Solo se registran intentos FALLIDOS — un login exitoso no cuenta contra
# el límite, para no penalizar a un operador legítimo que se equivocó
# una vez y después acertó.
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def is_rate_limited(client_ip: str) -> bool:
    """
    Verifica si una IP superó el máximo de intentos fallidos permitidos
    dentro de la ventana de tiempo configurada.

    Args:
        client_ip (str): Dirección IP del cliente (obtenida de request.client.host).

    Returns:
        bool: True si la IP debe ser bloqueada temporalmente, False si puede intentar.
    """
    _purge_old_attempts(client_ip)
    return len(_failed_attempts[client_ip]) >= MAX_ATTEMPTS


def register_failed_attempt(client_ip: str) -> None:
    """
    Registra un intento de login fallido para la IP dada.

    Args:
        client_ip (str): Dirección IP del cliente que falló la autenticación.
    """
    _failed_attempts[client_ip].append(time.time())


def reset_attempts(client_ip: str) -> None:
    """
    Limpia el historial de intentos fallidos de una IP tras un login exitoso.

    Args:
        client_ip (str): Dirección IP del cliente que se autenticó correctamente.
    """
    _failed_attempts.pop(client_ip, None)


def _purge_old_attempts(client_ip: str) -> None:
    """
    Elimina timestamps fuera de la ventana deslizante (limpieza interna).

    Args:
        client_ip (str): IP cuyo historial se va a depurar.
    """
    cutoff = time.time() - WINDOW_SECONDS
    _failed_attempts[client_ip] = [
        ts for ts in _failed_attempts[client_ip] if ts > cutoff
    ]
