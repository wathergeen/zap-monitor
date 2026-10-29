from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.groups.models import Group, Member
from apps.groups.tasks import sync_all_monitored_groups, sync_group_participants
from apps.health.tasks import check_webhook_inactivity_task
from apps.messages_monitor.models import Message
from apps.messages_monitor.tasks import apply_retention_policy_task


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def group(db):
    return Group.objects.create(jid="health_group@g.us", name="Grupo Saúde", monitored=True)


@pytest.mark.django_db
def test_healthz_endpoint(client):
    """Testa endpoint de integridade /healthz."""
    resp = client.get("/healthz")
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "database" in data
    assert "task_queue" in data
    assert "evolution_api" in data


@pytest.mark.django_db
def test_webhook_inactivity_alert_task(group):
    """Testa tarefa de verificação de inatividade de mensagens."""
    # Sem mensagens
    res = check_webhook_inactivity_task()
    assert "Alerta emitido" in res

    # Com mensagem recente
    member = Member.objects.create(phone="5511999995555")
    Message.objects.create(
        wa_id="MSG_RECENT_01",
        group=group,
        member=member,
        text="Mensagem recente",
        sent_at=timezone.now(),
    )
    res_ok = check_webhook_inactivity_task()
    assert "normal" in res_ok.lower()


@pytest.mark.django_db
def test_retention_policy_task(group):
    """Testa limpeza de mensagens antigas conforme LGPD."""
    member = Member.objects.create(phone="5511999996666")
    old_date = timezone.now() - timezone.timedelta(days=400)

    Message.objects.create(
        wa_id="MSG_OLD_01",
        group=group,
        member=member,
        text="Mensagem antiga para expirar",
        sent_at=old_date,
    )
    Message.objects.create(
        wa_id="MSG_NEW_01",
        group=group,
        member=member,
        text="Mensagem nova preservada",
        sent_at=timezone.now(),
    )

    apply_retention_policy_task()
    assert not Message.objects.filter(wa_id="MSG_OLD_01").exists()
    assert Message.objects.filter(wa_id="MSG_NEW_01").exists()


@pytest.mark.django_db
def test_anonymize_member_command(group):
    """Testa comando de gestão anonymize_member (direito do titular LGPD)."""
    member = Member.objects.create(phone="5511999997777", push_name="Titular Dados")
    msg = Message.objects.create(
        wa_id="MSG_ANON_01",
        group=group,
        member=member,
        text="Segredo confidencial do titular",
        sent_at=timezone.now(),
    )

    call_command("anonymize_member", member.id, confirm=True)

    member.refresh_from_db()
    msg.refresh_from_db()

    assert member.phone is None
    assert member.anonymized_at is not None
    assert "REMOVIDO EM CONFORMIDADE COM A LGPD" in msg.text


@pytest.mark.django_db
def test_reindex_messages_command(group):
    """Testa comando reindex_messages."""
    call_command("reindex_messages")


@pytest.mark.django_db
@patch("apps.groups.tasks.EvolutionReadOnlyClient")
def test_sync_group_participants_task(mock_evo_class, group):
    """Testa sincronização de participantes via mock do client Evolution."""
    mock_client = MagicMock()
    mock_client.get_group_participants.return_value = [
        {"id": "5511988880001@s.whatsapp.net", "pushName": "Participante Sync 1", "admin": None},
        {"id": "5511988880002@s.whatsapp.net", "pushName": "Admin Sync 2", "admin": "admin"},
    ]
    mock_evo_class.return_value = mock_client

    result = sync_group_participants(group.id)
    assert "Sincronizados 2 participantes" in result

    assert Member.objects.filter(phone="5511988880001").exists()
    assert Member.objects.filter(phone="5511988880002").exists()

    sync_all_monitored_groups()
