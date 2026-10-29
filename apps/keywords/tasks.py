import logging
import re
import time

from django.db import transaction
from procrastinate.contrib.django import app

from apps.messages_monitor.models import Message

from .models import Keyword, KeywordHit, normalize_keyword_text

logger = logging.getLogger("apps.keywords")


def contains_keyword(text_normalized: str, term_normalized: str) -> bool:
    """Verifica correspondência por palavra inteira no texto já normalizado."""
    if not text_normalized or not term_normalized:
        return False
    pattern = r"(?<!\w)" + re.escape(term_normalized) + r"(?!\w)"
    return bool(re.search(pattern, text_normalized))


@app.task(queue="default")
def backfill_keyword_hits(keyword_id: int, batch_size: int = 500):
    """
    Realiza o backfill histórico de uma nova palavra-chave cadastrada.
    Processa em blocos com pequenas pausas para não saturar I/O e não competir com a ingestão.
    Usa a mesma função canônica de normalização (casefold + unaccent) da ingestão.
    """
    try:
        keyword = Keyword.objects.select_related("group").get(pk=keyword_id)
    except Keyword.DoesNotExist:
        logger.warning(f"Keyword #{keyword_id} não encontrada para backfill.")
        return "Keyword não encontrada."

    term_norm = keyword.term_normalized or normalize_keyword_text(keyword.term)
    group = keyword.group

    messages_qs = (
        Message.objects.filter(group=group)
        .exclude(text="")
        .only("id", "group_id", "member_id", "text", "sent_at")
        .order_by("id")
    )

    total_hits_created = 0
    total_processed = 0
    last_id = 0

    while True:
        batch = list(messages_qs.filter(id__gt=last_id)[:batch_size])
        if not batch:
            break

        hits_to_create = []
        for msg in batch:
            last_id = msg.id
            total_processed += 1
            text_norm = normalize_keyword_text(msg.text)
            if contains_keyword(text_norm, term_norm):
                hits_to_create.append(
                    KeywordHit(
                        keyword=keyword,
                        message=msg,
                        group=group,
                        member=msg.member,
                        sent_at=msg.sent_at,
                    )
                )

        if hits_to_create:
            with transaction.atomic():
                # ignore_conflicts evita duplicar hits caso o job seja reexecutado
                KeywordHit.objects.bulk_create(hits_to_create, ignore_conflicts=True)
                total_hits_created += len(hits_to_create)

        # Pausa leve para aliviar o banco e permitir que a ingestão processe com prioridade
        time.sleep(0.05)

    logger.info(
        f"Backfill concluído para Keyword '{keyword.term}' (ID {keyword.id}): "
        f"{total_hits_created} hits em {total_processed} mensagens."
    )
    return f"Backfill finalizado: {total_hits_created} hits criados."
