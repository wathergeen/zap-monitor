# Prompt de desenvolvimento: Monitor de grupos WhatsApp (Evolution API + Django, versão enxuta)

> Arquivo de instrução para o assistente de desenvolvimento. Siga estas especificações como fonte da verdade do projeto.
> Versão 2: sem Redis e sem Celery. A fila de tarefas usa o próprio PostgreSQL. Foco em baixo consumo de recursos e em escalar para mais grupos sem reescrever.

---

## 1. Papel e objetivo

Atue como dev sênior (Python, Django, PostgreSQL, SQL, Docker, CI/CD, segurança de aplicações). Construa do zero um sistema web **somente leitura** para monitorar grupos de WhatsApp via **Evolution API**. O sistema recebe mensagens por webhook, armazena, e entrega ao administrador:

- lista de participantes atuais de cada grupo, com histórico de entrada e saída;
- quantidade de mensagens por pessoa, por período;
- conteúdo das mensagens de cada pessoa;
- se a pessoa esteve ativa em um determinado mês;
- se a pessoa citou palavras-chave cadastradas, quando e quantas vezes.

O bot **nunca envia mensagens**.

**Princípios de projeto (em ordem de prioridade):**

1. **Segurança** e privacidade.
2. **Simplicidade operacional**: o menor número possível de containers e dependências.
3. **Baixo consumo**: a stack inteira deve rodar confortavelmente em uma VPS de 2 vCPU e 2 GB de RAM.
4. **Escalabilidade previsível**: aguentar dezenas de grupos ativos sem reescrever, apenas ajustando configuração e, mais adiante, aplicando as otimizações da seção 12.
5. **Usabilidade** do painel.

Antes de codar, apresente um plano curto (arquitetura, estrutura de pastas, ordem de entrega) e depois implemente por etapas, com testes em cada uma.

## 2. Stack obrigatória

- Python 3.12, Django 5.x LTS. DRF **não** é necessário: o webhook é uma view Django simples.
- PostgreSQL 16 (fonte da verdade **e** fila de tarefas), driver `psycopg` 3 com pool de conexões.
- **procrastinate** (`procrastinate.contrib.django`) como fila e agendador de tarefas periódicas, usando o Postgres (LISTEN/NOTIFY). **Sem Redis e sem Celery.** Se houver impedimento técnico, a alternativa aceita é o `Django-Q2` com ORM como broker; justifique a troca antes de implementar.
- Evolution API v2 em container, com volume persistente.
- Front-end: templates Django + **HTMX + Alpine.js + Tailwind CSS** (build via CLI, sem CDN em produção) e Chart.js para gráficos. Sem SPA.
- Servidor: Gunicorn (`gthread`) atrás de Caddy (TLS automático). Arquivos estáticos com WhiteNoise.
- Cache: `DatabaseCache` do Django (usado por rate limit) ou cache local; **sem Redis**.
- Gerenciador de dependências: `uv` ou `pip-tools`, com versões fixadas e hashes.
- **Sem Elasticsearch** nesta versão. A busca usa PostgreSQL FTS atrás de uma interface abstrata (seção 8).

## 3. Funcionalidades

### 3.1 Ingestão (rápida e à prova de perda)

- Endpoint `POST /webhooks/evolution/` que recebe os eventos `MESSAGES_UPSERT`, `MESSAGES_UPDATE`, `MESSAGES_DELETE`, `GROUP_PARTICIPANTS_UPDATE` e `GROUP_UPDATE`. Habilitar na Evolution **somente** esses eventos.
- A view é **síncrona e mínima**: valida o segredo, descarta rapidamente grupos não monitorados e, em **uma única transação**, grava o `WebhookEvent` e enfileira o job do procrastinate (enfileiramento transacional: se o commit falhar, nada é perdido nem enfileirado pela metade). Responde `200` em poucos milissegundos.
- O processamento pesado acontece no worker: normalizar, gravar `Message`, atualizar estatísticas e detectar palavras-chave.
- Filtrar apenas mensagens de grupo (`remoteJid` terminando em `@g.us`) de grupos **cadastrados como monitorados**. Ignorar o resto sem armazenar.
- Idempotência: `Message.wa_id` com `UNIQUE`. Uso de `INSERT ... ON CONFLICT DO NOTHING`; as estatísticas só são incrementadas quando a mensagem foi realmente inserida.
- Mensagem editada ou apagada: manter a original, registrar `edited_at` e `deleted_at`, nunca sobrescrever silenciosamente. Por padrão, mensagem apagada **continua contando** como enviada nas métricas (configurável e documentado).
- Ignorar mensagens enviadas pelo próprio número do bot (`fromMe`).
- Persistir o `raw` do evento em `JSONB` apenas em `WebhookEvent` (com retenção curta, ex.: 7 dias) e guardar em `Message` só os campos necessários. Isso reduz disco e dados pessoais.
- Tarefas com retry e backoff exponencial, idempotentes. Jobs que esgotarem as tentativas ficam visíveis na tela de administração (fila de falhas) para reprocessamento manual.

