import csv
import io
import re
import zoneinfo
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db.models import Sum
from django.utils import html, timezone
from django.utils.safestring import mark_safe

from apps.analytics.models import MemberDailyStats, MemberMonthlyStats
from apps.groups.models import Group, GroupMembership, Member
from apps.keywords.models import KeywordHit
from apps.messages_monitor.models import Message


def get_app_timezone():
    tz_name = getattr(settings, "TIME_ZONE", "America/Sao_Paulo")
    return zoneinfo.ZoneInfo(tz_name)


def sanitize_csv_cell(val: Any) -> str:
    """
    Previne ataques de CSV Injection / Formula Injection.
    Prefixa células que começam com '=', '+', '-', '@' ou '\t', '\r' com apóstrofo (').
    """
    if val is None:
        return ""
    s = str(val).strip()
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def safe_highlight_terms(text: str, terms: List[str]) -> str:
    """
    Realiza o destaque de termos/palavras-chave com segurança estrita contra XSS:
    1. Escapa 100% do texto de entrada com html.escape (mensagens são dados não confiáveis).
    2. Aplica tag <mark> apenas nas palavras encontradas sem permitir injeção de HTML.
    """
    if not text:
        return ""
    escaped_text = html.escape(text)
    if not terms:
        return escaped_text

    for term in terms:
        if not term or not term.strip():
            continue
        escaped_term = html.escape(term.strip())
        pattern = re.compile(rf"({re.escape(escaped_term)})", re.IGNORECASE)
        escaped_text = pattern.sub(
            r'<mark class="bg-amber-200 dark:bg-amber-800 text-amber-900 dark:text-amber-100 px-1 rounded font-medium">\1</mark>',
            escaped_text,
        )

    return mark_safe(escaped_text)  # nosec B308 B703 # noqa: S308


