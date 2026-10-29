# Guia de Escalabilidade e Arquitetura de Alto Volume

Este documento descreve as decisões arquiteturais da **Versão 2** do ZapBot, o dimensionamento para operação enxuta em servidores modestos (VPS de 2 vCPU e 2 GB de RAM) e o planejamento de transição para volumes massivos de dados.

---

## 1. Princípios Arquiteturais da Versão 2 (Enxuta)

A Versão 2 eliminou dependências pesadas e redundantes (Redis, Celery e Elasticsearch), concentrando o estado e o tráfego no **PostgreSQL 16**.

### 1.1 Ingestão Ultrarrápida e Transacional
- O endpoint `/webhooks/evolution/` valida o cabeçalho `X-Webhook-Secret` em tempo constante e realiza descarte antecipado de mensagens que não sejam de grupos monitorados (`@g.us`).
- Em uma única transação atômica (`transaction.atomic()`), o `WebhookEvent` é persistido e a tarefa do **Procrastinate** é enfileirada diretamente na tabela `procrastinate_jobs` do PostgreSQL.
- Se o commit do banco falhar, nenhum evento é aceito e nenhuma tarefa órfã é criada. O webhook responde `200` em menos de 10 milissegundos.

### 1.2 Tabelas Agregadas de Estatísticas
Em vez de varrer milhões de registros na tabela `Message` para calcular KPIs de dashboards e gráficos:
- `MemberMonthlyStats(group, member, month, message_count, first_message_at, last_message_at)` mantém o total mensal por membro via upsert na ingestão.
- `MemberDailyStats(group, member, day, message_count)` mantém os valores diários para gráficos temporais do Chart.js.
- O comando `rebuild_stats` permite recalcular e reconciliar todas as agregações a qualquer momento a partir das mensagens gravadas.

### 1.3 Detecção de Palavras-chave na Ingestão (`KeywordHit`)
- Em vez de rodar consultas `LIKE '%termo%'` ou varreduras lentas em tempo de relatório, o worker normaliza o texto (`casefold()` + `unaccent`) e verifica correspondência por palavra inteira na ingestão.
- Ocorrências são gravadas diretamente na tabela indexada `KeywordHit(keyword, message, group, member, sent_at)`.
- Consultas analíticas e relatórios de citações passam a ser leituras indexadas com tempo de resposta inferior a 50 ms.

---

## 2. Orçamento de Recursos (2 vCPU / 2 GB de RAM)

Os limites estritos definidos no `docker-compose.prod.yml` garantem estabilidade sem esgotamento de memória (OOM):

| Serviço | Limite de Memória | Limite de CPU | Papel e Otimizações |
|---|---|---|---|
| **proxy** (Caddy 2) | 64 MB | 0.5 CPU | Terminação TLS automática, compressão zstd/gzip, proxy reverso |
| **web** (Django 5) | 384 MB | 1.0 CPU | Gunicorn `gthread` (2 workers × 4 threads), max-requests 1000 |
| **worker** (Procrastinate) | 256 MB | 0.5 CPU | Concorrência 4, escuta via LISTEN/NOTIFY, agendador cron integrado |
| **postgres** (PostgreSQL 16) | 512 MB | 1.0 CPU | `shared_buffers=256MB`, `work_mem=8MB`, autovacuum agressivo |
| **evolution** (Evolution API) | 512 MB | 0.5 CPU | Cache local em memória, sem Redis, salvamento redundante desativado |
| **Total Alocado** | **~1.728 MB** | **2 vCPUs** | Operação com margem de folga confortável no host |

---

## 3. Otimizações de Banco de Dados

### 3.1 Parâmetros do PostgreSQL
- `shared_buffers = 256MB`
- `effective_cache_size = 768MB`
- `work_mem = 8MB`
- `maintenance_work_mem = 64MB`
- `max_connections = 50`
- `random_page_cost = 1.1` (SSD NVMe)
- `wal_compression = on`
- `log_min_duration_statement = 500`

### 3.2 Particionamento Futuro
Quando o volume de mensagens ultrapassar dezenas de milhões:
- Particionar a tabela `Message` por ano/mês (`PARTITION BY RANGE (sent_at)`).
- Os índices `idx_message_group_sent` e `idx_message_member_sent` passam a ser locais em cada partição, mantendo o tamanho dos índices menor do que a memória RAM disponível.

---

## 4. Plano de Transição para Elasticsearch (Volume Extremo)

O uso de Elasticsearch foi retirado da Versão 2 para manter a simplicidade operacional e viabilizar a stack em 2 GB de RAM. A busca textual do ZapBot foi desacoplada atrás da interface abstrata `SearchBackend`.

### Quando considerar a adoção de Elasticsearch?
Apenas se:
1. O acervo ultrapassar centenas de milhões de mensagens com buscas concorrentes simultâneas em texto aberto.
2. Houver necessidade de buscas fonéticas complexas, sinônimos customizados ou algoritmos de relevância BM25 refinados que justifiquem o consumo de memória adicional (mínimo 2 GB a 4 GB dedicados apenas ao cluster ES).

### Como realizar a migração sem quebrar a aplicação:
1. Adicionar o container do Elasticsearch no Compose com volume persistente.
2. Definir `SEARCH_BACKEND=elasticsearch` nas variáveis de ambiente.
3. Executar o comando de reconstrução do índice a partir da fonte da verdade (PostgreSQL):
   ```bash
   python manage.py reindex_messages
   ```
4. Configurações recomendadas no Elasticsearch:
   - Identificador do documento: `_id = Message.wa_id` (idempotência natural).
   - Analisador: `portuguese` com filtro de tokens `asciifolding` e `lowercase`.
   - Mapeamento: campos `group_id`, `member_id`, `sent_at`, e campo `text` indexado para busca full-text.

