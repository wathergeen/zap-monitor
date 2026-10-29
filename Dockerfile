# ==============================================================================
# ESTÁGIO 1: Compilação de Assets de Frontend (Tailwind CSS)
# ==============================================================================
FROM node:22-slim AS frontend-builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --ignore-scripts
COPY tailwind.config.js ./
COPY static ./static
COPY templates ./templates
COPY apps ./apps
RUN npm run build:css

# ==============================================================================
# ESTÁGIO 2: Builder de Dependências Python
# ==============================================================================
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS python-builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --user --require-hashes -r requirements.txt

# ==============================================================================
# ESTÁGIO 3: Imagem Final de Execução (Segura, Sem Ferramentas de Build, Non-Root)
# ==============================================================================
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS final

# Criar usuário não-root com permissões estritas
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -s /bin/bash -m appuser

# Instalar apenas bibliotecas de runtime necessárias (libpq e utilitários)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar dependências Python do builder
COPY --from=python-builder /root/.local /home/appuser/.local

# Copiar arquivos de frontend compilados
COPY --from=frontend-builder /app/static/css/styles.css /app/static/css/styles.css

# Copiar código da aplicação
COPY --chown=appuser:appgroup . /app

# Criar diretórios de arquivos estáticos e temporários com permissões adequadas
RUN mkdir -p /app/staticfiles /app/backups && \
    chown -R appuser:appgroup /app

USER appuser
ENV PATH="/home/appuser/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["web"]

