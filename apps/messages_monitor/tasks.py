import datetime
import logging
from typing import Any, Dict, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.analytics.models import MemberDailyStats, MemberMonthlyStats
from apps.groups.models import Group, GroupMembership
from apps.groups.services import MemberResolutionService
from apps.keywords.models import Keyword, KeywordHit, normalize_keyword_text
from apps.keywords.tasks import contains_keyword

from .models import Message, WebhookEvent

logger = logging.getLogger("apps.messages_monitor")


def parse_epoch_timestamp(epoch_val: Any) -> datetime.datetime:
    """Converte timestamps epoch em segundos ou milissegundos para datetime com timezone."""
    if not epoch_val:
        return timezone.now()
    try:
        ts = float(epoch_val)
        if ts > 1000000000000:  # Milissegundos
            ts = ts / 1000.0
        return datetime.datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return timezone.now()


def extract_message_text_and_type(raw_msg_data: Dict[str, Any]) -> Tuple[str, str]:
    """
    Extrai com segurança o conteúdo textual e o tipo de mídia da estrutura da mensagem da Evolution API.
    Não armazena binários de mídia, apenas o texto ou legenda.
    """
    msg_container = raw_msg_data.get("message") or {}

    # 1. Mensagem de texto simples
    if "conversation" in msg_container and msg_container["conversation"]:
        return str(msg_container["conversation"]), Message.MessageType.TEXT

    # 2. Mensagem de texto estendida
    if "extendedTextMessage" in msg_container:
        ext = msg_container["extendedTextMessage"]
        return str(ext.get("text") or ""), Message.MessageType.TEXT

    # 3. Imagem
    if "imageMessage" in msg_container:
        caption = msg_container["imageMessage"].get("caption", "")
        return str(caption or ""), Message.MessageType.IMAGE

    # 4. Vídeo
    if "videoMessage" in msg_container:
        caption = msg_container["videoMessage"].get("caption", "")
        return str(caption or ""), Message.MessageType.VIDEO

    # 5. Áudio
    if "audioMessage" in msg_container:
        return "", Message.MessageType.AUDIO

    # 6. Documento
    if "documentMessage" in msg_container:
        caption = msg_container["documentMessage"].get("caption") or msg_container[
            "documentMessage"
        ].get("fileName", "")
        return str(caption or ""), Message.MessageType.DOCUMENT

    # 7. Figurinha / Sticker
    if "stickerMessage" in msg_container:
        return "", Message.MessageType.STICKER

    # 8. Reação
    if "reactionMessage" in msg_container:
        reaction_text = msg_container["reactionMessage"].get("text", "")
        return str(reaction_text or ""), Message.MessageType.REACTION

    return "", Message.MessageType.OTHER


@app.task(queue="default")
def process_webhook_event_task(event_id: int):
    """
    Processa assincronamente o evento bruto recebido via webhook.
    Garante idempotência, filtragem de grupos monitorados e registro de edições/exclusões.
    """
    try:
        event = WebhookEvent.objects.get(pk=event_id)
    except WebhookEvent.DoesNotExist:
        logger.error(f"WebhookEvent #{event_id} não encontrado.")
        return

    payload = event.payload or {}
    event_type = (event.event_type or "").upper()
    data = payload.get("data") or {}

    try:
        # 1. Tratar eventos de mensagens
        if "MESSAGES_UPSERT" in event_type or "MESSAGES.UPSERT" in event_type:
            _handle_message_upsert(event, data)
        elif "MESSAGES_UPDATE" in event_type or "MESSAGES.UPDATE" in event_type:
            _handle_message_update(event, data)
        elif "MESSAGES_DELETE" in event_type or "MESSAGES.DELETE" in event_type:
            _handle_message_delete(event, data)
        elif "GROUP_PARTICIPANTS_UPDATE" in event_type or "GROUP-PARTICIPANTS.UPDATE" in event_type:
            _handle_group_participants_update(event, data)
        elif "GROUP_UPDATE" in event_type or "GROUP.UPDATE" in event_type:
            _handle_group_update(event, data)
        else:
            event.status = WebhookEvent.Status.IGNORED
            event.error = f"Tipo de evento não monitorado: {event_type}"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "error", "processed_at"])

    except Exception as exc:
        logger.exception(f"Erro ao processar WebhookEvent #{event_id}: {exc}")
        event.status = WebhookEvent.Status.ERROR
        event.error = str(exc)
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "error", "processed_at"])