### 3.2 Participantes

- Sync inicial e periódico (tarefa periódica do procrastinate, com `queueing_lock` para não sobrepor execuções) via endpoint de participantes da Evolution.
- Tratar o mapeamento entre IDs `@lid` e número de telefone para não duplicar membros. Guardar ambos quando disponíveis.
- Histórico: `GroupMembership(group, member, role, joined_at, left_at NULL)`. "Presente agora" = `left_at IS NULL`.
- Registrar mudança de papel (membro, admin, superadmin).

### 3.3 Métricas (lidas de tabelas agregadas, não da tabela de mensagens)

- Mensagens por pessoa (total, por mês, por dia, por tipo de mídia).
- "Ativo no mês": pelo menos 1 mensagem **enviada** no mês. Reações, leitura e presença não contam. O fuso horário de referência (`America/Sao_Paulo`) é configurável.
- Ranking, tendência mensal e lista de inativos (presentes no grupo e sem mensagens no período).
- Lista de mensagens por pessoa com filtro por data, tipo e palavra-chave, com paginação por cursor (keyset), nunca `OFFSET` em tabelas grandes.
- Comando de gestão `rebuild_stats` para reconstruir as agregações a partir de `Message` (reconciliação e recuperação).

### 3.4 Palavras-chave

- CRUD de palavras-chave por grupo (com variações, sem diferenciar acentos e maiúsculas, com correspondência por palavra inteira).
- **Detecção no momento da ingestão**: o worker normaliza o texto (`casefold` + remoção de acentos) e grava `KeywordHit` para cada palavra-chave ativa encontrada. Assim, o relatório vira uma consulta simples em tabela pequena, sem varrer mensagens.
- Ao cadastrar uma palavra-chave nova, uma tarefa em lote faz o **backfill** do histórico (em blocos, com pausa entre blocos para não competir com a ingestão), usando a **mesma função de normalização** da ingestão.
- Relatório: quem citou, quantas vezes, em quais mensagens, em que data, limitado ao período em que a pessoa esteve no grupo.
- Busca textual livre com PostgreSQL FTS (`tsvector` com config `portuguese`, índice GIN, `unaccent`) e `pg_trgm` para tolerância a erro de digitação.
- Destacar o termo encontrado no trecho exibido, de forma segura (seção 6).

### 3.5 Exportação

- Exportar relatórios em CSV, em streaming (sem carregar tudo em memória), protegendo contra CSV injection (prefixar com apóstrofo células que começam com `=`, `+`, `-`, `@`). Limite de linhas por exportação. Toda exportação gera registro de auditoria.

## 4. Modelo de dados

- `Group(jid UNIQUE, name, monitored BOOL, created_at)`
- `Member(id, phone NULL, lid NULL, push_name, first_seen_at)` com unicidade parcial para `phone` e `lid`
- `GroupMembership(group, member, role, joined_at, left_at NULL)` com índice em `(group, left_at)`
- `Message(wa_id UNIQUE, group, member, type, text, sent_at, edited_at NULL, deleted_at NULL, search_vector)`
  - Índices: `(group_id, sent_at DESC)`, `(member_id, sent_at DESC)` e GIN em `search_vector`. `search_vector` é coluna gerada.
  - Não duplicar o payload bruto aqui.
