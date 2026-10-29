import re

from django.db import models
from django.utils import timezone


class EvolutionInstance(models.Model):
    """
    Configuração e credenciais de uma conexão/bot da Evolution API v2 gerenciada pela interface web.
    Permite múltiplos bots e servidores (locais ou remotos) sem depender exclusivamente do .env.
    """

    name = models.CharField("Nome do Bot / Conexão", max_length=100)
    api_url = models.CharField(
        "URL da Evolution API",
        max_length=255,
        help_text="Ex: http://localhost:8080, https://evolution.empresa.com ou IP",
    )
    api_key = models.CharField("Chave de API (Global ou da Instância)", max_length=255, blank=True)
    instance_name = models.CharField(
        "Nome da Instância",
        max_length=100,
        help_text="Nome exato da instância cadastrada na Evolution API",
    )
    webhook_secret = models.CharField(
        "Segredo do Webhook (X-Webhook-Secret)",
        max_length=128,
        blank=True,
        help_text="Segredo compartilhado enviado no cabeçalho X-Webhook-Secret",
    )
    webhook_secret_old = models.CharField(
        "Segredo Antigo (Rotação)",
        max_length=128,
        blank=True,
        help_text="Segredo anterior para rotação sem downtime",
    )
    active = models.BooleanField("Ativo", default=True, db_index=True)
    last_state = models.CharField("Último Estado", max_length=50, default="unknown")
    last_checked_at = models.DateTimeField("Última Verificação", null=True, blank=True)
    created_at = models.DateTimeField("Data de Cadastro", default=timezone.now)
    updated_at = models.DateTimeField("Última Atualização", auto_now=True)

    class Meta:
        verbose_name = "Instância Evolution"
        verbose_name_plural = "Instâncias Evolution"
        ordering = ["-active", "name"]

    def __str__(self):
        return f"{self.name} ({self.instance_name})"

    @property
    def is_connected(self) -> bool:
        return self.last_state.lower() in ("open", "connected")


class Group(models.Model):
    """
    Grupo de WhatsApp monitorado pelo sistema.
    """

    jid = models.CharField("JID do Grupo (@g.us)", max_length=120, unique=True, db_index=True)
    name = models.CharField("Nome do Grupo", max_length=255, blank=True)
    instance = models.ForeignKey(
        EvolutionInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="groups",
        verbose_name="Instância / Bot",
    )
    monitored = models.BooleanField("Monitorado", default=False, db_index=True)
    created_at = models.DateTimeField("Data de Cadastro", default=timezone.now)
    last_synced_at = models.DateTimeField("Última Sincronização", null=True, blank=True)

    class Meta:
        verbose_name = "Grupo"
        verbose_name_plural = "Grupos"
        ordering = ["name"]

    def __str__(self):
        return self.name or self.jid