def ingest_single_message_data(item: Dict[str, Any], default_group: Group = None) -> bool:
    """
    Ingere de forma idempotente e segura um registro de mensagem vindo da Evolution API.
    Retorna True se uma nova mensagem foi gravada.
    """
    key = item.get("key") or {}
    wa_id = key.get("id")
    if not wa_id:
        return False

    # Atalho: se a mensagem já foi gravada, não processa nada para evitar qualquer sobrecarga ou resolução redundante
    if Message.objects.filter(wa_id=wa_id).exists():
        return False

    remote_jid = key.get("remoteJid") or item.get("remoteJid") or ""
    if not remote_jid.endswith("@g.us"):
        return False

    group = default_group or Group.objects.filter(jid=remote_jid, monitored=True).first()
    if not group or not group.monitored:
        return False

    if key.get("fromMe") is True:
        return False

    participant_jid = (
        key.get("participant") or item.get("participant") or key.get("participantAlt") or ""
    )
    push_name = item.get("pushName") or ""

    lid = None
    if not participant_jid and push_name and push_name.strip().isdigit():
        lid = push_name.strip()
        participant_jid = f"{lid}@lid"

    if not participant_jid and not lid:
        logger.warning(f"Mensagem {wa_id} ignorada: sem remetente identificado.")
        return False

    member, _ = MemberResolutionService.get_or_create_member(
        identifier=participant_jid,
        push_name=push_name if push_name != lid else "",
        lid=lid,
    )
    if not member:
        return False

    MemberResolutionService.record_membership(group=group, member=member, is_leaving=False)

    text, msg_type = extract_message_text_and_type(item)
    sent_at = parse_epoch_timestamp(item.get("messageTimestamp"))

    with transaction.atomic():
        msg, created = Message.objects.get_or_create(
            wa_id=wa_id,
            defaults={
                "group": group,
                "member": member,
                "type": msg_type,
                "text": text,
                "sent_at": sent_at,
            },
        )
        if created:
            msg.update_search_vector()

            # 1. Upsert em MemberMonthlyStats
            month_date = sent_at.date().replace(day=1)
            monthly_stat, m_created = MemberMonthlyStats.objects.get_or_create(
                group=group,
                member=member,
                month=month_date,
                defaults={
                    "message_count": 1,
                    "first_message_at": sent_at,
                    "last_message_at": sent_at,
                },
            )
            if not m_created:
                MemberMonthlyStats.objects.filter(pk=monthly_stat.pk).update(
                    message_count=F("message_count") + 1,
                    last_message_at=sent_at,
                )

            # 2. Upsert em MemberDailyStats
            day_date = sent_at.date()
            daily_stat, d_created = MemberDailyStats.objects.get_or_create(
                group=group,
                member=member,
                day=day_date,
                defaults={"message_count": 1},
            )
            if not d_created:
                MemberDailyStats.objects.filter(pk=daily_stat.pk).update(
                    message_count=F("message_count") + 1
                )

            # 3. Detecção de palavras-chave na ingestão
            if text:
                text_norm = normalize_keyword_text(text)
                active_keywords = Keyword.objects.filter(group=group, active=True)
                hits_to_create = []
                for kw in active_keywords:
                    kw_norm = kw.term_normalized or normalize_keyword_text(kw.term)
                    if contains_keyword(text_norm, kw_norm):
                        hits_to_create.append(
                            KeywordHit(
                                keyword=kw,
                                message=msg,
                                group=group,
                                member=member,
                                sent_at=sent_at,
                            )
                        )
                if hits_to_create:
                    KeywordHit.objects.bulk_create(hits_to_create, ignore_conflicts=True)

            return True
    return False