- `MemberMonthlyStats(group, member, month, message_count, first_message_at, last_message_at)` com `UNIQUE(group, member, month)`. Atualizada por upsert (`ON CONFLICT DO UPDATE SET message_count = message_count + 1`) **na mesma transação** que insere a mensagem.
- `MemberDailyStats(group, member, day, message_count)` para os gráficos diários (mesma lógica).
- `Keyword(group, term, term_normalized, active)`
- `KeywordHit(keyword, message, group, member, sent_at)` com índices em `(group, keyword, sent_at)` e `(member, keyword)`.
- `WebhookEvent(id, received_at, status, payload JSONB, error)` com limpeza automática por retenção curta.
- `AuditLog(user, action, target, ip, user_agent, created_at)` (somente inserção)

Todas as migrações devem ser reversíveis. Tabelas do procrastinate ficam no mesmo banco, em schema ou prefixo próprio.

## 5. Autenticação e autorização (básico, funcional e seguro)

- Login por e-mail e senha usando o sistema do Django, com modelo de usuário customizado desde o início.
- Sem cadastro público. Usuários são criados pelo admin via comando `createsuperuser` ou painel interno. Convite por link de uso único com expiração.
- Hash de senha com **Argon2** (`argon2-cffi`). Validadores de senha: mínimo 12 caracteres, bloqueio de senhas comuns, bloqueio de senhas similares aos dados do usuário.
- Proteção contra força bruta com `django-axes` (bloqueio por combinação usuário + IP, tempo de bloqueio configurável, aviso genérico na tela). Backend de armazenamento do axes: banco de dados.
- Mensagens de erro de login **genéricas**, sem revelar se o e-mail existe. Fluxo de recuperação de senha com resposta idêntica para e-mail existente e inexistente.
- **2FA TOTP** com `django-otp` (opcional em dev, **obrigatório em produção** para papel admin), com códigos de recuperação armazenados com hash.
- Sessão: cookie `HttpOnly`, `Secure`, `SameSite=Lax`, expiração por inatividade (ex.: 30 min) e tempo máximo absoluto (ex.: 12 h), rotação do ID de sessão no login, logout invalida a sessão no servidor. Sessões no banco (`django.contrib.sessions` com backend de banco) e limpeza periódica por tarefa agendada.
- Papéis: `admin` (tudo), `analista` (leitura e relatórios), `leitor` (só visualização, sem exportar). Permissões checadas em toda view e em toda query (menor privilégio, negar por padrão).
- Proteção de objeto: usuário só vê os grupos aos quais foi atribuído (evitar IDOR; testar explicitamente).
- Log de auditoria para login, logout, falha de login, exportação, alteração de permissões e consulta a conteúdo de mensagens.

## 6. Segurança da aplicação

- `DEBUG=False` por padrão. Settings separados (`base`, `dev`, `prod`). A aplicação **não sobe em produção** se faltar `SECRET_KEY`, `ALLOWED_HOSTS` ou segredos obrigatórios (falhar na inicialização com mensagem clara).
- Configurações de produção obrigatórias: `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS` (≥ 31536000), `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS=DENY`, `SECURE_REFERRER_POLICY`, `CSRF_TRUSTED_ORIGINS` explícito, `SECURE_PROXY_SSL_HEADER` configurado corretamente para o proxy.
- **CSP** estrita com `django-csp`, sem `unsafe-inline` (usar nonce). Assets locais, sem CDN externo. Cabeçalhos `Permissions-Policy` e `Cross-Origin-Opener-Policy`.
- CSRF ativo em todos os formulários e em requisições HTMX (enviar o token via `hx-headers`).
- Escapar toda saída (autoescape do Django). **Nunca** usar `|safe` em conteúdo de mensagem: mensagens de WhatsApp são entrada não confiável. O destaque de palavra-chave é feito no servidor, escapando o texto primeiro e só depois envolvendo os trechos em `<mark>`.
- Sem SQL cru concatenado; usar ORM e queries parametrizadas (inclusive nas migrações que usam SQL).
- Limite de taxa (rate limit) no login, na recuperação de senha, nas exportações e na API interna, com `django-ratelimit` usando `DatabaseCache`, e também no proxy quando disponível.
- Limite de tamanho do corpo da requisição no webhook e na aplicação. Timeouts em todas as chamadas HTTP de saída.
- Respostas autenticadas com `Cache-Control: no-store`.
- Tratamento de erro: páginas 400/403/404/500 amigáveis, sem stack trace nem detalhes internos.
- Logs estruturados em JSON, **sem** senhas, tokens, nem conteúdo de mensagens. Mascarar telefones nos logs. Integração opcional com Sentry com scrubbing de dados pessoais.

