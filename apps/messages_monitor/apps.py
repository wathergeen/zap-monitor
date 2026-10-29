from django.apps import AppConfig


class MessagesMonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.messages_monitor"
    verbose_name = "Monitoramento e Ingestão de Mensagens"
