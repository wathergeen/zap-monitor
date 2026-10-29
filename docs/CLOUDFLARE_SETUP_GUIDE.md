# Guia de Configuração e Integração com Cloudflare (Túnel & Proxy)

Este documento descreve como configurar a infraestrutura de borda utilizando o **Cloudflare Tunnel (cloudflared)** ou o Proxy Reverso Cloudflare para expor com segurança o **ZapMonitor** em ambientes de staging e produção.

---

## 1. Arquitetura de Exposição com Cloudflare

O **ZapMonitor** adota a política de **zero portas expostas diretamente para a internet** quando operado via Cloudflare Tunnel.

```
Internet/WhatsApp Network
        │
        ▼ (HTTPS)
   [Cloudflare Edge] (DDoS, WAF, Bot Management)
        │
        ▼ (Túnel Criptografado mTLS)
   [cloudflared Container] (Rede Docker `frontend`)
        │
        ▼ (HTTP)
   [Caddy Proxy / Gunicorn Web]
```

### Vantagens:
- **Nenhuma porta 80/443 precisa ser aberta no firewall ou roteador do servidor.**
- **A Evolution API e o PostgreSQL permanecem em rede interna isolada (`backend` com `internal: true`).**
- **Proteção contra ataques DDoS e inspeção de WAF no nível de borda da Cloudflare.**

---

## 2. Configurando o Cloudflare Tunnel (`cloudflared`)

### 2.1 Criando o Túnel no Painel Cloudflare Zero Trust
1. Acesse o painel **Cloudflare Zero Trust** &rarr; **Networks** &rarr; **Tunnels**.
2. Clique em **Create a Tunnel** e selecione o tipo **Cloudflared**.
3. Dê um nome ao túnel (ex: `zapmonitor-prod`).
4. Copie o token de autenticação gerado (ex: `eyJh...`).

### 2.2 Integrando o Túnel ao `docker-compose.prod.yml`
Adicione o serviço `cloudflared` na rede `frontend`:

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    networks:
      - frontend
    depends_on:
      proxy:
        condition: service_healthy
```

Defina no arquivo `.env`:
```bash
CLOUDFLARE_TUNNEL_TOKEN=eyJh...seu_token_aqui
```

### 2.3 Roteamento de Hostname Público no Cloudflare
No painel do túnel na Cloudflare, adicione uma rota pública para a sua aplicação:
- **Subdomain**: `zapbot`
- **Domain**: `suaempresa.com.br`
- **Service Type**: `HTTP`
- **URL**: `proxy:80` (ou `web:8000` caso aponte direto)

---

## 3. Configurações de Segurança Obrigatórias no Painel Cloudflare

### 3.1 SSL/TLS
- Configure o modo de criptografia para **Full (Strict)**.
- Ative **Always Use HTTPS**.
- Ative **Minimum TLS Version**: `1.2` (ou `1.3`).
- Ative **HTTP Strict Transport Security (HSTS)** com max-age &ge; 31536000 e includeSubDomains.

### 3.2 Cabeçalhos e IP Real
O Caddy e o Django estão configurados para extrair o IP real do cliente através do cabeçalho `CF-Connecting-IP`.
No Django (`core/settings/base.py`):
```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```
No `Caddyfile.prod`:
```caddy
reverse_proxy web:8000 {
    header_up Host {host}
    header_up X-Real-IP {http.request.header.CF-Connecting-IP}
    header_up X-Forwarded-For {http.request.header.CF-Connecting-IP}
    header_up X-Forwarded-Proto https
}
```

### 3.3 Regras de WAF e Restrição para Webhooks
1. **Endpoint do Webhook (`/webhooks/evolution/`)**:
   - Crie uma regra customizada no WAF para permitir apenas requisições contendo o cabeçalho `X-Webhook-Secret` válido.
2. **Endpoint Administrativo (`/internal-admin/`)**:
   - Crie uma regra de **Cloudflare Access** (Zero Trust) ou WAF IP Access Rule para restringir `/internal-admin/*` exclusivamente para os IPs da VPN corporativa ou autenticação via SSO (Google Workspace / Okta).

---

## 4. Variáveis de Ambiente no Django com Cloudflare

No arquivo `.env` de produção, certifique-se de configurar:

```bash
ALLOWED_HOSTS=zapbot.suaempresa.com.br
CSRF_TRUSTED_ORIGINS=https://zapbot.suaempresa.com.br
```
Isso garante que formulários com proteção CSRF e requisições via HTMX sejam validados sem erros de origem suspeita.

