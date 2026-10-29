from django.db import models

from apps.groups.models import Group, Member


class MemberMonthlyStats(models.Model):
    """
    Tabela agregada mensal de mensagens por participante em cada grupo.
    Atualizada via upsert na mesma transação que insere a mensagem.
    Garante consultas analíticas e relatórios O(1) sem varrer a tabela de mensagens.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="monthly_stats", verbose_name="Grupo"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="monthly_stats", verbose_name="Participante"
    )
    month = models.DateField(
        "Mês de Referência (YYYY-MM-01)",
        db_index=True,
        help_text="Primeiro dia do mês correspondente",
    )
    message_count = models.PositiveIntegerField("Total de Mensagens", default=0)
    first_message_at = models.DateTimeField("Primeira Mensagem no Mês", null=True, blank=True)
    last_message_at = models.DateTimeField("Última Mensagem no Mês", null=True, blank=True)

    class Meta:
        verbose_name = "Estatística Mensal de Participante"
        verbose_name_plural = "Estatísticas Mensais de Participantes"
        unique_together = ("group", "member", "month")
        indexes = [
            models.Index(fields=["group", "month"], name="idx_monthly_stats_grp_mth"),
            models.Index(fields=["member", "month"], name="idx_monthly_stats_mbr_mth"),
        ]

    def __str__(self):
        return f"{self.member} em {self.group} ({self.month:%Y-%m}): {self.message_count} msgs"


class MemberDailyStats(models.Model):
    """
    Tabela agregada diária de mensagens por participante em cada grupo.
    Utilizada para gráficos de linha temporal e atividade diária.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="daily_stats", verbose_name="Grupo"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="daily_stats", verbose_name="Participante"
    )
    day = models.DateField("Dia", db_index=True)
    message_count = models.PositiveIntegerField("Total de Mensagens no Dia", default=0)

    class Meta:
        verbose_name = "Estatística Diária de Participante"
        verbose_name_plural = "Estatísticas Diárias de Participantes"
        unique_together = ("group", "member", "day")
        indexes = [
            models.Index(fields=["group", "day"], name="idx_daily_stats_grp_day"),
            models.Index(fields=["member", "day"], name="idx_daily_stats_mbr_day"),
        ]

    def __str__(self):
        return f"{self.member} em {self.group} ({self.day:%Y-%m-%d}): {self.message_count} msgs"
