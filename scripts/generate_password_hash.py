#!/usr/bin/env python3
"""
Утилита для генерации bcrypt-хеша пароля.

Использование:
    python scripts/generate_password_hash.py "my_secret_password"
"""

import sys

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python scripts/generate_password_hash.py <password>")
        sys.exit(1)

    password = sys.argv[1]
    hashed = pwd_context.hash(password)
    print(hashed)


if __name__ == "__main__":
    main()