## 7. Segurança do webhook e da Evolution API

- O webhook exige um **segredo compartilhado** enviado em cabeçalho customizado (configurado nos headers do webhook da Evolution), comparado em tempo constante (`hmac.compare_digest`). Segredo longo e aleatório, rotacionável sem downtime (aceitar dois segredos durante a troca).
- A Evolution API **não é exposta à internet**. Ela fica numa rede Docker interna, acessível só pelo Django e pelo worker. O webhook é recebido pela rede interna.
- Chave global da Evolution (`AUTHENTICATION_API_KEY`) forte, vinda de segredo, nunca no repositório. Painel/manager da Evolution desabilitado ou restrito à rede interna/VPN.
- Configurar a instância para **não** enviar confirmação de leitura nem presença (`alwaysOnline=false`, `readMessages=false`, `readStatus=false`, `rejectCall=false`) e proibir qualquer chamada de envio de mensagem no código. Adicionar um teste automatizado que falha se o código chamar endpoints `sendText`/`sendMedia`.
- **Reduzir a Evolution ao mínimo** (menos disco, menos RAM, menos dados pessoais duplicados). Confirmar os nomes exatos das variáveis na documentação da versão fixada:
  - desativar o armazenamento próprio de mensagens, atualizações de mensagem, contatos, chats e histórico (`DATABASE_SAVE_DATA_*`), já que o Django é a fonte da verdade;
  - usar cache local (`CACHE_LOCAL_ENABLED=true`) e **desligar o Redis** (`CACHE_REDIS_ENABLED=false`);
  - desativar integrações não usadas (chatbots, Typebot, RabbitMQ, SQS, websocket etc.);
  - webhook sem base64 de mídia.
- A Evolution usa o **mesmo servidor PostgreSQL** (banco e usuário próprios, sem acesso ao banco da aplicação e vice-versa).
- Volume persistente para a sessão do WhatsApp, com permissões restritas e backup criptografado. Evitar reconectar via QR repetidamente.
- Fixar a versão da imagem da Evolution por **digest** e atualizar de forma planejada.
- Mídias: por padrão **não** baixar nem armazenar arquivos, apenas metadados (tipo, tamanho, legenda).

## 8. Busca

- Interface `SearchBackend` com uma implementação: `PostgresFTSBackend`. O restante do código só conhece a interface.
- Elasticsearch **fora do escopo** desta versão. Só considerar quando houver centenas de milhões de mensagens ou necessidade real de fuzzy avançado, sinônimos ou dashboards. Documentar em `docs/escalabilidade.md` como seria a troca (índice reconstruível a partir do Postgres, `_id` = `wa_id`, analyzer `portuguese` + `asciifolding`).
- Testes de contrato escritos contra a interface, para facilitar uma troca futura.

## 9. Front-end (moderno, rápido e acessível)

- Layout responsivo (mobile first), tema claro e escuro (respeitando `prefers-color-scheme`), tipografia limpa, espaçamento consistente, componentes reutilizáveis (partials/`include`).
- Telas:
  1. **Login** (com campo de 2FA quando aplicável, foco automático, mostrar/ocultar senha)
  2. **Dashboard**: cards (total de mensagens, membros ativos no mês, inativos, novas entradas/saídas), gráfico de mensagens por dia e ranking dos mais ativos, lidos das tabelas agregadas
  3. **Grupos**: lista, estado do monitoramento, última mensagem recebida, saúde da conexão com a Evolution e da fila de tarefas
  4. **Participantes** do grupo: tabela com busca, filtros (presente/saiu, ativo/inativo, papel), ordenação, paginação, data de entrada e saída
  5. **Perfil do participante**: total de mensagens, atividade por mês (heatmap ou barras), linha do tempo de mensagens, palavras-chave citadas
  6. **Palavras-chave**: cadastro, status do backfill e relatório por período
  7. **Configurações, auditoria e fila de falhas** (somente admin)
