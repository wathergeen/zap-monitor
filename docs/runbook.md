# Runbook de Operações, Manutenção e Recuperação de Desastres

Este guia operacional contém instruções detalhadas para deploy, rotação de chaves sem downtime, testes de restauração de backup e procedimentos de contingência caso a sessão do WhatsApp caia ou o número seja banido.

---

## 1. Procedimento de Deploy em Produção

### 1.1 Pré-requisitos
- Servidor Linux (Ubuntu 22.04 LTS ou superior recomendado) com Docker Engine 24+ e Docker Compose plugin.
- Acesso à rede e portas de firewall: **apenas 80 e 443 liberadas externamente** (ou zero portas caso utilize Cloudflare Tunnel).

### 1.2 Passo a Passo do Deploy
```bash
# 1. Configurar variáveis de ambiente de produção
cp .env.example .env.prod
# Edite .env.prod com senhas reais, SECRET_KEY >= 50 caracteres e domínios válidos
chmod 600 .env.prod

# 2. Subir os serviços em modo produção
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 3. Verificar a integridade de todos os containers
docker compose -f docker-compose.prod.yml ps

# 4. Criar o primeiro superusuário administrador
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser

# 5. Ativar a verificação em duas etapas (2FA) no primeiro login em /accounts/2fa/setup/
```

---

## 2. Rotação de Segredos sem Downtime

### 2.1 Rotação do Segredo de Webhook (`X-Webhook-Secret`)
O ZapMonitor adota arquitetura Multi-Bot onde cada bot possui seu próprio segredo de webhook. O sistema aceita simultaneamente a chave atual e a chave antiga no banco de dados (`EvolutionInstance`) para evitar perda de mensagens durante a transição:

1. Gere um novo segredo aleatório forte:
   ```bash
   openssl rand -hex 32
   ```
2. Na interface web administrativa do ZapMonitor (`/settings/`):
   - Localize o bot desejado na aba **"Bots & Conexões Evolution"**.
   - Clique em **"Editar"** e cole o novo segredo no campo **"Segredo do Webhook (X-Webhook-Secret)"**.
   - Salve a conexão. O sistema automaticamente preserva o segredo anterior como chave de rotação sem downtime.
3. No painel ou API da Evolution API, atualize o cabeçalho customizado `X-Webhook-Secret` para o novo valor gerado.
4. Nenhuma reinicialização de containers nem alteração no `.env` é necessária.

### 2.2 Rotação de Senha do Banco de Dados PostgreSQL
1. Crie um novo usuário no PostgreSQL ou altere a senha:
   ```bash
   docker compose -f docker-compose.prod.yml exec postgres psql -U zapbot_user -d zapbot_db -c "ALTER USER zapbot_user WITH PASSWORD 'nova_senha_forte';"
   ```
2. Atualize `DB_PASSWORD` no `.env.prod`.
3. Reinicie os serviços dependentes:
   ```bash
   docker compose -f docker-compose.prod.yml up -d web worker beat
   ```

---

## 3. Disaster Recovery: Backup e Restauração Testada

### 3.1 Execução de Backup Criptografado
```bash
# Executa dump com compactação e criptografia AES-256
BACKUP_PASSPHRASE="sua_chave_mestra_de_backup" ./scripts/backup.sh
```
Os arquivos são armazenados no diretório relativo `./backups/` com retenção automática de 7 dias.

### 3.2 Teste de Restauração em Ambiente Isolado
Para validar que o backup é íntegro e restaurável:

```bash
# 1. Identifique o arquivo de backup mais recente
BACKUP_FILE=$(ls -t backups/*.sql.gz* | head -n 1)

# 2. Execute a restauração (para arquivos .enc, passe a passphrase)
BACKUP_PASSPHRASE="sua_chave_mestra_de_backup" ./scripts/restore.sh "$BACKUP_FILE"

# 3. Valide a integridade dos dados restaurados
uv run python manage.py shell -c "from apps.messages_monitor.models import Message; print(f'Total de mensagens restauradas: {Message.objects.count()}')"
```

---

## 4. Contingência: Sessão Desconectada ou Número Banido

### 4.1 Identificação do Incidente
O sistema detecta e alerta automaticamente quando:
- O endpoint `/healthz` reporta `"evolution_api": "disconnected"` ou `"unreachable"`.
- Nenhuma mensagem for recebida nos grupos monitorados por mais de 6 horas (`check-webhook-inactivity`).
- Logs exibem warnings de inatividade prolongada.

### 4.2 Procedimento para Reconexão de Sessão (Desconexão Simples)
1. Acesse o servidor e verifique os logs do container da Evolution API:
   ```bash
   docker compose -f docker-compose.prod.yml logs --tail=100 evolution
   ```
2. Solicite a geração de um novo QR Code via curl interno:
   ```bash
   docker compose -f docker-compose.prod.yml exec web python -c "
   import httpx
   from apps.groups.models import EvolutionInstance
   inst = EvolutionInstance.objects.filter(active=True).first()
   url = f'{inst.api_url}/instance/connect/{inst.instance_name}'
   r = httpx.get(url, headers={'apikey': inst.api_key})
   print(r.json())
   "
   ```
3. Realize a leitura do QR Code no aplicativo do WhatsApp (ou acompanhe o status na interface web do ZapMonitor).

### 4.3 Procedimento Caso o Número Seja Banido pelo WhatsApp
Se a conta for suspensa permanentemente pelo WhatsApp:

> [!IMPORTANT]
> **Todo o histórico de mensagens, participantes e palavras-chave armazenados no PostgreSQL permanece 100% seguro e preservado.** Apenas a sessão de gateway é substituída.

1. **Remover ou pausar o bot antigo**:
   - Pelo painel web (`/settings/`), pause ou remova a conexão com o bot comprometido.
2. **Cadastrar o novo bot na interface web**:
   - Conecte o novo número/instância na Evolution API via QR Code.
   - Adicione a nova conexão em **Configurações > Nova Conexão / Bot** no painel web.
   - Adicione o novo número como participante/administrador dos grupos no WhatsApp.
3. **Disparar ressincronização de participantes**:
   ```bash
   docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "
   from apps.groups.tasks import sync_all_monitored_groups
   sync_all_monitored_groups.delay()
   "
   ```
4. O sistema continuará a ingestão de novas mensagens de onde parou, mapeando os participantes históricos normalmente.

