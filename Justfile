# ==============================================================================
# Justfile - Automação Operacional do ZapBot (Versão 2 Enxuta)
# Stack: Django 5, PostgreSQL 16 (Dados e Fila Procrastinate), Caddy, Evolution API
# ==============================================================================

# Exibe os comandos disponíveis
default:
    @just --list

# Instala todas as dependências do projeto (Python via uv e Frontend via npm)
setup:
    uv sync --extra dev
    npm install
    cp -n .env.example .env || true
    @echo "Setup concluído! Revise o arquivo .env com suas configurações."

# Compila o Tailwind CSS para o arquivo estático local
build-css:
    npm run build:css

# Executa migrações do banco de dados (Django e Procrastinate)
migrate:
    uv run python manage.py migrate

# Cria usuário administrador inicial
createsuperuser:
    uv run python manage.py createsuperuser

# Reconstrói tabelas agregadas de estatísticas (MemberMonthlyStats e MemberDailyStats)
rebuild-stats:
    uv run python manage.py rebuild_stats

# Inicia o servidor web local em modo de desenvolvimento (uso: just dev [porta])
dev port="8000":
    uv run python manage.py runserver 0.0.0.0:{{port}}

# Inicia o worker assíncrono do Procrastinate localmente
worker:
    uv run python manage.py procrastinate worker --concurrency 4

# Executa bateria de testes automatizados com cobertura de código
test:
    USE_SQLITE=1 uv run pytest --cov=apps --cov-report=term-missing --cov-fail-under=85

# Executa análise estática de código e formatação (ruff)
lint:
    uv run ruff check .
    uv run ruff format --check .

# Formata arquivos Python com ruff
format:
    uv run ruff format .
    uv run ruff check --fix .

# Executa análise estática de segurança (SAST com Bandit)
sast:
    uv run bandit -r apps/ core/

# Executa auditoria de vulnerabilidades em dependências (pip-audit)
audit:
    uv run pip-audit

# Executa verificação rigorosa de configurações para produção
check-deploy:
    DJANGO_SETTINGS_MODULE=core.settings.prod \
    SECRET_KEY="production_dummy_key_just_for_local_syntax_check_50_chars_min" \
    ALLOWED_HOSTS="localhost" \
    DB_PASSWORD="dummy_strong_password_for_check" \
    uv run python manage.py check --deploy

# Sobe ambiente completo de desenvolvimento em containers Docker (5 containers)
up:
    docker compose up -d

# Para os containers do ambiente de desenvolvimento
down:
    docker compose down

# Visualiza logs dos containers de desenvolvimento
logs:
    docker compose logs -f

# Sobe ambiente de produção com limites estritos de recursos (2 vCPU / 2GB RAM)
prod-up:
    docker compose -f docker-compose.prod.yml up -d

# Para containers de produção
prod-down:
    docker compose -f docker-compose.prod.yml down

# Executa teste de carga no webhook (20 req/s durante 60s com duplicatas)
loadtest:
    uv run python scripts/loadtest_webhook.py --rate 20 --duration 60

# Realiza backup comprimido do banco PostgreSQL
backup:
    @mkdir -p backups
    docker compose exec -T postgres pg_dump -U ${DB_USER:-zapbot_user} ${DB_NAME:-zapbot_db} | gzip > backups/backup_$(date +%Y%m%d_%H%M%S).sql.gz
    @echo "Backup salvo em backups/"

# Restaura o banco PostgreSQL a partir de um arquivo (Uso: just restore backups/arquivo.sql.gz)
restore file:
    @gunzip < {{file}} | docker compose exec -T postgres psql -U ${DB_USER:-zapbot_user} -d ${DB_NAME:-zapbot_db}
    @echo "Restauração finalizada."