- Interações com HTMX (filtros e paginação sem recarregar a página, estados de carregamento, mensagens de erro inline). Sem frameworks pesados de SPA.
- Usabilidade: busca global com atalho de teclado, filtros persistidos na URL (links compartilháveis), estados vazios com orientação, confirmações em ações destrutivas, feedback visual de sucesso e erro.
- Acessibilidade (WCAG 2.1 AA): contraste, foco visível, navegação por teclado, rótulos em todos os campos, `aria-live` para atualizações dinâmicas, HTML semântico.
- Interface em português do Brasil, datas e números no formato `pt-BR`, fuso configurável.
- Telefones mascarados por padrão (ex.: `+55 11 9****-1234`), com opção de revelar para papéis autorizados (e registro em auditoria).
- Performance: `select_related`/`prefetch_related`, agregações lidas das tabelas de estatísticas, paginação por cursor, orçamento de assets pequeno (CSS/JS minificados, com hash no nome, compressão gzip/brotli no proxy).

## 10. Privacidade e LGPD

- Registrar a base legal e a finalidade no `README`/`docs/privacidade.md`. Modelo de aviso aos participantes do grupo (texto sugerido) incluído na documentação.
- Política de retenção configurável (ex.: apagar ou anonimizar mensagens com mais de N meses) via tarefa periódica, apagando **em lotes** para não travar o banco. Agregados são recalculados ou mantidos de forma anonimizada, conforme a configuração.
- Comandos de gestão para exportar e **excluir/anonimizar** todos os dados de uma pessoa (atendimento a titular), incluindo `KeywordHit` e estatísticas.
- Minimização: não armazenar mídia, status, nem dados de grupos não monitorados; não manter cópia das mensagens na Evolution.
- Criptografia em repouso: volume/disco do servidor e backups criptografados.

## 11. Docker e infraestrutura

### 11.1 Serviços (apenas 5 containers)

`proxy` (Caddy), `web` (Django/Gunicorn), `worker` (procrastinate: tarefas e agendamentos), `postgres` e `evolution`. **Sem Redis, sem Celery beat, sem Elasticsearch.** Migrações rodam em um job único (`migrate`) antes de `web` e `worker` subirem.

### 11.2 Orçamento de recursos (metas para VPS de 2 vCPU / 2 GB)

Definir `mem_limit` e `cpus` no compose de produção com valores iniciais próximos de:

| Serviço | Memória (limite) | Observações |
|---|---|---|
| proxy | 64 MB | Caddy |
| web | 384 MB | Gunicorn `gthread`: 2 workers × 4 threads, `--max-requests 1000 --max-requests-jitter 100`, `--worker-tmp-dir /dev/shm` |
| worker | 256 MB | concorrência inicial 4, ajustável por variável de ambiente |
| postgres | 512 MB | ver ajustes abaixo |
| evolution | 512 MB | com integrações desligadas |

Ajustes iniciais do PostgreSQL (via parâmetros do container, documentados e revisáveis): `shared_buffers=256MB`, `effective_cache_size=768MB`, `work_mem=8MB`, `maintenance_work_mem=64MB`, `max_connections=50`, `random_page_cost=1.1` (SSD), `wal_compression=on`, `log_min_duration_statement=500`. Habilitar `pg_stat_statements`. Autovacuum ativo, com ajuste mais agressivo nas tabelas de estatísticas, que recebem muitos updates.

### 11.3 Regras de segurança da infraestrutura

