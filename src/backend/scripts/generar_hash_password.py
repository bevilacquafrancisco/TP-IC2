"""
Archivo: generar_hash_password.py
Autor: Francisco Bevilacqua
Fecha: 2026-06-19
Versión: 1.0.0
Descripción: Script utilitario de línea de comandos para generar el hash
             bcrypt de una contraseña. El resultado se pega en la variable
             de entorno AUTH_USERS del archivo .env (NUNCA en .env.example).

Uso:
    python scripts/generar_hash_password.py

[SEC] Este script NUNCA guarda la contraseña en texto plano en ningún
archivo ni log. Se ingresa interactivamente con getpass (no queda en el
historial de la shell como pasaría con un argumento de línea de comandos
tipo "python script.py mi_contraseña").
"""

import getpass
import sys
from pathlib import Path

# Permite ejecutar el script desde la raíz del proyecto sin instalar el
# paquete app/ — agrega backend/ al path de búsqueda de módulos.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402


def main() -> None:
    """Punto de entrada del script: pide usuario y contraseña, imprime el par listo para .env."""
    print("=" * 70)
    print("Generador de hash bcrypt para AUTH_USERS (.env)")
    print("=" * 70)

    username = input("Nombre de usuario (ej. francisco): ").strip()
    if not username:
        print("[ERROR] El nombre de usuario no puede estar vacío.")
        sys.exit(1)

    # [SEC] getpass.getpass() no muestra la contraseña en pantalla mientras
    # se tipea, y no queda en el historial de comandos de la terminal.
    password = getpass.getpass("Contraseña (no se muestra al tipear): ")
    password_confirm = getpass.getpass("Confirmar contraseña: ")

    if password != password_confirm:
        print("[ERROR] Las contraseñas no coinciden. Intentá de nuevo.")
        sys.exit(1)

    if len(password) < 8:
        print("[ADVERTENCIA] La contraseña tiene menos de 8 caracteres.")
        print("Se recomienda longitud > 8 sobre complejidad forzada (RNF-007).")

    hashed = hash_password(password)

    print("\n" + "=" * 70)
    print("Hash generado. Copiá la siguiente línea en tu .env real,")
    print("agregándola a AUTH_USERS (separada por coma si hay otros usuarios):")
    print("=" * 70)
    print(f"\n{username}:{hashed}\n")


if __name__ == "__main__":
    main()
