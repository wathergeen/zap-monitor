from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models


class AuditLog(models.Model):
    """
    Log de auditoria estritamente append-only (somente inserção).
    Registra ações críticas para segurança e conformidade LGPD.
    """

    class Action(models.TextChoices):
        LOGIN = "login", "Login realizado com sucesso"
        LOGOUT = "logout", "Logout realizado"
        LOGIN_FAILED = "login_failed", "Falha de autenticação"
        PASSWORD_RESET = "password_reset", "Solicitação de recuperação de senha"
        EXPORT_CSV = "export_csv", "Exportação de dados em CSV"
        PERMISSION_CHANGE = "permission_change", "Alteração de permissões ou papel"
        VIEW_MESSAGES = "view_messages", "Consulta a conteúdo detalhado de mensagens"
        REVEAL_PHONE = "reveal_phone", "Visualização de telefone desmascarado"
        KEYWORD_CHANGE = "keyword_change", "Criação, edição ou remoção de palavra-chave"
        MEMBER_ANONYMIZED = "member_anonymized", "Anonimização de dados pessoais (LGPD)"
        GROUP_MONITOR_TOGGLE = (
            "group_monitor_toggle",
            "Alteração de status de monitoramento de grupo",
        )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="Usuário",
    )
    action = models.CharField("Ação", max_length=50, choices=Action.choices, db_index=True)
    target = models.CharField("Alvo / Recurso", max_length=255, blank=True)
    ip_address = models.GenericIPAddressField("Endereço IP", null=True, blank=True)
    user_agent = models.TextField("User Agent", blank=True)
    details = models.JSONField("Detalhes Adicionais", default=dict, blank=True)
    created_at = models.DateTimeField("Data / Hora", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Registro de Auditoria"
        verbose_name_plural = "Registros de Auditoria"
        ordering = ["-created_at"]

    def __str__(self):
        user_display = self.user.email if self.user else "Sistema/Anônimo"
        return f"[{self.created_at:%Y-%m-%d %H:%M:%S}] {user_display} - {self.get_action_display()}"

    def save(self, *args, **kwargs):
        # Garante append-only: proíbe edição de logs existentes
        if self.pk:
            raise PermissionDenied(
                "Registros de auditoria são estritamente imutáveis e não podem ser editados."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Garante append-only: proíbe exclusão direta de logs individuais
        raise PermissionDenied(
            "Registros de auditoria são estritamente imutáveis e não podem ser excluídos."
        )

    @classmethod
    def log(cls, action: str, request=None, user=None, target="", details=None):
        """Método utilitário para gravação rápida e segura de logs de auditoria."""
        ip = None
        user_agent = ""
        current_user = user

        if request:
            # Obtém IP respeitando proxies confiáveis configurados
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                ip = x_forwarded_for.split(",")[0].strip()
            else:
                ip = request.META.get("REMOTE_ADDR")

            user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
            if current_user is None and hasattr(request, "user") and request.user.is_authenticated:
                current_user = request.user

        return cls.objects.create(
            user=current_user,
            action=action,
            target=target[:255],
            ip_address=ip,
            user_agent=user_agent,
            details=details or {},
        )
