import os
import sys

from .base import *

DEBUG = True

ALLOWED_HOSTS = list(
    set(
        list(ALLOWED_HOSTS)
        + [
            "localhost",
            "127.0.0.1",
            "testserver",
            "web",
            "0.0.0.0",
            "192.168.0.9",
            "192.168.0.12",
            "*",
        ]
    )
)

# Em desenvolvimento, assegura que todas as origens derivadas de ALLOWED_HOSTS sejam confiáveis para CSRF
for host in ALLOWED_HOSTS:
    if host not in ("localhost", "127.0.0.1", "testserver", "web", "*"):
        for scheme in ("http", "https"):
            CSRF_TRUSTED_ORIGINS.extend(
                [
                    f"{scheme}://{host}",
                    f"{scheme}://{host}:8000",
                    f"{scheme}://{host}:38080",
                ]
            )
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(CSRF_TRUSTED_ORIGINS))

# Permite desenvolvimento e testes locais com SQLite quando configurado via USE_SQLITE=1
# ou durante execução do pytest sem container PostgreSQL ativo.
if os.getenv("USE_SQLITE", "0") == "1" or (
    "pytest" in sys.modules and os.getenv("USE_POSTGRES_TESTS", "0") != "1"
):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
    # Procrastinate utiliza DDL e funções exclusivas de PostgreSQL (LISTEN/NOTIFY e schemas).
    # Desativa migrações do procrastinate apenas ao rodar em SQLite.
    MIGRATION_MODULES = {
        "procrastinate": None,
    }
else:
    db_host = os.getenv("DB_HOST", "localhost")
    if db_host == "postgres":
        import socket

        try:
            socket.gethostbyname("postgres")
        except (socket.gaierror, OSError):
            db_host = "localhost"

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "zapbot_db"),
            "USER": os.getenv("DB_USER", "zapbot_user"),
            "PASSWORD": os.getenv("DB_PASSWORD", "zapbot_strong_password"),
            "HOST": db_host,
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }

# Em desenvolvimento e testes, cache local em memória (rápido e sem dependência externa)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "zapbot-dev-locmem-cache",
    }
}

# Em desenvolvimento, 2FA não é forçado para admins por padrão
TOTP_ENFORCE_ADMIN = False

# Storage de arquivos estáticos simples para dev/testes (sem exigir collectstatic prévio)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Chave padrão de segurança para testes em dev caso não informada
if not SECRET_KEY:
    SECRET_KEY = "django-insecure-dev-key-only-for-local-testing-purposes"  # nosec B105
