import os

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

# Validação estrita de inicialização em Produção
if (
    not SECRET_KEY
    or len(SECRET_KEY) < 40
    or "django-insecure" in SECRET_KEY
    or "change-me" in SECRET_KEY
):
    raise ImproperlyConfigured(
        "FALHA CRÍTICA DE SEGURANÇA: SECRET_KEY inválida, insegura ou ausente em produção! "
        "Defina uma chave aleatória e forte de no mínimo 50 caracteres."
    )

if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "FALHA CRÍTICA DE SEGURANÇA: ALLOWED_HOSTS não pode estar vazio nem conter wildcard '*' em produção!"
    )

# Conexões e segredos com a Evolution API são cadastrados dinamicamente no banco de dados (EvolutionInstance)
# pela tela web de configurações. O EVOLUTION_WEBHOOK_SECRET em .env é estritamente opcional (fallback legado).

db_password = os.getenv("DB_PASSWORD")
if not db_password or db_password == "zapbot_strong_password":  # nosec B105
    raise ImproperlyConfigured(
        "FALHA CRÍTICA DE SEGURANÇA: Defina uma senha forte para DB_PASSWORD em produção diferente do valor padrão!"
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "zapbot_db"),
        "USER": os.getenv("DB_USER", "zapbot_user"),
        "PASSWORD": db_password,
        "HOST": os.getenv("DB_HOST", "postgres"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 600,
    }
}

# Cabeçalhos e políticas de segurança exigidas para produção
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 ano mínimo
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# 2FA Obrigatório em Produção para administradores
TOTP_ENFORCE_ADMIN = True
