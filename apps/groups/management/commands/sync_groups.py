from django.core.management.base import BaseCommand

from apps.groups.models import EvolutionInstance
from apps.groups.services import GroupSyncService


class Command(BaseCommand):
    help = "Sincroniza todos os grupos de WhatsApp conhecidos pelas instâncias ativas da Evolution API v2."

    def add_arguments(self, parser):
        parser.add_argument(
            "--instance",
            type=str,
            help="Nome ou ID da instância específica para sincronizar",
        )

    def handle(self, *args, **options):
        instance_filter = options.get("instance")
        if instance_filter:
            inst = (
                EvolutionInstance.objects.filter(id=instance_filter).first()
                if instance_filter.isdigit()
                else None
            ) or EvolutionInstance.objects.filter(instance_name=instance_filter).first()
            if not inst:
                self.stderr.write(
                    self.style.ERROR(f"Instância '{instance_filter}' não encontrada.")
                )
                return
            self.stdout.write(
                f"Sincronizando grupos da instância {inst.name} ({inst.instance_name})..."
            )
            synced = GroupSyncService.sync_instance_groups(inst)
            self.stdout.write(
                self.style.SUCCESS(f"{len(synced)} grupos sincronizados com sucesso.")
            )
        else:
            self.stdout.write(
                "Sincronizando grupos de todas as instâncias ativas da Evolution API..."
            )
            synced = GroupSyncService.sync_all_instances_groups()
            self.stdout.write(
                self.style.SUCCESS(f"Total de {len(synced)} grupos sincronizados com sucesso.")
            )