class GroupMetricsService:
    """
    Cálculo de métricas, ranking, atividade mensal e relatórios.
    Lê primariamente das tabelas agregadas MemberMonthlyStats e MemberDailyStats.
    """

    def __init__(self, group: Group):
        self.group = group
        self.tz = get_app_timezone()

    def get_dashboard_kpis(
        self, year: Optional[int] = None, month: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calcula os cartões de indicadores do Dashboard para o grupo:
        - Total de mensagens no mês (lido da agregação)
        - Membros ativos no mês (>= 1 mensagem enviada)
        - Membros inativos no mês
        - Entradas e saídas no mês
        """
        now = timezone.now().astimezone(self.tz)
        target_year = year or now.year
        target_month = month or now.month
        month_date = date(target_year, target_month, 1)

        start_date = datetime(target_year, target_month, 1, 0, 0, 0, tzinfo=self.tz)
        if target_month == 12:
            end_date = datetime(target_year + 1, 1, 1, 0, 0, 0, tzinfo=self.tz)
        else:
            end_date = datetime(target_year, target_month + 1, 1, 0, 0, 0, tzinfo=self.tz)

        # Consulta rápida na tabela agregada MemberMonthlyStats
        monthly_qs = MemberMonthlyStats.objects.filter(
            group=self.group, month=month_date, message_count__gt=0
        )
        total_messages = monthly_qs.aggregate(total=Sum("message_count"))["total"] or 0
        active_member_ids = set(monthly_qs.values_list("member_id", flat=True))

        # Se as estatísticas ainda não foram povoadas (ex: antes do primeiro processamento ou rebuild_stats),
        # faz fallback transparente para a tabela de mensagens
        if total_messages == 0 and not monthly_qs.exists():
            msg_qs = Message.objects.filter(
                group=self.group,
                sent_at__gte=start_date,
                sent_at__lt=end_date,
            ).exclude(type=Message.MessageType.REACTION)
            total_messages = msg_qs.count()
            active_member_ids = set(msg_qs.values_list("member_id", flat=True).distinct())

        active_members_count = len(active_member_ids)

        # Participantes atualmente no grupo (left_at IS NULL)
        current_members = set(
            GroupMembership.objects.filter(
                group=self.group,
                left_at__isnull=True,
            ).values_list("member_id", flat=True)
        )

        current_members_count = len(current_members)
        inactive_members_count = max(
            0, current_members_count - len(active_member_ids.intersection(current_members))
        )

        # Novas entradas no mês
        new_entries = GroupMembership.objects.filter(
            group=self.group,
            joined_at__gte=start_date,
            joined_at__lt=end_date,
        ).count()

        # Novas saídas no mês
        exits = GroupMembership.objects.filter(
            group=self.group,
            left_at__gte=start_date,
            left_at__lt=end_date,
        ).count()

        # Mensagens apagadas no mês
        deleted_messages_count = Message.objects.filter(
            group=self.group,
            sent_at__gte=start_date,
            sent_at__lt=end_date,
            deleted_at__isnull=False,
        ).count()

        return {
            "total_messages": total_messages,
            "active_members_count": active_members_count,
            "inactive_members_count": inactive_members_count,
            "current_members_count": current_members_count,
            "new_entries": new_entries,
            "exits": exits,
            "deleted_messages_count": deleted_messages_count,
            "target_year": target_year,
            "target_month": target_month,
        }

    def get_daily_activity(self, days: int = 30) -> Dict[str, Any]:
        """
        Retorna agregação de mensagens por dia dos últimos N dias a partir de MemberDailyStats.
        """
        since_date = (timezone.now() - timezone.timedelta(days=days)).date()
        daily_stats = (
            MemberDailyStats.objects.filter(group=self.group, day__gte=since_date)
            .values("day")
            .annotate(count=Sum("message_count"))
            .order_by("day")
        )

        if not daily_stats.exists():
            # Fallback transparente para Message se estatísticas diárias estiverem vazias
            qs = (
                Message.objects.filter(group=self.group, sent_at__date__gte=since_date)
                .values("sent_at__date")
                .annotate(count=Sum("id"))
                .order_by("sent_at__date")
            )
            labels = [item["sent_at__date"].strftime("%d/%m") for item in qs]
            data = [item["count"] for item in qs]
            return {"labels": labels, "data": data}

        labels = [item["day"].strftime("%d/%m") for item in daily_stats]
        data = [item["count"] for item in daily_stats]
        return {"labels": labels, "data": data}

    def get_most_active_ranking(
        self, limit: int = 10, start_date=None, end_date=None
    ) -> List[Dict[str, Any]]:
        """
        Ranking dos membros com maior volume de mensagens no período lido das estatísticas.
        """
        qs = MemberMonthlyStats.objects.filter(group=self.group)
        if start_date:
            qs = qs.filter(
                month__gte=start_date.date().replace(day=1)
                if hasattr(start_date, "date")
                else start_date
            )
        if end_date:
            qs = qs.filter(
                month__lte=end_date.date().replace(day=1) if hasattr(end_date, "date") else end_date
            )

        ranking = (
            qs.values("member_id", "member__push_name", "member__phone")
            .annotate(total=Sum("message_count"))
            .order_by("-total")[:limit]
        )

        if not ranking.exists():
            # Fallback para Message se MemberMonthlyStats ainda não tiver dados
            msg_qs = Message.objects.filter(group=self.group).exclude(
                type=Message.MessageType.REACTION
            )
            if start_date:
                msg_qs = msg_qs.filter(sent_at__gte=start_date)
            if end_date:
                msg_qs = msg_qs.filter(sent_at__lte=end_date)
            ranking = (
                msg_qs.values("member_id", "member__push_name", "member__phone")
                .annotate(total=Sum("id"))
                .order_by("-total")[:limit]
            )

        return list(ranking)

    def is_member_active_in_month(self, member: Member, year: int, month: int) -> bool:
        """
        Retorna True se o membro enviou pelo menos 1 mensagem no mês de referência.
        """
        month_date = date(year, month, 1)
        stat = MemberMonthlyStats.objects.filter(
            group=self.group, member=member, month=month_date, message_count__gt=0
        ).exists()
        if stat:
            return True

        # Fallback para Message
        start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=self.tz)
        if month == 12:
            end_date = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=self.tz)
        else:
            end_date = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=self.tz)

        return (
            Message.objects.filter(
                group=self.group,
                member=member,
                sent_at__gte=start_date,
                sent_at__lt=end_date,
            )
            .exclude(type=Message.MessageType.REACTION)
            .exists()
        )


class ExportService:
    """
    Serviço de geração e exportação de relatórios em formato CSV,
    imunizado contra CSV Injection e sanitizado.
    """

    @classmethod
    def export_group_participants_csv(cls, group: Group) -> str:
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        writer.writerow(
            [
                sanitize_csv_cell("ID Participante"),
                sanitize_csv_cell("Nome WhatsApp"),
                sanitize_csv_cell("Telefone Mascarado"),
                sanitize_csv_cell("Papel"),
                sanitize_csv_cell("Data de Entrada"),
                sanitize_csv_cell("Data de Saída"),
                sanitize_csv_cell("Status Atual"),
                sanitize_csv_cell("Total de Mensagens"),
            ]
        )

        memberships = GroupMembership.objects.filter(group=group).select_related("member")
        for m in memberships:
            # Consulta otimizada somando message_count de MemberMonthlyStats
            total_msgs = (
                MemberMonthlyStats.objects.filter(group=group, member=m.member).aggregate(
                    total=Sum("message_count")
                )["total"]
                or 0
            )
            if total_msgs == 0:
                total_msgs = Message.objects.filter(group=group, member=m.member).count()

            writer.writerow(
                [
                    sanitize_csv_cell(m.member.id),
                    sanitize_csv_cell(m.member.display_name),
                    sanitize_csv_cell(m.member.masked_phone),
                    sanitize_csv_cell(m.get_role_display()),
                    sanitize_csv_cell(m.joined_at.strftime("%d/%m/%Y %H:%M")),
                    sanitize_csv_cell(
                        m.left_at.strftime("%d/%m/%Y %H:%M") if m.left_at else "Presente"
                    ),
                    sanitize_csv_cell("Presente" if m.is_active else "Saiu"),
                    sanitize_csv_cell(total_msgs),
                ]
            )

        return output.getvalue()

    @classmethod
    def export_keyword_report_csv(cls, group: Group, keyword_term: str) -> str:
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        writer.writerow(
            [
                sanitize_csv_cell("ID Mensagem"),
                sanitize_csv_cell("Data / Hora"),
                sanitize_csv_cell("Participante"),
                sanitize_csv_cell("Telefone Mascarado"),
                sanitize_csv_cell("Termo Citado"),
                sanitize_csv_cell("Conteúdo da Mensagem"),
            ]
        )

        # Consulta eficiente pela tabela KeywordHit indexada
        hits = (
            KeywordHit.objects.filter(group=group, keyword__term__iexact=keyword_term)
            .select_related("message", "member", "keyword")
            .order_by("-sent_at")
        )

        if hits.exists():
            for hit in hits:
                writer.writerow(
                    [
                        sanitize_csv_cell(hit.message.wa_id),
                        sanitize_csv_cell(hit.sent_at.strftime("%d/%m/%Y %H:%M:%S")),
                        sanitize_csv_cell(hit.member.display_name),
                        sanitize_csv_cell(hit.member.masked_phone),
                        sanitize_csv_cell(hit.keyword.term),
                        sanitize_csv_cell(hit.message.text),
                    ]
                )
        else:
            # Fallback para Message se hits ainda não tiverem sido povoados
            messages = (
                Message.objects.filter(group=group, text__icontains=keyword_term)
                .select_related("member")
                .order_by("-sent_at")
            )
            for msg in messages:
                writer.writerow(
                    [
                        sanitize_csv_cell(msg.wa_id),
                        sanitize_csv_cell(msg.sent_at.strftime("%d/%m/%Y %H:%M:%S")),
                        sanitize_csv_cell(msg.member.display_name),
                        sanitize_csv_cell(msg.member.masked_phone),
                        sanitize_csv_cell(keyword_term),
                        sanitize_csv_cell(msg.text),
                    ]
                )

        return output.getvalue()
