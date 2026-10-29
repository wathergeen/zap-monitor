# Guia de Configuração do Ambiente de Desenvolvimento (Dev-Env)

Este documento orienta os desenvolvedores na configuração rápida, segura e padronizada do ambiente local para o **ZapMonitor** (Versão 2 Enxuta: 5 containers, sem Redis e sem Celery, fila via PostgreSQL com Procrastinate).

---

## 1. Pré-requisitos de Sistema

Certifique-se de possuir instalado em sua máquina de trabalho:
- **Python 3.12+**
- **uv** (gerenciador de pacotes e ambientes virtuais Python ultra-rápido)
- **Node.js 20+** e **npm** (para compilação do Tailwind CSS via CLI)
- **Docker** e **Docker Compose v2+**
- **just** (executor de comandos do `Justfile`)

---

## 2. Instalação e Configuração Passo a Passo

### 2.1 Clonar o Repositório e Configurar Variáveis
```bash
cp .env.example .env
```
O arquivo `.env` gerado já contém configurações seguras e pré-populadas para o ambiente local.

### 2.2 Instalar Dependências e Compilar Assets
Utilize o `Justfile` para configurar o ambiente com um único comando:
```bash
just setup
```
Este comando executa:
1. `uv sync --extra dev` (cria `.venv/` e instala todas as dependências com hashes fixados).
2. `npm install` (instala Tailwind CSS, Alpine.js, HTMX e Chart.js).
3. `just build-css` (compila `static/css/styles.css` localmente).

---

## 3. Modos de Execução

Você pode executar o projeto de duas formas no ambiente local:

### Modo 1: Todos os Serviços em Docker (Recomendado - 4 Containers)
Sobe os 4 containers da stack principal: `proxy` (Caddy 2), `web` (Django 5/Gunicorn), `worker` (Procrastinate) e `postgres` (PostgreSQL 16):
```bash
# Subir todos os serviços em background
just up

# Acompanhar logs unificados
just logs

# Parar serviços
just down
```
Acesse a aplicação em `http://localhost:8000`.

### Modo 2: Desenvolvimento Híbrido Local
Para depurar código Python diretamente na IDE ou terminal:

1. Suba apenas o banco de dados PostgreSQL (porta 5432 já exposta para `localhost`):
```bash
docker compose up -d postgres
```

2. Execute as migrações e crie seu superusuário:
```bash
just migrate
just createsuperuser
```

3. Em terminais separados (ou via tmux/screen), inicie:
```bash
# Terminal 1: Servidor Web Django
just dev

# Terminal 2: Worker Assíncrono do Procrastinate
just worker

# Terminal 3 (opcional): Watcher de Tailwind CSS
npm run watch:css
```

---

## 4. Testes e Qualidade de Código

Antes de submeter alterações ou abrir Pull Requests:

```bash
# Executar a suíte completa de testes com cobertura mínima de 85%
just test

# Executar verificação de lint e formatação (Ruff)
just lint

# Aplicar correções automáticas de formatação
just format

# Executar varreduras de segurança estática (SAST - Bandit)
just sast

# Executar auditoria de dependências (pip-audit)
just audit

# Validar configurações de produção
just check-deploy

# Executar teste de carga no webhook (20 req/s por 60s)
just loadtest
```

---

## 5. Conectando a Evolution API (Gestão via Interface Web)

O ZapMonitor opera com arquitetura **Multi-Bot**: o gerenciamento de múltiplos bots e instâncias da **Evolution API v2** é realizado **EXCLUSIVAMENTE pela interface administrativa web**, sem necessidade de configurar bots no arquivo `.env` nem reiniciar containers.

### 5.1 Cadastro de Conexões pela Interface Web

