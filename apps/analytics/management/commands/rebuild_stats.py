import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Max, Min
from django.db.models.functions import TruncDate, TruncMonth

from apps.analytics.models import MemberDailyStats, MemberMonthlyStats
from apps.groups.models import Group
from apps.messages_monitor.models import Message


class Command(BaseCommand):
    help = "Reconstrói as tabelas agregadas de estatísticas (MemberMonthlyStats e MemberDailyStats) a partir de Message."

    def add_arguments(self, parser):
        parser.add_argument(
            "--group-id",
            type=int,
            default=None,
            help="ID do grupo para reconstruir estatísticas (padrão: todos os grupos).",
        )

    def handle(self, *args, **options):
        group_id = options.get("group_id")
        self.stdout.write(self.style.NOTICE("Iniciando reconstrução de estatísticas agregadas..."))

        messages_qs = Message.objects.all()
        if group_id:
            group = Group.objects.filter(pk=group_id).first()
            if not group:
                self.stderr.write(self.style.ERROR(f"Grupo #{group_id} não encontrado."))
                return
            messages_qs = messages_qs.filter(group=group)

        with transaction.atomic():
            # 1. Limpar estatísticas anteriores no escopo selecionado
            if group_id:
                MemberMonthlyStats.objects.filter(group_id=group_id).delete()
                MemberDailyStats.objects.filter(group_id=group_id).delete()
            else:
                MemberMonthlyStats.objects.all().delete()
                MemberDailyStats.objects.all().delete()

            # 2. Reconstruir MemberMonthlyStats
            monthly_aggregates = (
                messages_qs.annotate(month=TruncMonth("sent_at"))
                .values("group_id", "member_id", "month")
                .annotate(
                    total=Count("id"),
                    first_msg=Min("sent_at"),
                    last_msg=Max("sent_at"),
                )
            )

            monthly_stats_objs = []
            for item in monthly_aggregates:
                month_dt = item["month"]
                if isinstance(month_dt, datetime.datetime):
                    month_date = month_dt.date().replace(day=1)
                else:
                    month_date = month_dt.replace(day=1)

                monthly_stats_objs.append(
                    MemberMonthlyStats(
                        group_id=item["group_id"],
                        member_id=item["member_id"],
                        month=month_date,
                        message_count=item["total"],
                        first_message_at=item["first_msg"],
                        last_message_at=item["last_msg"],
                    )
                )

            MemberMonthlyStats.objects.bulk_create(monthly_stats_objs, batch_size=1000)

            # 3. Reconstruir MemberDailyStats
            daily_aggregates = (
                messages_qs.annotate(day=TruncDate("sent_at"))
                .values("group_id", "member_id", "day")
                .annotate(total=Count("id"))
            )

            daily_stats_objs = []
            for item in daily_aggregates:
                day_val = item["day"]
                if isinstance(day_val, datetime.datetime):
                    day_val = day_val.date()

                daily_stats_objs.append(
                    MemberDailyStats(
                        group_id=item["group_id"],
                        member_id=item["member_id"],
                        day=day_val,
                        message_count=item["total"],
                    )
                )

            MemberDailyStats.objects.bulk_create(daily_stats_objs, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconstrução concluída com sucesso: "
                f"{len(monthly_stats_objs)} registros mensais e "
                f"{len(daily_stats_objs)} registros diários gerados."
            )
        )
