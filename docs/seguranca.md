# Política de Segurança e Threat Model do ZapMonitor

Este documento detalha as camadas de segurança, modelo de ameaças e decisões arquiteturais de proteção implementadas no **ZapMonitor**.

---

## 1. Princípios de Arquitetura de Segurança

1. **Somente Leitura Garantido por Design**:
   - A aplicação é estritamente passiva: **nenhuma função técnica de envio de mensagens (`sendText`, `sendMedia`, `sendVoice`, etc.) existe no código-fonte**.
   - Há um teste automatizado (`tests/test_no_send_calls.py`) que inspeciona a AST de todo o projeto e falha no CI/CD se qualquer chamada a endpoints de envio for introduzida.
2. **Defesa em Profundidade**:
   - Isolamento de rede com Docker (`internal: true` para backend).
   - Somente o proxy reverso (Caddy) expõe portas públicas.
   - Headers restritivos de navegação e CSP estrita baseada em nonces criptográficos.
3. **Princípio do Menor Privilégio e Negação por Padrão (Deny by Default)**:
   - Toda view exige autenticação explícita.
   - Três papéis estritos: `admin`, `analista` e `leitor`.
   - Proteção de objeto anti-IDOR: o usuário só enxerga grupos que foram explicitamente atribuídos a ele.
4. **Logs Imutáveis e Auditoria Completa**:
   - A tabela `AuditLog` é estritamente append-only (métodos `save()` e `delete()` lançam `PermissionDenied` caso tentem alterar ou remover registros).

---

## 2. Autenticação e Gestão de Acessos

### 2.1 Hashing de Senhas com Argon2
- O sistema utiliza **Argon2** (`argon2-cffi`) como hasher prioritário, oferecendo alta resistência contra ataques baseados em GPUs e ASICs.
- Validadores estritos: mínimo de 12 caracteres, bloqueio de senhas comuns e proibição de senhas semelhantes aos dados do usuário.

### 2.2 Proteção contra Força Bruta (`django-axes`)
- Rastreamento de tentativas falhas por par `[username, ip_address]`.
- Bloqueio temporário configurável (padrão: 5 tentativas falhas &rarr; bloqueio por 1 hora).
- Resposta de login genérica ("Credenciais inválidas. Verifique seu e-mail e senha") sem revelar existência prévia de contas.
- Fluxo de recuperação de senha idêntico para e-mails cadastrados e não cadastrados.

### 2.3 Autenticação em Duas Etapas (2FA TOTP)
- Baseado em RFC 6238 com `django-otp`.
- **Obrigatório em produção para usuários com papel de Administrador**.
- Gera 8 códigos de backup de emergência, armazenados com hash SHA-256 no banco de dados para evitar vazamento em dumps.

### 2.4 Gestão de Sessões
- Cookies com flags `HttpOnly`, `SameSite=Lax` e `Secure=True` em produção.
- Expiração por inatividade (30 minutos) e tempo máximo absoluto de vida (12 horas).
- Rotação do ID de sessão (`request.session.cycle_key()`) no login para evitar ataques de fixação de sessão.
- Logout invalida o estado da sessão diretamente no servidor.

---

## 3. Segurança no Front-end e Sanitização de Entradas

### 3.1 Content Security Policy (CSP)
- CSP estrita implementada com `django-csp`.
- Uso de `nonce` dinâmico para scripts e estilos necessários.
- Proibição de scripts de terceiros e CDNs externos: todos os assets (Tailwind, HTMX, Alpine, Chart.js) são empacotados e servidos localmente.
- Diretivas adicionais:
  - `frame-ancestors: 'none'` (anti-Clickjacking).
  - `Cross-Origin-Opener-Policy: same-origin`.
  - `Permissions-Policy: accelerometer=(), camera=(), geolocation=(), microphone=()`.

### 3.2 Proteção contra XSS Armazenado
- Conteúdo de mensagens do WhatsApp é tratado como **entrada não confiável**.
- **Nunca é utilizado o filtro `|safe` sobre textos de mensagens**.
- O destaque de palavras-chave (`safe_highlight_terms`) realiza `html.escape` em 100% da string antes de aplicar as tags de marcação estilizadas.

### 3.3 Imunização contra CSV Injection (Formula Injection)
- Exportações de planilhas CSV utilizam `sanitize_csv_cell`:
  - Células que se iniciam com `=`, `+`, `-`, `@`, `\t` ou `\r` recebem um prefixo apóstrofo (`'`).
  - Previne execução acidental de comandos ou fórmulas DDE quando o arquivo é aberto no Microsoft Excel ou LibreOffice Calc.
- Toda exportação gera registro de auditoria com IP e usuário.

---

## 4. Segurança do Webhook e da Evolution API

1. **Segredo Compartilhado com Rotação sem Downtime (Multi-Bot)**:
   - Cada entrega de webhook exige o cabeçalho HTTP `X-Webhook-Secret`.
   - Comparado em tempo constante com `hmac.compare_digest` para mitigar ataques de temporização (timing attacks).
   - Gerenciado dinamicamente por bot no banco de dados (`EvolutionInstance`). Ao atualizar o segredo na interface web, o segredo anterior é automaticamente preservado em `webhook_secret_old` para garantir rotação sem downtime e sem reinicialização de containers.
2. **Isolamento de Rede Docker**:
   - A porta `8080` da Evolution API **não é publicada para a internet**.
   - Apenas o worker Procrastinate e o Django conseguem acessá-la via rede `backend` privada (`internal: true`).
3. **Minimização de Mídias**:
   - O sistema **não baixa nem armazena** arquivos binários de mídia (áudios, vídeos, fotos). Registra exclusivamente metadados (tipo e legendas).