1. Acesse o painel web em `http://localhost:8000/settings/` (logado como Administrador).
2. Clique na aba **"Bots & Conexões Evolution"**.
3. Clique em **"+ Nova Conexão / Bot"** e preencha:
   - **Nome de Identificação**: Nome amigável (ex.: "Bot Atendimento Comercial", "WhatsApp Matriz").
   - **URL da Evolution API**: Endereço do seu servidor Evolution API (ex.: `http://localhost:8080`, `https://evolution.minhaempresa.com` ou IP na rede).
   - **Nome da Instância**: O nome exato cadastrado na Evolution API (ex.: `Litaby_BOT_001`).
   - **Chave de API**: Chave global ou da instância (`apikey`).
   - **Segredo do Webhook (X-Webhook-Secret)**: Segredo compartilhado para validação HMAC desta instância.
   - **Ativo**: Mantenha marcado para habilitar o monitoramento.
4. Clique em **"Salvar Conexão"**.
5. Na listagem de conexões, utilize o botão **"Testar Conexão"** (ícone de recarregar) para verificar instantaneamente a conectividade e o status retornado pela Evolution API via HTMX.

### 5.2 Sincronização de Grupos de WhatsApp

Para importar ou atualizar os grupos em que o bot está inserido:
- **Pela Interface Web**:
  - Na página de **Grupos de WhatsApp** (`/groups/`), clique no botão **"Sincronizar Grupos"**.
  - Ou nas **Configurações** (`/settings/?tab=evolution`), clique em **"Sincronizar"** na linha correspondente ao bot.
- **Pela Linha de Comando (CLI)**:
  ```bash
  uv run python manage.py sync_groups
  # Ou para uma instância específica:
  uv run python manage.py sync_groups --instance Litaby_BOT_001
  ```

### 5.3 Configuração do Webhook no Servidor Evolution API

Para cada instância/bot cadastrado no painel web, configure o webhook no respectivo servidor da Evolution API:
- **URL**: `http(s)://<dominio-ou-ip>/webhooks/evolution/?secret=<segredo_da_instancia>`
  *(Como a interface web do Evolution Manager não possui campo para cabeçalhos HTTP customizados, o ZapMonitor aceita o segredo diretamente como parâmetro `?secret=...` ou `?token=...` na URL, além do cabeçalho `X-Webhook-Secret`).*
- **Eventos Monitorados**: Marcar `MESSAGES_UPSERT`, `MESSAGES_UPDATE`, `MESSAGES_DELETE`, `GROUP_PARTICIPANTS_UPDATE` e `GROUP_UPDATE`.

### 5.4 Checklist para Mensagens Aparecerem no Dashboard

Para que as mensagens recebidas via WhatsApp sejam processadas e exibidas no Dashboard:
1. **IP em `ALLOWED_HOSTS`**: O IP da máquina que roda o ZapMonitor (ex: `192.168.0.9`) deve estar presente no `ALLOWED_HOSTS` do `.env` (em desenvolvimento `dev.py`, qualquer host local é aceito). Caso contrário, a Evolution API recebe erro `400 Bad Request (DisallowedHost)`.
2. **Grupo Ativo como "Monitorado"**: Na tela de **Grupos** (`/groups/`), o grupo desejado deve estar com o status **Monitorado** (badge verde). Mensagens de grupos com monitoramento desligado são descartadas propositalmente por privacidade e LGPD.
3. **Worker do Procrastinate Rodando**: O processamento e indexação das mensagens ocorrem de forma assíncrona. Certifique-se de que o worker está em execução (`just worker` ou container `worker`).
4. **Mensagens de Terceiros**: Mensagens enviadas pelo próprio número conectado como bot possuem a flag `fromMe: true` e são descartadas pelo coletor para não poluir estatísticas de membros.

> [!NOTE]
> **Por que não configurar bots no `.env`?**
> O arquivo `.env` é reservado para configurações estáticas de infraestrutura da aplicação (banco de dados, portas, chaves do Django). Como o sistema suporta $N$ conexões com instâncias diferentes de bots e servidores da Evolution, todas as conexões, credenciais e segredos de webhook de cada bot são persistidos no banco de dados e gerenciados dinamicamente via interface web.

