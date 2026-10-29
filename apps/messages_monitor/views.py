import hmac
import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.groups.models import Group

from .models import WebhookEvent
from .tasks import process_webhook_event_task

logger = logging.getLogger("apps.messages_monitor")


def verify_webhook_secret(request: HttpRequest) -> bool:
    """
    Validação em tempo constante do segredo compartilhado enviado no cabeçalho X-Webhook-Secret
    ou como query parameter (?secret=... ou ?token=...) para compatibilidade com interfaces web
    da Evolution API que não possuem campo de cabeçalhos customizados.
    Suporta segredos cadastrados no banco de dados por bot/instância e variáveis de ambiente (fallback).
    """
    provided_secret = (
        request.headers.get("X-Webhook-Secret")
        or request.META.get("HTTP_X_WEBHOOK_SECRET")
        or request.GET.get("secret")
        or request.GET.get("token")
        or ""
    )
    if not provided_secret:
        return False

    # 1. Validação contra instâncias cadastradas no banco de dados
    try:
        from apps.groups.models import EvolutionInstance

        for inst in EvolutionInstance.objects.filter(active=True):
            if inst.webhook_secret and hmac.compare_digest(provided_secret, inst.webhook_secret):
                return True
            if inst.webhook_secret_old and hmac.compare_digest(
                provided_secret, inst.webhook_secret_old
            ):
                return True
    except Exception as e:
        logger.warning(f"Erro ao consultar instâncias Evolution para validação de webhook: {e}")

    # 2. Fallback para segredos definidos em variáveis de ambiente
    current_secret = getattr(settings, "EVOLUTION_WEBHOOK_SECRET", "")
    old_secret = getattr(settings, "EVOLUTION_WEBHOOK_SECRET_OLD", "")

    valid_current = bool(current_secret and hmac.compare_digest(provided_secret, current_secret))
    valid_old = bool(old_secret and hmac.compare_digest(provided_secret, old_secret))

    return valid_current or valid_old


def _extract_group_jid_and_from_me(payload: dict) -> tuple[str, bool]:
    """Extrai rapidamente o JID do grupo e a flag fromMe do payload para descarte antecipado."""
    data = payload.get("data")
    if not data:
        return "", False

    items = data if isinstance(data, list) else [data]
    if not items or not isinstance(items[0], dict):
        return "", False

    first_item = items[0]
    key = first_item.get("key") or {}

    remote_jid = (
        key.get("remoteJid")
        or first_item.get("remoteJid")
        or first_item.get("id")
        or first_item.get("groupJid")
        or ""
    )
    from_me = bool(key.get("fromMe", False))
    return str(remote_jid), from_me


@csrf_exempt
@require_POST
def evolution_webhook_view(request: HttpRequest) -> HttpResponse:
    """
    Endpoint síncrono e mínimo de recepção do webhook da Evolution API v2: POST /webhooks/evolution/
    1. Valida o segredo compartilhado em tempo constante.
    2. Descarta rapidamente grupos não monitorados e mensagens próprias do bot (fromMe).
    3. Em uma ÚNICA transação atômica, grava o WebhookEvent e enfileira no Procrastinate.
    4. Responde 200 em poucos milissegundos.
    """
    # 1. Validação do segredo compartilhado (tempo constante)
    if not verify_webhook_secret(request):
        logger.warning(
            "Tentativa de entrega de webhook rejeitada: segredo ausente ou inválido",
            extra={"ip": request.META.get("REMOTE_ADDR")},
        )
        return HttpResponseForbidden("Segredo de autenticação do webhook inválido ou ausente.")

    # 2. Validação do corpo da requisição JSON
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Payload do webhook com formato JSON inválido: {e}")
        return JsonResponse({"error": "Corpo da requisição não é um JSON válido"}, status=400)

    # 3. Identificação do tipo do evento
    event_type = str(payload.get("event") or payload.get("type") or "UNKNOWN").upper()

    # 4. Descarte rápido de mensagens do próprio bot (fromMe)
    remote_jid, from_me = _extract_group_jid_and_from_me(payload)
    if from_me:
        return JsonResponse({"status": "ignored", "reason": "from_me"}, status=200)

    # 5. Descarte rápido de mensagens que não sejam de grupos (@g.us) ou de grupos não monitorados
    if remote_jid:
        if not remote_jid.endswith("@g.us"):
            return JsonResponse({"status": "ignored", "reason": "not_group_message"}, status=200)

        # Verifica se o grupo está cadastrado e monitorado
        if not Group.objects.filter(jid=remote_jid, monitored=True).exists():
            return JsonResponse({"status": "ignored", "reason": "unmonitored_group"}, status=200)

    # 6. Gravação transacional do WebhookEvent + enfileiramento transacional no Procrastinate
    with transaction.atomic():
        event = WebhookEvent.objects.create(
            event_type=event_type,
            status=WebhookEvent.Status.PENDING,
            payload=payload,
        )
        process_webhook_event_task.defer(event_id=event.id)

    return JsonResponse({"status": "queued", "id": event.id}, status=200)
