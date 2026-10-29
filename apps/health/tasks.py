import logging

from django.conf import settings
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.groups.models import Group
from apps.messages_monitor.models import Message

logger = logging.getLogger("apps.health")


@app.periodic(cron="*/15 * * * *")
@app.task(queue="default", queueing_lock="check_webhook_inactivity")
def check_webhook_inactivity_task(timestamp: int = 0):
    """
    Verifica se o sistema parou de receber mensagens nos grupos monitorados
    por mais de X horas (indicativo de sessão de WhatsApp desconectada/banida).
    Executa a cada 15 minutos com lock de fila.
    """
    monitored_groups_exist = Group.objects.filter(monitored=True).exists()
    if not monitored_groups_exist:
        return "Nenhum grupo monitorado ativo para alertar inatividade."

    alert_hours = getattr(settings, "WEBHOOK_INACTIVITY_ALERT_HOURS", 6)
    cutoff = timezone.now() - timezone.timedelta(hours=alert_hours)

    last_msg = Message.objects.order_by("-sent_at").first()

    if not last_msg or last_msg.sent_at < cutoff:
        elapsed = (
            "sem mensagens registradas"
            if not last_msg
            else f"última em {last_msg.sent_at:%d/%m/%Y %H:%M}"
        )
        logger.warning(
            f"ALERTA OPERACIONAL: Nenhuma mensagem recebida há mais de {alert_hours} horas ({elapsed}). "
            "A sessão do WhatsApp na Evolution API pode estar desconectada."
        )
        return f"Alerta emitido: Inatividade superior a {alert_hours}h."

    return "Atividade de mensagens normal."