- **Somente o proxy publica portas (80/443)**. Postgres, Evolution e demais serviços ficam em redes internas, sem `ports:`.
- Redes separadas: `frontend` (proxy ↔ web) e `backend` (web/worker/evolution ↔ postgres), com `internal: true` no backend.
- Imagem da aplicação em **multi-stage build**, base `python:3.12-slim` fixada por digest, usuário **não root**, `read_only: true` com `tmpfs` onde necessário, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, sem ferramentas de build na imagem final, `.dockerignore` completo. A mesma imagem serve `web`, `worker` e `migrate` (muda só o comando).
- Healthchecks em todos os serviços, `restart: unless-stopped`, `depends_on` com `condition: service_healthy`.
- Segredos: variáveis via arquivo `.env` **fora do Git** (com `.env.example` sem valores reais) e, em produção, Docker secrets ou gerenciador de segredos. Nunca colocar segredo em `Dockerfile`, imagem ou log.
- Postgres: senha forte, usuário da aplicação **sem** permissão de superusuário nem de criar bancos; usuário separado para migrações; usuário separado para a Evolution; `pg_hba.conf` restritivo; TLS se algum dia sair da rede interna.
- Proxy: TLS 1.2+ com HSTS, redirecionamento HTTP→HTTPS, limite de tamanho de corpo, cabeçalhos de segurança, remoção do cabeçalho `Server`, compressão, restrição de IP para `/admin/` ou desabilitar o admin padrão do Django (ou mudar a rota e proteger com 2FA).
- Rotação de logs do Docker (`max-size`/`max-file`) para não encher o disco.
- Observabilidade: logs em JSON, endpoint `/healthz` (sem dados sensíveis), métricas básicas (tamanho da fila do procrastinate, latência do webhook, mensagens ingeridas por minuto, jobs falhos) e alerta quando **nenhuma mensagem chegar por X horas** (indício de sessão do WhatsApp caída).
- Backup: `pg_dump` agendado, criptografado, com retenção e **teste de restauração documentado** (runbook). Backup do volume da sessão do WhatsApp separado e criptografado.

## 12. Escalabilidade (o que fazer quando crescer)

Registrar em `docs/escalabilidade.md` uma escada de otimizações, **sem implementar antes de precisar**, cada degrau com o gatilho de uso:

1. **Mais grupos no mesmo número**: custo marginal baixo (o gargalo é o volume de mensagens, não o número de grupos). Aumentar a concorrência do worker e os limites de memória.
2. **Mais conexões**: PgBouncer em modo *transaction* para o `web`. O worker do procrastinate precisa de conexão **direta** (LISTEN/NOTIFY não funciona em transaction pooling).
3. **Tabela `Message` acima de ~20 milhões de linhas**: particionamento por mês (`PARTITION BY RANGE (sent_at)`). Atenção: a restrição de unicidade passa a incluir a chave de partição (por exemplo `UNIQUE (wa_id, sent_at)`), e o Django exige migrações com SQL explícito. A retenção passa a ser `DROP PARTITION`, que é quase instantâneo.
4. **Leitura pesada**: réplica de leitura para relatórios e exportações.
5. **Vários números do WhatsApp**: uma instância da Evolution por número, distribuindo grupos entre elas (também reduz o risco de banimento concentrado).
6. **Busca**: só então avaliar Elasticsearch/OpenSearch atrás da interface `SearchBackend`.

Incluir um script `scripts/loadtest_webhook.py` que simula 20 mensagens/s por 60 s (com duplicatas propositais) e verifica: nenhuma mensagem perdida, nenhuma duplicada, latência p95 do webhook abaixo de 100 ms e fila drenando em seguida.

## 13. CI/CD

Pipeline (GitHub Actions ou GitLab CI) com estas etapas, todas obrigatórias para o merge:

1. Lint e formatação (`ruff`, `ruff format`), tipagem (`mypy` ou `pyright`)
2. Testes (`pytest`, `pytest-django`, cobertura mínima de 85%) contra Postgres real em container (sem Redis)
3. `python manage.py check --deploy` com falha em qualquer aviso
4. Verificação de migrações pendentes (`makemigrations --check`) e aplicação do procrastinate (`procrastinate schema`)
5. SAST: `bandit`; dependências: `pip-audit`; segredos: `gitleaks`
6. Build da imagem e varredura com `trivy` (falha em severidade alta/crítica)
7. Geração de SBOM
8. Deploy automático para staging; produção com aprovação manual, migrações antes de subir a nova versão e rollback documentado

