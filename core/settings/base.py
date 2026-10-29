import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Carregar arquivo .env na inicialização caso exista
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "")

DEBUG = False

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

# Aplicações instaladas
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Terceiros
    "axes",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "csp",
    "procrastinate.contrib.django",
    # Módulos locais da aplicação
    "apps.accounts",
    "apps.groups",
    "apps.messages_monitor",
    "apps.keywords",
    "apps.search",
    "apps.analytics",
    "apps.audit",
    "apps.health",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "csp.middleware.CSPMiddleware",
    "axes.middleware.AxesMiddleware",
    "core.middleware.SessionTimeoutMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.csrf",
                "apps.accounts.context_processors.user_role_context",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

# Modelo de Usuário Customizado e Autenticação
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 12,
        },
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internacionalização e Fuso Horário
LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.getenv("TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True

# Arquivos Estáticos e Storage
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Configurações de Sessão
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", 1800))  # 30 minutos de inatividade
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", 43200))  # 12 horas absolutas
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Proteção CSRF
CSRF_COOKIE_HTTPONLY = False  # Permitir leitura por script HTMX via cookie csrftoken
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origin.strip()
]

# Limite de tamanho de requisição (2.5 MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = 2621440
FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440

# Django Axes (Proteção contra Força Bruta)
AXES_FAILURE_LIMIT = int(os.getenv("AXES_FAILURE_LIMIT", 5))
AXES_COOLOFF_TIME = int(os.getenv("AXES_COOLOFF_TIME", 1))
AXES_LOCKOUT_CALLABLE = "apps.accounts.views.lockout_view"
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_VERBOSE = False

# Content Security Policy (CSP Estrita com Nonce)
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'", "'unsafe-eval'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
        "connect-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
    }
}
CSP_INCLUDE_NONCE_IN = ["script-src"]

# Cache da aplicação (DatabaseCache - sem Redis)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "zapbot_cache_table",
    }
}

# Configurações do Procrastinate (PostgreSQL LISTEN/NOTIFY - sem Celery e sem Redis)
# As tarefas e workers utilizam a conexão do PostgreSQL configurada no Django.

# Configurações da Evolution API (Multi-Bot dinâmico via modelo EvolutionInstance)
# Os bots/instâncias são cadastrados e gerenciados exclusivamente pela tela de configurações web.
# As variáveis abaixo permanecem vazias por padrão e funcionam apenas como fallback legado opcional.
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "")
EVOLUTION_WEBHOOK_SECRET = os.getenv("EVOLUTION_WEBHOOK_SECRET", "")
EVOLUTION_WEBHOOK_SECRET_OLD = os.getenv("EVOLUTION_WEBHOOK_SECRET_OLD", "")

# 2FA Enforcement
TOTP_ENFORCE_ADMIN = os.getenv("TOTP_ENFORCE_ADMIN", "False").lower() in ("true", "1", "yes")

# Busca Textual (PostgreSQL FTS padrão)
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "postgres")

# Retenção LGPD e Alertas de Inatividade
RETENTION_MONTHS = int(os.getenv("RETENTION_MONTHS", 12))
WEBHOOK_INACTIVITY_ALERT_HOURS = int(os.getenv("WEBHOOK_INACTIVITY_ALERT_HOURS", 6))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Logging Estruturado em JSON (Sem dados sensíveis e com telefones mascarados)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json_safe": {
            "()": "core.logging_formatter.SafeJsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json_safe",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
