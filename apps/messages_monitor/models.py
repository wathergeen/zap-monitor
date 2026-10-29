from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.utils import timezone

from apps.groups.models import Group, Member


class WebhookEvent(models.Model):
    """
    Armazena eventos brutos recebidos via Webhook da Evolution API
    para auditoria, diagnóstico e reprocessamento idempotente.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        PROCESSED = "processed", "Processado"
        IGNORED = "ignored", "Ignorado"
        ERROR = "error", "Erro"

    event_type = models.CharField("Tipo do Evento", max_length=100, blank=True)
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    payload = models.JSONField("Payload Bruto JSONB")
    error = models.TextField("Erro / Stack Trace", blank=True)
    received_at = models.DateTimeField("Recebido Em", default=timezone.now, db_index=True)
    processed_at = models.DateTimeField("Processado Em", null=True, blank=True)

    class Meta:
        verbose_name = "Evento de Webhook"
        verbose_name_plural = "Eventos de Webhooks"
        ordering = ["-received_at"]

    def __str__(self):
        return f"WebhookEvent #{self.pk} - {self.event_type} ({self.status})"


class MessageQuerySet(models.QuerySet):
    """QuerySet customizado para consultas expressivas sobre mensagens."""

    def deleted(self):
        """Retorna apenas mensagens que foram apagadas/removidas."""
        return self.filter(deleted_at__isnull=False)

    def active(self):
        """Retorna apenas mensagens ativas (não apagadas)."""
        return self.filter(deleted_at__isnull=True)


class Message(models.Model):
    """
    Mensagem capturada de grupo monitorado.
    Totalmente idempotente por wa_id.
    """

    class MessageType(models.TextChoices):
        TEXT = "text", "Texto"
        IMAGE = "image", "Imagem"
        VIDEO = "video", "Vídeo"
        AUDIO = "audio", "Áudio"
        DOCUMENT = "document", "Documento"
        STICKER = "sticker", "Figurinha"
        REACTION = "reaction", "Reação"
        OTHER = "other", "Outro"

    wa_id = models.CharField("ID WhatsApp da Mensagem", max_length=128, unique=True, db_index=True)
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="messages", verbose_name="Grupo"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="messages", verbose_name="Participante"
    )
    type = models.CharField(
        "Tipo de Mensagem", max_length=20, choices=MessageType.choices, default=MessageType.TEXT
    )
    text = models.TextField("Texto da Mensagem / Legenda", blank=True)
    sent_at = models.DateTimeField("Data / Hora de Envio", db_index=True)
    edited_at = models.DateTimeField("Data / Hora de Edição", null=True, blank=True)
    deleted_at = models.DateTimeField("Data / Hora de Exclusão", null=True, blank=True)

    # Campo de busca full-text com configuração portuguesa
    search_vector = SearchVectorField("Vetor de Busca FTS", null=True, blank=True)

    objects = MessageQuerySet.as_manager()

    class Meta:
        verbose_name = "Mensagem"
        verbose_name_plural = "Mensagens"
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["group", "sent_at"], name="idx_message_group_sent"),
            models.Index(fields=["member", "sent_at"], name="idx_message_member_sent"),
            models.Index(fields=["search_vector"], name="idx_message_search_vector"),
        ]

    def __str__(self):
        preview = (self.text[:40] + "...") if len(self.text) > 40 else self.text
        return f"[{self.sent_at:%d/%m/%Y %H:%M}] {self.member.display_name}: {preview}"

    @property
    def is_deleted(self) -> bool:
        """Indica se a mensagem foi apagada/removida no WhatsApp."""
        return self.deleted_at is not None

    def update_search_vector(self):
        """Atualiza o vetor FTS no Postgres."""
        from django.db import connection

        if connection.vendor == "postgresql" and self.text:
            Message.objects.filter(pk=self.pk).update(
                search_vector=SearchVector("text", config="portuguese")
            )