Outras exigências: branch `main` protegida, revisão obrigatória, commits assinados quando possível, Dependabot/Renovate ativo, `pre-commit` configurado (ruff, gitleaks, detecção de chaves privadas).

## 14. Testes obrigatórios

- Webhook: segredo inválido (401/403), payload malformado, duplicado (idempotência), grupo não monitorado, mensagem do próprio bot, mensagem editada e apagada.
- Fila: worker interrompido no meio de um job (o job é retomado e não duplica nada), job que falha várias vezes vai para a fila de falhas, enfileiramento transacional (falha no commit não deixa job órfão).
- Estatísticas: contagem mensal e diária consistente com `Message`; `rebuild_stats` reproduz exatamente os mesmos números; reentrega de evento não incrementa duas vezes.
- Métricas: ativo/inativo no limite do mês e com fuso horário diferente; membro que saiu e voltou.
- Palavras-chave: acentos, maiúsculas, palavra inteira, backfill produz o mesmo resultado que a ingestão.
- Autorização: usuário sem acesso ao grupo não vê dados (IDOR), cada papel só faz o que pode, endpoints exigem login.
- Segurança: XSS armazenado via texto de mensagem (`<script>`, atributos, HTML), CSV injection, CSRF, bloqueio do `django-axes`, cookies com as flags corretas, cabeçalhos de segurança presentes.
- Busca: acentos, plural, maiúsculas, erro de digitação.
- Teste que garante que **não existe** chamada de envio de mensagem à Evolution.

## 15. Entregáveis e ordem de execução

1. Plano e estrutura do projeto
2. Docker Compose (dev) com Evolution, Postgres e Django
3. Modelos, migrações, ingestão do webhook e fila (procrastinate) com testes
4. Estatísticas agregadas e `rebuild_stats`
5. Sync de participantes e histórico
6. Autenticação, papéis, 2FA, auditoria
7. Palavras-chave (ingestão, backfill, relatório) e busca (Postgres FTS)
8. Front-end completo
9. Hardening de produção (compose prod, limites de recursos, proxy, settings, segredos)
10. CI/CD e script de teste de carga
11. Documentação: `README`, `docs/seguranca.md`, `docs/privacidade.md`, `docs/escalabilidade.md`, `docs/runbook.md` (deploy, rotação de segredos, restauração de backup, o que fazer se o número for banido ou a sessão cair, como reprocessar jobs falhos)

## 16. Critérios de aceite

- `docker compose -f docker-compose.prod.yml up` sobe os 5 containers com health verde e apenas as portas 80/443 expostas.
- Consumo total de memória em operação normal (um grupo ativo) abaixo de cerca de 1,5 GB, e a stack funcionando em 2 vCPU / 2 GB.
- Teste de carga: 20 mensagens/s sustentadas, sem perda nem duplicação, webhook com p95 abaixo de 100 ms.
- Dashboard e relatório de palavras-chave respondem rápido (meta: abaixo de 300 ms no p95) com 1 milhão de mensagens de teste.
- `manage.py check --deploy` sem avisos.
- Nenhum segredo no repositório nem nas imagens.
- Todos os testes e varreduras do CI passam.
- Um usuário sem permissão não consegue ler dados de nenhum grupo.
- Mensagem com HTML/JS malicioso é exibida como texto inofensivo.
- O sistema se recupera sozinho se o worker, o Postgres ou a Evolution ficarem indisponíveis por alguns minutos, sem perder nem duplicar mensagens.
- Nenhuma funcionalidade envia mensagem pelo WhatsApp.

## 17. Regras de conduta para o assistente

- Se uma decisão tiver trade-off relevante (segurança × usabilidade, simplicidade × desempenho), explique em duas ou três linhas e escolha a opção mais segura e simples por padrão.
- Não adicione dependências nem containers fora desta especificação sem justificar. Cada nova peça precisa de um motivo mensurável.
- Não invente endpoints, variáveis ou parâmetros da Evolution API nem do procrastinate: consulte a documentação da versão fixada e deixe os pontos incertos marcados com `TODO` e uma nota.
- Entregue em partes pequenas e testáveis, rodando testes e linters a cada etapa, e resuma ao final de cada etapa o que foi feito e o que falta.