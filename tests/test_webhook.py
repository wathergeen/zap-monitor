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


@pytest.mark.django_db
def test_message_deletion_mapping_and_protocol_revoke(monitored_group):
    """
    Testa o mapeamento de mensagens apagadas via:
    1. messages.delete com formato keys lista
    2. messages.update com status REVOKED
    3. messages.upsert com protocolMessage tipo REVOKE (0)
    4. Propriedade is_deleted e MessageQuerySet (.deleted() e .active())
    """
    member_alice = Member.objects.create(
        phone="5511999998888",
        push_name="Alice",
    )
    GroupMembership.objects.create(
        group=monitored_group,
        member=member_alice,
        role=GroupMembership.Role.MEMBER,
    )

    now = timezone.now()
    # Criação de mensagens para cada cenário
    msg_del_keys = Message.objects.create(
        wa_id="MSG_DEL_KEYS_001",
        group=monitored_group,
        member=member_alice,
        type=Message.MessageType.TEXT,
        text="Mensagem a ser apagada com payload keys",
        sent_at=now,
    )
    msg_update_rev = Message.objects.create(
        wa_id="MSG_UPD_REV_002",
        group=monitored_group,
        member=member_alice,
        type=Message.MessageType.TEXT,
        text="Mensagem a ser apagada com update REVOKED",
        sent_at=now,
    )
    msg_proto_rev = Message.objects.create(
        wa_id="MSG_PROTO_REV_003",
        group=monitored_group,
        member=member_alice,
        type=Message.MessageType.TEXT,
        text="Mensagem a ser apagada via protocolMessage revoke",
        sent_at=now,
    )

    assert not msg_del_keys.is_deleted
    assert Message.objects.filter(group=monitored_group).active().count() >= 3

    # 1. Evento messages.delete com payload no formato data.keys = [...]
    event_keys = WebhookEvent.objects.create(
        event_type="MESSAGES_DELETE",
        payload={"data": {"keys": [{"id": "MSG_DEL_KEYS_001", "remoteJid": monitored_group.jid}]}},
    )
    process_webhook_event_task(event_keys.id)
    msg_del_keys.refresh_from_db()
    assert msg_del_keys.is_deleted
    assert msg_del_keys.deleted_at is not None

    # 2. Evento messages.update com status REVOKED
    event_upd = WebhookEvent.objects.create(
        event_type="MESSAGES_UPDATE",
        payload={
            "data": [
                {
                    "key": {"id": "MSG_UPD_REV_002", "remoteJid": monitored_group.jid},
                    "update": {"status": "REVOKED"},
                }
            ]
        },
    )
    process_webhook_event_task(event_upd.id)
    msg_update_rev.refresh_from_db()
    assert msg_update_rev.is_deleted

    # 3. Evento messages.upsert com protocolMessage REVOKE
    event_upsert_revoke = WebhookEvent.objects.create(
        event_type="MESSAGES_UPSERT",
        payload={
            "data": {
                "key": {
                    "id": "PROTOCOL_REVOKE_CONTAINER_999",
                    "remoteJid": monitored_group.jid,
                    "fromMe": False,
                    "participant": member_alice.phone + "@s.whatsapp.net",
                },
                "message": {
                    "protocolMessage": {
                        "key": {
                            "id": "MSG_PROTO_REV_003",
                            "remoteJid": monitored_group.jid,
                        },
                        "type": 0,
                    }
                },
                "messageTimestamp": int(now.timestamp()),
            }
        },
    )
    process_webhook_event_task(event_upsert_revoke.id)
    msg_proto_rev.refresh_from_db()
    assert msg_proto_rev.is_deleted
    # Não deve ter criado mensagem lixo para o PROTOCOL_REVOKE_CONTAINER_999
    assert not Message.objects.filter(wa_id="PROTOCOL_REVOKE_CONTAINER_999").exists()

    # 4. MessageQuerySet
    deleted_msgs = Message.objects.filter(group=monitored_group).deleted()
    assert msg_del_keys in deleted_msgs
    assert msg_update_rev in deleted_msgs
    assert msg_proto_rev in deleted_msgs
