from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups.models import Member
from apps.messages_monitor.models import Message


class Command(BaseCommand):
    help = "Anonimiza todos os dados pessoais de um participante em conformidade com a LGPD (Direito do Titular)."

    def add_arguments(self, parser):
        parser.add_argument("member_id", type=int, help="ID do participante no banco")
        parser.add_argument(
            "--confirm", action="store_true", help="Confirmação expressa da anonimização"
        )

    def handle(self, *args, **options):
        member_id = options["member_id"]
        if not options["confirm"]:
            raise CommandError("Ação irreversível! Para executar, passe o parâmetro --confirm")

        try:
            member = Member.objects.get(pk=member_id)
        except Member.DoesNotExist as err:
            raise CommandError(f"Participante com ID {member_id} não encontrado.") from err

        now = timezone.now()
        original_identifier = member.phone or member.lid or str(member.id)

        # 1. Anonimizar cadastro do participante
        member.phone = None
        member.lid = None
        member.push_name = f"Titular Anonimizado #{member.id}"
        member.anonymized_at = now
        member.save()

        # 2. Anonimizar conteúdo de todas as mensagens enviadas pelo participante
        updated_msgs = Message.objects.filter(member=member).update(
            text="[CONTEÚDO REMOVIDO EM CONFORMIDADE COM A LGPD]",
        )

        # 3. Registrar auditoria LGPD
        AuditLog.objects.create(
            action=AuditLog.Action.MEMBER_ANONYMIZED,
            target=f"Member #{member_id}",
            details={"original_identifier": original_identifier, "messages_redacted": updated_msgs},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Participante #{member_id} anonimizado com sucesso! {updated_msgs} mensagens foram sanitizadas."
            )
        )
