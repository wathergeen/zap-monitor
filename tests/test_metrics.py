import zoneinfo
from datetime import datetime

import pytest
from django.utils import timezone

from apps.analytics.services import GroupMetricsService
from apps.groups.models import Group, GroupMembership, Member
from apps.groups.services import MemberResolutionService
from apps.messages_monitor.models import Message


@pytest.fixture
def test_group(db):
    return Group.objects.create(
        jid="120363000000000002@g.us", name="Grupo de Métricas", monitored=True
    )


@pytest.mark.django_db
def test_active_inactive_at_month_boundary_and_timezone(test_group):
    """
    Testa se um membro é corretamente considerado ativo/inativo no limite do mês
    de acordo com o fuso horário configurado (America/Sao_Paulo).
    """
    sp_tz = zoneinfo.ZoneInfo("America/Sao_Paulo")
    metrics_service = GroupMetricsService(test_group)

    member_active = Member.objects.create(phone="5511999990001", push_name="Ativo")
    member_inactive = Member.objects.create(phone="5511999990002", push_name="Inativo")

    # Associações ativas
    GroupMembership.objects.create(group=test_group, member=member_active, joined_at=timezone.now())
    GroupMembership.objects.create(
        group=test_group, member=member_inactive, joined_at=timezone.now()
    )

    # Mensagem enviada em 15/05/2026 às 14:00 (São Paulo)
    sent_may = datetime(2026, 5, 15, 14, 0, 0, tzinfo=sp_tz)
    Message.objects.create(
        wa_id="MSG_MAY_01",
        group=test_group,
        member=member_active,
        text="Mensagem em maio",
        sent_at=sent_may,
        type=Message.MessageType.TEXT,
    )

    # Mensagem enviada no último segundo de maio: 2026-05-31 23:59:59 SP
    sent_may_last = datetime(2026, 5, 31, 23, 59, 59, tzinfo=sp_tz)
    Message.objects.create(
        wa_id="MSG_MAY_LAST",
        group=test_group,
        member=member_active,
        text="Último segundo de maio",
        sent_at=sent_may_last,
        type=Message.MessageType.TEXT,
    )

    # Mensagem enviada no primeiro segundo de junho: 2026-06-01 00:00:01 SP
    sent_june_first = datetime(2026, 6, 1, 0, 0, 1, tzinfo=sp_tz)
    Message.objects.create(
        wa_id="MSG_JUNE_01",
        group=test_group,
        member=member_active,
        text="Primeiro segundo de junho",
        sent_at=sent_june_first,
        type=Message.MessageType.TEXT,
    )

    # Reação (NÃO deve contar como atividade)
    Message.objects.create(
        wa_id="MSG_REACTION_MAY",
        group=test_group,
        member=member_inactive,
        text="👍",
        sent_at=sent_may,
        type=Message.MessageType.REACTION,
    )

    # Em maio de 2026
    assert metrics_service.is_member_active_in_month(member_active, 2026, 5) is True
    assert metrics_service.is_member_active_in_month(member_inactive, 2026, 5) is False

    # KPIs de maio
    kpis_may = metrics_service.get_dashboard_kpis(year=2026, month=5)
    assert kpis_may["active_members_count"] == 1
    assert kpis_may["inactive_members_count"] == 1


@pytest.mark.django_db
def test_member_left_and_reentered(test_group):
    """
    Testa participante que saiu do grupo e posteriormente retornou:
    mantém histórico de entrada anterior e associa nova permanência ativa.
    """
    member = Member.objects.create(phone="5511999990003", push_name="Mariana")

    # 1. Entra no grupo
    m1 = MemberResolutionService.record_membership(
        group=test_group, member=member, is_leaving=False
    )
    assert m1.is_active is True
    assert (
        GroupMembership.objects.filter(
            group=test_group, member=member, left_at__isnull=True
        ).count()
        == 1
    )

    # 2. Sai do grupo
    m_left = MemberResolutionService.record_membership(
        group=test_group, member=member, is_leaving=True
    )
    assert m_left.is_active is False
    assert (
        GroupMembership.objects.filter(
            group=test_group, member=member, left_at__isnull=True
        ).count()
        == 0
    )

    # 3. Retorna ao grupo meses depois
    m_reenter = MemberResolutionService.record_membership(
        group=test_group, member=member, is_leaving=False
    )
    assert m_reenter.is_active is True
    # Há dois registros no histórico: um anterior encerrado e um ativo agora
    assert GroupMembership.objects.filter(group=test_group, member=member).count() == 2
    assert (
        GroupMembership.objects.filter(
            group=test_group, member=member, left_at__isnull=True
        ).count()
        == 1
    )