class Member(models.Model):
    """
    Participante de grupos de WhatsApp.
    Suporta resolução e unificação entre ID @lid e número de telefone real (@s.whatsapp.net).
    """

    phone = models.CharField(
        "Telefone (DDI+DDD+Número)", max_length=32, null=True, blank=True, db_index=True
    )
    lid = models.CharField(
        "ID @lid da Evolution", max_length=64, null=True, blank=True, db_index=True
    )
    push_name = models.CharField("Nome no WhatsApp (Push Name)", max_length=255, blank=True)
    first_seen_at = models.DateTimeField("Primeira Visualização", default=timezone.now)
    anonymized_at = models.DateTimeField("Data de Anonimização LGPD", null=True, blank=True)

    class Meta:
        verbose_name = "Participante"
        verbose_name_plural = "Participantes"
        constraints = [
            models.UniqueConstraint(
                fields=["phone"],
                condition=models.Q(phone__isnull=False) & ~models.Q(phone=""),
                name="unique_member_phone_not_null",
            ),
            models.UniqueConstraint(
                fields=["lid"],
                condition=models.Q(lid__isnull=False) & ~models.Q(lid=""),
                name="unique_member_lid_not_null",
            ),
        ]

    def __str__(self):
        if self.push_name:
            return f"{self.push_name} ({self.masked_phone})"
        return self.masked_phone

    @property
    def display_name(self) -> str:
        if self.anonymized_at:
            return f"Participante Anonimizado #{self.pk}"
        return self.push_name or self.masked_phone

    @property
    def clean_phone(self) -> str | None:
        """
        Retorna apenas os dígitos do número de telefone (sem sufixos como @s.whatsapp.net).
        """
        if not self.phone:
            return None
        ident = self.phone.split("@")[0].split(":")[0]
        digits = re.sub(r"\D", "", ident)
        return digits if len(digits) >= 8 else None

    @property
    def formatted_phone(self) -> str:
        """
        Retorna o telefone completo formatado no padrão internacional / nacional:
        Ex: +55 (63) 99223-1596
        """
        if self.anonymized_at:
            return "[ANONIMIZADO]"
        if not self.phone:
            if self.lid:
                return f"LID:{self.lid}"
            return "Sem telefone"

        digits = self.clean_phone or ""
        if len(digits) >= 12:
            country = digits[:2]
            area = digits[2:4]
            rest = digits[4:]
            if len(rest) == 9:
                return f"+{country} ({area}) {rest[:5]}-{rest[5:]}"
            elif len(rest) == 8:
                return f"+{country} ({area}) {rest[:4]}-{rest[4:]}"
            return f"+{country} ({area}) {rest}"
        elif len(digits) >= 10:
            area = digits[:2]
            rest = digits[2:]
            if len(rest) == 9:
                return f"({area}) {rest[:5]}-{rest[5:]}"
            elif len(rest) == 8:
                return f"({area}) {rest[:4]}-{rest[4:]}"
            return f"({area}) {rest}"
        elif digits:
            return digits
        return self.phone

    @property
    def masked_phone(self) -> str:
        """
        Retorna o telefone mascarado por padrão (+55 11 9****-1234)
        """
        if self.anonymized_at:
            return "[ANONIMIZADO]"
        if not self.phone:
            if self.lid:
                return f"LID:{self.lid[:4]}...{self.lid[-4:]}"
            return "Sem telefone"

        # Limpar apenas números
        digits = self.clean_phone or re.sub(r"\D", "", self.phone)
        if len(digits) >= 12:
            # Ex: 5511987654321 -> +55 11 9****-4321
            country = digits[:2]
            area = digits[2:4]
            rest = digits[4:]
            first_digit = rest[0]
            last_digits = rest[-4:]
            masked = f"+{country} ({area}) {first_digit}****-{last_digits}"
            return masked
        elif len(digits) >= 8:
            return f"{digits[:2]}****{digits[-3:]}"
        return "****"


class GroupMembership(models.Model):
    """
    Associação e histórico de permanência de um participante em um grupo.
    left_at IS NULL indica presença atual no grupo.
    """

    class Role(models.TextChoices):
        MEMBER = "member", "Membro"
        ADMIN = "admin", "Administrador"
        SUPERADMIN = "superadmin", "Criador / Superadmin"

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="memberships", verbose_name="Grupo"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="memberships", verbose_name="Participante"
    )
    role = models.CharField(
        "Papel no Grupo", max_length=20, choices=Role.choices, default=Role.MEMBER
    )
    joined_at = models.DateTimeField("Data de Entrada", default=timezone.now)
    left_at = models.DateTimeField("Data de Saída", null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Associação a Grupo"
        verbose_name_plural = "Associações a Grupos"
        indexes = [
            models.Index(fields=["group", "left_at"], name="idx_membership_group_left"),
            models.Index(fields=["member", "left_at"], name="idx_membership_member_left"),
        ]

    def __str__(self):
        status = "Presente" if self.is_active else f"Saiu em {self.left_at:%d/%m/%Y}"
        return f"{self.member} em {self.group} ({self.get_role_display()} - {status})"

    @property
    def is_active(self) -> bool:
        return self.left_at is None