def sync_group_recent_messages(group: Group, limit: int = 50) -> int:
    """
    Sincroniza sob demanda as mensagens mais recentes da Evolution API para garantir
    tolerância total a falhas ou atrasos de entrega de webhook.
    """
    if not group or not group.monitored:
        return 0

    from apps.groups.evolution_client import EvolutionReadOnlyClient

    client = EvolutionReadOnlyClient(instance=group.instance)
    records = client.find_messages(group.jid, limit=limit)
    new_count = 0
    for r in records:
        if ingest_single_message_data(r, default_group=group):
            new_count += 1
    return new_count


def _handle_message_upsert(event: WebhookEvent, data: Dict[str, Any]):
    messages_list = data if isinstance(data, list) else [data]
    processed_count = 0

    for item in messages_list:
        key = item.get("key") or {}
        remote_jid = key.get("remoteJid") or item.get("remoteJid") or ""

        # Filtrar apenas grupos (@g.us)
        if not remote_jid.endswith("@g.us"):
            event.status = WebhookEvent.Status.IGNORED
            event.error = f"Ignorado: Mensagem não é de grupo ({remote_jid})"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "error", "processed_at"])
            continue

        # Verificar se o grupo está cadastrado como monitorado
        group = Group.objects.filter(jid=remote_jid, monitored=True).first()
        if not group:
            event.status = WebhookEvent.Status.IGNORED
            event.error = f"Ignorado: Grupo {remote_jid} não está marcado como monitorado."
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "error", "processed_at"])
            continue

        # Associar à instância Evolution caso ainda não esteja associado
        instance_name = event.payload.get("instance")
        if instance_name and not group.instance:
            from apps.groups.models import EvolutionInstance

            evo_inst = EvolutionInstance.objects.filter(
                instance_name=instance_name, active=True
            ).first()
            if evo_inst:
                group.instance = evo_inst
                group.save(update_fields=["instance"])

        # Ignorar mensagens enviadas pelo próprio número do bot (fromMe)
        if key.get("fromMe") is True:
            event.status = WebhookEvent.Status.IGNORED
            event.error = "Ignorado: Mensagem enviada pelo próprio bot (fromMe=True)"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "error", "processed_at"])
            continue

        ingest_single_message_data(item, default_group=group)
        processed_count += 1

    if processed_count > 0:
        event.status = WebhookEvent.Status.PROCESSED
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])


def _handle_message_update(event: WebhookEvent, data: Dict[str, Any]):
    """
    Trata mensagens editadas:
    Mantém o registro original, adiciona timestamp edited_at.
    Nunca sobrescreve silenciosamente.
    """
    items = data if isinstance(data, list) else [data]
    for item in items:
        key = item.get("key") or {}
        wa_id = key.get("id")
        if not wa_id:
            continue

        msg = Message.objects.filter(wa_id=wa_id).first()
        if not msg:
            continue

        new_text, _ = extract_message_text_and_type(item)
        now = timezone.now()

        msg.text = new_text
        msg.edited_at = now
        msg.save(update_fields=["text", "edited_at"])
        msg.update_search_vector()

    event.status = WebhookEvent.Status.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])


def _handle_message_delete(event: WebhookEvent, data: Dict[str, Any]):
    """
    Trata mensagens apagadas:
    Registra deleted_at mantendo a mensagem original para fins de auditoria.
    """
    items = data if isinstance(data, list) else [data]
    for item in items:
        key = item.get("key") or {}
        wa_id = key.get("id")
        if not wa_id:
            continue

        msg = Message.objects.filter(wa_id=wa_id).first()
        if msg and not msg.deleted_at:
            msg.deleted_at = timezone.now()
            msg.save(update_fields=["deleted_at"])

    event.status = WebhookEvent.Status.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])


