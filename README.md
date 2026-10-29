# ZapMonitor: Monitor de Grupos WhatsApp (Evolution API + Django - Versão 2 Enxuta)

Sistema web de nível empresarial, **estritamente somente leitura**, para monitoramento e auditoria de grupos de WhatsApp via **Evolution API v2**, registrando participantes, histórico de entrada/saída, volume de mensagens, palavras-chave e métricas de atividade com total conformidade à LGPD.

> [!IMPORTANT]
> **POLÍTICA DE SOMENTE LEITURA**:
> O sistema opera de forma passiva. Nenhuma função de envio de mensagens (`sendText`, `sendMedia`, `sendVoice`, etc.) existe ou é permitida na aplicação. Há um teste automatizado contínuo no CI que falha caso qualquer chamada a endpoints de envio seja introduzida no código.

---

## 1. Stack Tecnológica e Arquitetura (Versão 2 Enxuta)

- **Backend**: Python 3.12, Django 5.2 LTS, Gunicorn `gthread` (2 workers × 4 threads).
- **Banco de Dados & Fila de Tarefas**: PostgreSQL 16 como fonte da verdade **e** broker transacional assíncrono via **Procrastinate** (LISTEN/NOTIFY). **Sem Redis e sem Celery**.
- **Tabelas Agregadas de Estatísticas**: `MemberMonthlyStats` e `MemberDailyStats` atualizadas no momento da ingestão para consultas analíticas O(1) sem varredura em tabelas gigantes.
- **Detecção de Palavras-chave na Ingestão**: O worker normaliza o texto (`casefold()` + `unaccent`) e grava diretamente em `KeywordHit`, eliminando buscas lentas em tempo de relatório.
- **Gateway WhatsApp Multi-Bot**: Suporte a múltiplos bots e instâncias simultâneas da Evolution API v2, cadastrados e gerenciados **exclusivamente via interface administrativa web** (Configurações > Bots & Conexões), sem dependência de variáveis no `.env`. Cada bot possui seu próprio segredo de webhook, URL, chave e controle de estado em tempo real.
- **Frontend Moderno**: Django Templates + **HTMX** (interações assíncronas) + **Alpine.js** + **Tailwind CSS** (build nativo via CLI, sem CDNs externos) + **Chart.js** (gráficos responsivos com suporte a modo escuro/claro).
- **Proxy Reverso**: Caddy 2 com TLS automático, compressão brotli/gzip e cabeçalhos de segurança (HSTS, CSP com nonce).
- **Segurança da Aplicação**: Argon2 para hashing de senhas, `django-axes` contra força bruta, autenticação em duas etapas TOTP (`django-otp`) obrigatória para administradores em produção, proteção anti-IDOR por atribuição de grupo, imunização contra CSV injection e mascaramento padrão de telefones com auditoria sob demanda.

---

## 2. Início Rápido (Ambiente de Desenvolvimento e Testes)

Consulte os guias detalhados:
- [docs/ambiente_testes_local.md](docs/ambiente_testes_local.md): Instruções passo a passo de inicialização e testes locais.
- [dev-env.md](dev-env.md): Guia de desenvolvimento e atalhos operacionais.
- [docs/escalabilidade.md](docs/escalabilidade.md): Orçamento de recursos (2 vCPU / 2GB RAM) e plano de alto volume.

### 2.1 Inicialização Rápida com Just
```bash
# 1. Configurar variáveis de ambiente a partir do modelo
cp .env.example .env

# 2. Configurar dependências (Python uv + Node npm) e compilar assets
just setup

# 3. Subir os serviços em containers (Proxy, Web, Worker, Postgres)
just up

# 4. Criar superusuário inicial
just createsuperuser
```

Acesse o painel web em `http://localhost:8000`.

---

## 3. Ambiente de Produção (Meta: 2 vCPU / 2 GB de RAM)

Em produção, **somente as portas 80 e 443 do proxy Caddy são expostas**. Os demais serviços operam em rede interna (`backend` com `internal: true`).

```bash
# 1. Configurar variáveis de produção com segredos fortes
cp .env.example .env.prod

# 2. Iniciar os 4 serviços com limites estritos de recursos
just prod-up

# 3. Verificar conformidade de deploy do Django
just check-deploy
```

Consulte o [docs/runbook.md](docs/runbook.md) para runbooks operacionais de deploy, rotação de segredos sem downtime e recuperação de desastres.

---

## 4. Comandos de Automação (`Justfile`)

| Comando | Descrição |
| :--- | :--- |
| `just setup` | Instala dependências Python (uv) e Node (npm), compila assets |
| `just up` | Inicia os 5 containers do ambiente de desenvolvimento |
| `just down` | Para os containers de desenvolvimento |
| `just dev` | Executa o servidor Django localmente |
| `just worker` | Inicia o worker assíncrono do Procrastinate |
| `just test` | Executa a suíte de testes com cobertura mínima de 85% |
| `just lint` | Executa Ruff linter e verificação de formatação |
| `just format` | Aplica correções automáticas de código e estilo |
| `just sast` | Executa análise estática de segurança com Bandit |
| `just audit` | Executa auditoria de vulnerabilidades em dependências com pip-audit |
| `just rebuild-stats` | Reconstrói tabelas de estatísticas agregadas a partir de Message |
| `just loadtest` | Executa teste de carga no webhook (20 req/s por 60s) |
| `just backup` | Gera backup compactado do PostgreSQL |
| `just restore <arquivo>` | Restaura banco de dados a partir de arquivo de dump |

---

## 5. Documentação do Projeto

- [docs/ambiente_testes_local.md](docs/ambiente_testes_local.md): Como subir e rodar o ambiente de testes local.
- [docs/escalabilidade.md](docs/escalabilidade.md): Detalhes de escalabilidade, agregações e Procrastinate.
- [docs/CLOUDFLARE_SETUP_GUIDE.md](docs/CLOUDFLARE_SETUP_GUIDE.md): Integração com Cloudflare Tunnel (Zero Portas Abertas) e WAF.
- [docs/seguranca.md](docs/seguranca.md): Políticas de cabeçalhos, CSP com nonce, Argon2, 2FA e anti-IDOR.
- [docs/privacidade.md](docs/privacidade.md): Conformidade com LGPD, base legal, aviso para grupos e anonimização de titular.
- [docs/runbook.md](docs/runbook.md): Procedimentos de deploy, rotação de segredos, reprocessamento de falhas e contingência.
