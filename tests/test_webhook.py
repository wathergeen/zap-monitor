import json

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.groups.models import Group, GroupMembership, Member
from apps.messages_monitor.models import Message, WebhookEvent
from apps.messages_monitor.tasks import process_webhook_event_task


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def monitored_group(db):
    return Group.objects.create(
        jid="120363000000000001@g.us",
        name="Grupo de Teste Monitorado",
        monitored=True,
    )


@pytest.fixture
def unmonitored_group(db):
    return Group.objects.create(
        jid="120363999999999999@g.us",
        name="Grupo Não Monitorado",
        monitored=False,
    )


@pytest.mark.django_db
def test_webhook_rejects_missing_or_invalid_secret(api_client):
    """Rejeita requisições sem o cabeçalho X-Webhook-Secret ou com valor incorreto."""
    payload = {"event": "messages.upsert", "data": {}}

    # Sem cabeçalho
    resp1 = api_client.post(
        "/webhooks/evolution/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp1.status_code == 403

    # Com segredo incorreto
    resp2 = api_client.post(
        "/webhooks/evolution/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="segredo-invalido",
    )
    assert resp2.status_code == 403


@pytest.mark.django_db
@override_settings(
    EVOLUTION_WEBHOOK_SECRET="segredo-novo-12345",
    EVOLUTION_WEBHOOK_SECRET_OLD="segredo-antigo-98765",
)
def test_webhook_accepts_secret_rotation(api_client):
    """Aceita tanto a chave atual quanto a chave antiga durante a janela de rotação."""
    payload = {"event": "messages.upsert", "data": {}}

    # Chave nova
    resp1 = api_client.post(
        "/webhooks/evolution/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="segredo-novo-12345",
    )
    assert resp1.status_code == 200

    # Chave antiga
    resp2 = api_client.post(
        "/webhooks/evolution/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="segredo-antigo-98765",
    )
    assert resp2.status_code == 200

    # Chave via Query Parameter (?secret=...)
    resp3 = api_client.post(
        "/webhooks/evolution/?secret=segredo-novo-12345",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp3.status_code == 200

    # Chave via Query Parameter (?token=...)
    resp4 = api_client.post(
        "/webhooks/evolution/?token=segredo-novo-12345",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp4.status_code == 200


@pytest.mark.django_db
@override_settings(EVOLUTION_WEBHOOK_SECRET="secret-test")
def test_webhook_malformed_payload(api_client):
    """Retorna erro 400 se o corpo da requisição não for JSON válido."""
    resp = api_client.post(
        "/webhooks/evolution/",
        data="corpo-invalido-nao-json",
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="secret-test",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_webhook_ingestion_idempotency_and_success(monitored_group):
    """
    Testa ingestão de mensagem, resolução de participante e idempotência:
    reenviar o mesmo evento não cria mensagem duplicada.
    """
    payload_data = {
        "key": {
            "remoteJid": monitored_group.jid,
            "fromMe": False,
            "id": "MSG_ID_TEST_001",
            "participant": "5511999998888@s.whatsapp.net",
        },
        "pushName": "João da Silva",
        "message": {
            "conversation": "Olá mundo, primeira mensagem de teste!",
        },
        "messageTimestamp": 1714300000,
    }

    event = WebhookEvent.objects.create(
        event_type="MESSAGES_UPSERT",
        status=WebhookEvent.Status.PENDING,
        payload={"event": "messages.upsert", "data": payload_data},
    )

    # Processa primeira vez
    process_webhook_event_task(event.id)
    event.refresh_from_db()

    assert event.status == WebhookEvent.Status.PROCESSED
    assert Message.objects.count() == 1

    msg = Message.objects.get(wa_id="MSG_ID_TEST_001")
    assert msg.text == "Olá mundo, primeira mensagem de teste!"
    assert msg.member.push_name == "João da Silva"
    assert msg.member.phone == "5511999998888"
    assert GroupMembership.objects.filter(
        group=monitored_group, member=msg.member, left_at__isnull=True
    ).exists()

    # Reentrega do mesmo evento (Idempotência)
    event2 = WebhookEvent.objects.create(
        event_type="MESSAGES_UPSERT",
        status=WebhookEvent.Status.PENDING,
        payload={"event": "messages.upsert", "data": payload_data},
    )
    process_webhook_event_task(event2.id)

    # Contagem de mensagens continua exatamente 1
    assert Message.objects.count() == 1


@pytest.mark.django_db
def test_webhook_ignores_unmonitored_group(unmonitored_group):
    """Mensagens de grupos não marcados como monitorados são ignoradas e descartadas."""
    payload_data = {
        "key": {
            "remoteJid": unmonitored_group.jid,
            "fromMe": False,
            "id": "MSG_UNMONITORED_001",
            "participant": "5511999997777@s.whatsapp.net",
        },
        "message": {"conversation": "Mensagem que não deve ser salva"},
        "messageTimestamp": 1714300000,
    }

    event = WebhookEvent.objects.create(
        event_type="MESSAGES_UPSERT",
        status=WebhookEvent.Status.PENDING,
        payload={"event": "messages.upsert", "data": payload_data},
    )

    process_webhook_event_task(event.id)
    event.refresh_from_db()

    assert event.status == WebhookEvent.Status.IGNORED
    assert Message.objects.count() == 0


@pytest.mark.django_db
def test_webhook_ignores_from_me_messages(monitored_group):
    """Mensagens enviadas pelo próprio bot (fromMe=True) são ignoradas."""
    payload_data = {
        "key": {
            "remoteJid": monitored_group.jid,
            "fromMe": True,
            "id": "MSG_FROM_ME_001",
            "participant": "5511999991111@s.whatsapp.net",
        },
        "message": {"conversation": "Mensagem enviada por mim"},
        "messageTimestamp": 1714300000,
    }

    event = WebhookEvent.objects.create(
        event_type="MESSAGES_UPSERT",
        status=WebhookEvent.Status.PENDING,
        payload={"event": "messages.upsert", "data": payload_data},
    )

    process_webhook_event_task(event.id)
    event.refresh_from_db()

    assert event.status == WebhookEvent.Status.IGNORED
    assert Message.objects.count() == 0


@pytest.mark.django_db
def test_webhook_handles_edited_and_deleted_messages(monitored_group):
    """
    Testa mensagem editada e apagada:
    Mantém registro original, preenche timestamps de edição e exclusão.
    """
    member = Member.objects.create(phone="5511999992222", push_name="Carlos")
    msg = Message.objects.create(
        wa_id="MSG_EDIT_DEL_001",
        group=monitored_group,
        member=member,
        text="Texto original da mensagem",
        sent_at=timezone.now(),
    )

    # 1. Evento de edição
    edit_payload = {
        "key": {"id": "MSG_EDIT_DEL_001", "remoteJid": monitored_group.jid},
        "message": {"conversation": "Texto corrigido após edição"},
    }
    event_edit = WebhookEvent.objects.create(
        event_type="MESSAGES_UPDATE",
        payload={"data": edit_payload},
    )
    process_webhook_event_task(event_edit.id)

    msg.refresh_from_db()
    assert msg.text == "Texto corrigido após edição"
    assert msg.edited_at is not None

    # 2. Evento de exclusão
    delete_payload = {
        "key": {"id": "MSG_EDIT_DEL_001", "remoteJid": monitored_group.jid},
    }
    event_delete = WebhookEvent.objects.create(
        event_type="MESSAGES_DELETE",
        payload={"data": delete_payload},
    )
    process_webhook_event_task(event_delete.id)

    msg.refresh_from_db()
    assert msg.deleted_at is not None
    # Texto permanece preservado para auditoria
    assert msg.text == "Texto corrigido após edição"