def _handle_group_participants_update(event: WebhookEvent, data: Dict[str, Any]):
    group_jid = data.get("id") or data.get("groupJid") or ""
    group = Group.objects.filter(jid=group_jid, monitored=True).first()
    if not group:
        event.status = WebhookEvent.Status.IGNORED
        event.error = f"Grupo {group_jid} não monitorado para atualização de participantes."
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "error", "processed_at"])
        return

    action = (data.get("action") or "").lower()
    participants = data.get("participants") or []

    for p_jid in participants:
        member, _ = MemberResolutionService.get_or_create_member(identifier=p_jid)
        if action == "add":
            MemberResolutionService.record_membership(group=group, member=member, is_leaving=False)
        elif action == "remove":
            MemberResolutionService.record_membership(group=group, member=member, is_leaving=True)
        elif action == "promote":
            MemberResolutionService.record_membership(
                group=group,
                member=member,
                role=GroupMembership.Role.ADMIN,
                is_leaving=False,
            )
        elif action == "demote":
            MemberResolutionService.record_membership(
                group=group,
                member=member,
                role=GroupMembership.Role.MEMBER,
                is_leaving=False,
            )

    event.status = WebhookEvent.Status.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])


def _handle_group_update(event: WebhookEvent, data: Dict[str, Any]):
    group_jid = data.get("id") or ""
    group = Group.objects.filter(jid=group_jid).first()
    if group:
        subject = data.get("subject") or data.get("name")
        if subject:
            group.name = subject
            group.save(update_fields=["name"])

    event.status = WebhookEvent.Status.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])


@app.periodic(cron="0 3 * * *")
@app.task(queue="default", queueing_lock="apply_retention_policy")
def apply_retention_policy_task(timestamp: int = 0):
    """
    Política de retenção configurável para conformidade com a LGPD e limpeza de WebhookEvent.
    1. Apaga mensagens mais antigas que N meses (configurado em settings.RETENTION_MONTHS) em lotes.
    2. Apaga WebhookEvents brutos com mais de 7 dias de retenção em lotes.
    """
    retention_months = getattr(settings, "RETENTION_MONTHS", 12)
    cutoff_date = timezone.now() - datetime.timedelta(days=retention_months * 30)

    # 1. Apagar mensagens antigas em lotes de 500
    msg_ids = list(
        Message.objects.filter(sent_at__lt=cutoff_date).values_list("id", flat=True)[:500]
    )
    total_messages_deleted = 0
    while msg_ids:
        deleted_count, _ = Message.objects.filter(id__in=msg_ids).delete()
        total_messages_deleted += deleted_count
        msg_ids = list(
            Message.objects.filter(sent_at__lt=cutoff_date).values_list("id", flat=True)[:500]
        )

    if total_messages_deleted > 0:
        logger.info(
            f"LGPD: Removidas {total_messages_deleted} mensagens anteriores a {cutoff_date}"
        )

    # 2. Apagar WebhookEvents com mais de 7 dias
    events_cutoff = timezone.now() - datetime.timedelta(days=7)
    event_ids = list(
        WebhookEvent.objects.filter(received_at__lt=events_cutoff).values_list("id", flat=True)[
            :500
        ]
    )
    total_events_deleted = 0
    while event_ids:
        deleted_count, _ = WebhookEvent.objects.filter(id__in=event_ids).delete()
        total_events_deleted += deleted_count
        event_ids = list(
            WebhookEvent.objects.filter(received_at__lt=events_cutoff).values_list("id", flat=True)[
                :500
            ]
        )

    if total_events_deleted > 0:
        logger.info(
            f"Retenção de WebhookEvent: Removidos {total_events_deleted} eventos brutos antigos."
        )

    return f"Retenção concluída: {total_messages_deleted} msgs e {total_events_deleted} eventos removidos."
