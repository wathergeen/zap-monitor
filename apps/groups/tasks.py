import logging

from django.utils import timezone
from procrastinate.contrib.django import app

from .evolution_client import EvolutionReadOnlyClient
from .models import Group, GroupMembership
from .services import MemberResolutionService

logger = logging.getLogger("apps.groups")


@app.task(queue="default")
def sync_group_participants(group_id: int):
    """
    Sincroniza os participantes de um grupo monitorado específico.
    Compara a lista atual da Evolution com as associações ativas no banco.
    """
    try:
        group = Group.objects.get(pk=group_id, monitored=True)
    except Group.DoesNotExist:
        return f"Grupo {group_id} não encontrado ou não monitorado."

    client = EvolutionReadOnlyClient(instance=group.instance)
    participants = client.get_group_participants(group.jid)
    if not participants:
        logger.info(f"Nenhum participante retornado pela Evolution para o grupo {group.jid}")
        return f"Sem dados para grupo {group.jid}"

    current_member_ids = set()
    now = timezone.now()

    for p in participants:
        # A Evolution pode enviar `id`, `userJid`, `phoneNumber`, `admin`
        identifier = p.get("id") or p.get("userJid") or ""
        phone = p.get("phoneNumber")
        lid = p.get("lid")
        push_name = p.get("pushName") or p.get("name") or ""
        admin_type = p.get("admin")  # 'admin', 'superadmin', ou None

        role = GroupMembership.Role.MEMBER
        if admin_type == "superadmin":
            role = GroupMembership.Role.SUPERADMIN
        elif admin_type == "admin":
            role = GroupMembership.Role.ADMIN

        member, _ = MemberResolutionService.get_or_create_member(
            identifier=identifier,
            push_name=push_name,
            lid=lid,
            phone=phone,
        )
        current_member_ids.add(member.id)

        # Garante associação ativa
        MemberResolutionService.record_membership(
            group=group, member=member, role=role, is_leaving=False
        )

    # Identificar quem estava ativo no grupo mas não está mais na lista atual retornada (saída)
    active_memberships = GroupMembership.objects.filter(group=group, left_at__isnull=True)
    for m in active_memberships:
        if m.member_id not in current_member_ids:
            m.left_at = now
            m.save(update_fields=["left_at"])

    group.last_synced_at = now
    group.save(update_fields=["last_synced_at"])
    return (
        f"Sincronizados {len(current_member_ids)} participantes no grupo {group.name or group.jid}"
    )


@app.periodic(cron="0 */1 * * *")
@app.task(queue="default", queueing_lock="sync_all_monitored_groups")
def sync_all_monitored_groups(timestamp: int = 0):
    """
    Dispara periodicamente a cada 1 hora a sincronização de todos os grupos monitorados.
    Utiliza queueing_lock para evitar sobreposição de execuções.
    """
    monitored_groups = Group.objects.filter(monitored=True).values_list("id", flat=True)
    for group_id in monitored_groups:
        sync_group_participants.defer(group_id=group_id)
    return f"Enfileirada sincronização para {len(monitored_groups)} grupos monitorados."
