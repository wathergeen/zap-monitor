# Guia de Inicialização do Ambiente de Testes Local

Este guia detalha como configurar, inicializar e validar o ambiente de testes local do **ZapBot** (Monitor de grupos WhatsApp - Versão 2 Enxuta).

O sistema foi arquitetado para operar em **4 containers** (ou de forma híbrida local) sem Redis, sem Celery e sem Elasticsearch, utilizando o PostgreSQL tanto para persistência quanto como broker transacional assíncrono via **Procrastinate**, e conectando-se à Evolution API externa (ex.: Arcane).

---

## 1. Pré-requisitos na Máquina Host

- **Python 3.12+** instalado
- **uv** (gerenciador de pacotes e virtualenvs ultrarrápido): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node.js 20+** e **npm** (para compilação do Tailwind CSS)
- **Docker** e **Docker Compose** (caso deseje rodar a stack completa em containers)
- **Just** (opcional, para executar atalhos do `Justfile`): `cargo install just` ou via package manager

---

## 2. Opção A: Execução Local Rápida (Testes e Desenvolvimento Nativo)

Esta opção é a mais ágil para rodar testes automatizados e desenvolver novas funcionalidades sem exigir Docker ativo.

### Passo 1: Instalar Dependências e Configurar Ambiente
```bash
# Sincroniza ambiente virtual e dependências de desenvolvimento
uv sync --extra dev

# Instala ferramentas de build do Tailwind CSS
npm install

# Cria o arquivo de variáveis de ambiente a partir do exemplo
cp -n .env.example .env
```

### Passo 2: Compilar Assets Estáticos (Tailwind CSS)
```bash
npm run build:css
```

### Passo 3: Executar Migrações do Banco de Dados
Para executar testes rápidos e validações locais sem subir o container do PostgreSQL, utilize a flag `USE_SQLITE=1`:
```bash
USE_SQLITE=1 uv run python manage.py migrate
```

Para conectar em uma instância local do PostgreSQL:
```bash
uv run python manage.py migrate
```

### Passo 4: Criar Usuário Administrador
```bash
USE_SQLITE=1 uv run python manage.py createsuperuser
```

### Passo 5: Iniciar o Servidor Web de Desenvolvimento
```bash
USE_SQLITE=1 uv run python manage.py runserver 0.0.0.0:8000
```
Acesse no navegador: `http://localhost:8000`

### Passo 6: Iniciar o Worker Assíncrono (Procrastinate)
Em um terminal separado:
```bash
uv run python manage.py procrastinate worker --concurrency 4
```

---

## 3. Opção B: Execução Completa via Docker Compose (4 Containers)

Esta opção inicializa a stack da aplicação, composta por 4 containers:
1. `proxy` (Caddy 2 com TLS e proxy reverso na porta 8000)
2. `web` (Django 5 + Gunicorn `gthread` 2 workers × 4 threads)
3. `worker` (Procrastinate Worker conectado ao PostgreSQL com concorrência 4)
4. `postgres` (PostgreSQL 16 com extensões `unaccent` e `pg_trgm`)

A integração com a **Evolution API** adota arquitetura **Multi-Bot**: as conexões com as instâncias do WhatsApp são cadastradas e gerenciadas **exclusivamente pela tela de configurações web** (`/settings/`), não dependendo do arquivo `.env`. Cada bot cadastrado possui sua URL, chave de API e segredo de webhook individuais.

### Passo 1: Subir a Stack
```bash
docker compose up -d
```
ou utilizando o Just:
```bash
just up
```

### Passo 2: Acompanhar Logs e Saúde dos Containers
```bash
docker compose ps
docker compose logs -f
```

### Passo 3: Executar Migrações e Criar Superusuário no Container
```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

### Passo 4: Acessar a Aplicação
- Painel Administrativo / Dashboard: `http://localhost:8000`
- Webhook de Ingestão: `http://localhost:8000/webhooks/evolution/`
- Healthcheck: `http://localhost:8000/healthz`

---

## 4. Execução de Testes Automatizados e Qualidade

O projeto possui cobertura de testes automatizados com meta mínima de 85%, além de verificações de SAST, linters e formatação.

### Testes Automatizados com Cobertura
```bash
# Via Just
just test

# Ou diretamente via uv
USE_SQLITE=1 uv run pytest --cov=apps --cov-report=term-missing --cov-fail-under=85
```

### Análise Estática de Código e Linters (Ruff)
```bash
just lint
# ou
uv run ruff check . && uv run ruff format --check .
```

### Análise de Segurança (SAST com Bandit)
```bash
just sast
# ou
uv run bandit -r apps/ core/
```

### Auditoria de Dependências (pip-audit)
```bash
just audit
# ou
uv run pip-audit
```

### Verificação de Segurança para Produção (Django Check Deploy)
```bash
just check-deploy
```

---

## 5. Simulação de Carga e Idempotência do Webhook

Para testar a capacidade de ingestão contínua do webhook (meta de 20 msgs/s por 60 segundos com p95 < 100ms e detecção de duplicadas):

1. Garanta que o servidor web e o worker estejam em execução.
2. Cadastre previamente um grupo monitorado no painel ou via shell:
```bash
uv run python manage.py shell -c "from apps.groups.models import Group; Group.objects.get_or_create(jid='120363041234567890@g.us', defaults={'name': 'Grupo Teste Carga', 'monitored': True})"
```
3. Execute o script de teste de carga:
```bash
just loadtest
# ou
uv run python scripts/loadtest_webhook.py --rate 20 --duration 60 --group-jid 120363041234567890@g.us
```
4. Após o teste, valide os registros agregados executando a reconciliação:
```bash
just rebuild-stats
```

