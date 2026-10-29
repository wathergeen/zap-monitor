from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.analytics.models import MemberDailyStats, MemberMonthlyStats
from apps.analytics.services import ExportService
from apps.groups.models import EvolutionInstance, Group, Member
from apps.keywords.models import Keyword, KeywordHit
from apps.keywords.tasks import backfill_keyword_hits
from apps.messages_monitor.models import Message, WebhookEvent
from apps.messages_monitor.tasks import (
    extract_message_text_and_type,
    process_webhook_event_task,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user_with_totp(db):
    user = User.objects.create_user(
        email="totp_user@test.com", password="StrongPassword123!", role=User.Role.ADMIN
    )
    device = TOTPDevice.objects.create(user=user, name="Default", confirmed=True)
    return user, device


@pytest.fixture
def monitored_group(db):
    return Group.objects.create(jid="boost_group@g.us", name="Boost Group", monitored=True)


@pytest.mark.django_db
def test_totp_verify_flow_success_and_failure(client, user_with_totp):
    user, device = user_with_totp

    # Login inicial redireciona para 2FA
    resp_login = client.post(
        "/accounts/login/",
        {"email": "totp_user@test.com", "password": "StrongPassword123!"},
    )
    assert resp_login.status_code == 302
    assert "/accounts/2fa/verify/" in resp_login.headers["Location"]

    # Código inválido
    resp_bad = client.post("/accounts/2fa/verify/", {"code": "000000"})
    assert resp_bad.status_code == 200
    assert "Código de verificação incorreto" in resp_bad.content.decode("utf-8")

    # Código válido (mocking verify_token)
    device.verify_token = MagicMock(return_value=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            TOTPDevice.objects,
            "filter",
            MagicMock(return_value=MagicMock(first=MagicMock(return_value=device))),
        )
        resp_ok = client.post("/accounts/2fa/verify/", {"code": "123456"})
        assert resp_ok.status_code == 302
        assert resp_ok.headers["Location"] == "/"


def test_extract_message_text_and_type_all_media():
    """Testa extração de todos os tipos de mídia suportados."""
    # Imagem
    img_data = {"message": {"imageMessage": {"caption": "Foto da reunião"}}}
    text, m_type = extract_message_text_and_type(img_data)
    assert text == "Foto da reunião"
    assert m_type == "image"

    # Vídeo
    vid_data = {"message": {"videoMessage": {"caption": "Vídeo tutorial"}}}
    text, m_type = extract_message_text_and_type(vid_data)
    assert text == "Vídeo tutorial"
    assert m_type == "video"

    # Áudio
    audio_data = {"message": {"audioMessage": {}}}
    text, m_type = extract_message_text_and_type(audio_data)
    assert text == ""
    assert m_type == "audio"

    # Documento
    doc_data = {"message": {"documentMessage": {"fileName": "relatorio.pdf"}}}
    text, m_type = extract_message_text_and_type(doc_data)
    assert text == "relatorio.pdf"
    assert m_type == "document"

    # Figurinha
    sticker_data = {"message": {"stickerMessage": {}}}
    text, m_type = extract_message_text_and_type(sticker_data)
    assert text == ""
    assert m_type == "sticker"

    # Reação
    reaction_data = {"message": {"reactionMessage": {"text": "❤️"}}}
    text, m_type = extract_message_text_and_type(reaction_data)
    assert text == "❤️"
    assert m_type == "reaction"


@pytest.mark.django_db
def test_group_participants_and_group_update_events(monitored_group):
    """Testa processamento de eventos GROUP_PARTICIPANTS_UPDATE e GROUP_UPDATE."""
    # 1. Evento add
    ev_add = WebhookEvent.objects.create(
        event_type="GROUP_PARTICIPANTS_UPDATE",
        payload={
            "data": {
                "id": monitored_group.jid,
                "action": "add",
                "participants": ["5511977770001@s.whatsapp.net"],
            }
        },
    )
    process_webhook_event_task(ev_add.id)
    ev_add.refresh_from_db()
    assert ev_add.status == WebhookEvent.Status.PROCESSED
    assert Member.objects.filter(phone="5511977770001").exists()

    # 2. Evento promote
    ev_promote = WebhookEvent.objects.create(
        event_type="GROUP_PARTICIPANTS_UPDATE",
        payload={
            "data": {
                "id": monitored_group.jid,
                "action": "promote",
                "participants": ["5511977770001@s.whatsapp.net"],
            }
        },
    )
    process_webhook_event_task(ev_promote.id)

    # 3. Evento remove
    ev_remove = WebhookEvent.objects.create(
        event_type="GROUP_PARTICIPANTS_UPDATE",
        payload={
            "data": {
                "id": monitored_group.jid,
                "action": "remove",
                "participants": ["5511977770001@s.whatsapp.net"],
            }
        },
    )
    process_webhook_event_task(ev_remove.id)

    # 4. Evento group update
    ev_name = WebhookEvent.objects.create(
        event_type="GROUP_UPDATE",
        payload={"data": {"id": monitored_group.jid, "subject": "Novo Nome do Grupo"}},
    )
    process_webhook_event_task(ev_name.id)
    monitored_group.refresh_from_db()
    assert monitored_group.name == "Novo Nome do Grupo"


@pytest.mark.django_db
def test_rebuild_stats_command_and_aggregates(monitored_group):
    """Testa reconstrução e integridade de estatísticas agregadas a partir de Message."""
    member = Member.objects.create(phone="5511977770002", push_name="Membro Stats")
    now = timezone.now()

    # Criar mensagens
    for i in range(5):
        Message.objects.create(
            wa_id=f"REBUILD_MSG_{i}",
            group=monitored_group,
            member=member,
            text=f"Mensagem de teste {i}",
            sent_at=now,
        )

    # Executar comando de reconstrução para o grupo
    call_command("rebuild_stats", group_id=monitored_group.id)

    m_stat = MemberMonthlyStats.objects.filter(group=monitored_group, member=member).first()
    assert m_stat is not None
    assert m_stat.message_count == 5
    assert str(m_stat) != ""

    d_stat = MemberDailyStats.objects.filter(group=monitored_group, member=member).first()
    assert d_stat is not None
    assert d_stat.message_count == 5
    assert str(d_stat) != ""

    # Executar sem group_id (todos os grupos)
    call_command("rebuild_stats")


@pytest.mark.django_db
def test_backfill_keyword_hits_and_export_services(monitored_group):
    """Testa backfill de palavras-chave históricas e serviços de exportação CSV."""
    from apps.groups.models import GroupMembership

    member = Member.objects.create(phone="5511977770003", push_name="Membro Keyword")
    GroupMembership.objects.create(group=monitored_group, member=member)
    now = timezone.now()

    Message.objects.create(
        wa_id="KW_MSG_01",
        group=monitored_group,
        member=member,
        text="Aviso de urgência máxima aos membros",
        sent_at=now,
    )
    Message.objects.create(
        wa_id="KW_MSG_02",
        group=monitored_group,
        member=member,
        text="Mensagem sem o termo citado",
        sent_at=now,
    )

    kw = Keyword.objects.create(group=monitored_group, term="urgência")
    assert kw.term_normalized == "urgencia"

    # Executar backfill
    res = backfill_keyword_hits(kw.id)
    assert "hits criados" in res or "Backfill" in res

    hit = KeywordHit.objects.filter(keyword=kw).first()
    assert hit is not None
    assert hit.message.wa_id == "KW_MSG_01"
    assert str(hit) != ""

    # Testar exportação CSV
    csv_participants = ExportService.export_group_participants_csv(monitored_group)
    assert "Membro Keyword" in csv_participants

    csv_keywords = ExportService.export_keyword_report_csv(monitored_group, "urgência")
    assert "KW_MSG_01" in csv_keywords


@pytest.mark.django_db
def test_webhook_early_discard_non_group_and_unmonitored(client):
    """Testa descarte rápido de mensagens que não são de grupo ou grupo não monitorado."""
    EvolutionInstance.objects.create(
        name="Bot Boost Test",
        instance_name="bot-boost-test",
        api_url="http://evolution-test:8080",
        webhook_secret="webhook_shared_secret_min_32_characters_random",
        active=True,
    )
    # 1. Mensagem privada (sem @g.us)
    resp = client.post(
        "/webhooks/evolution/",
        data='{"event": "messages.upsert", "data": {"key": {"remoteJid": "5511999991111@s.whatsapp.net"}}}',
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="webhook_shared_secret_min_32_characters_random",
    )
    assert resp.status_code == 200
    assert resp.json().get("status") == "ignored"

    # 2. Grupo não monitorado
    resp_unmon = client.post(
        "/webhooks/evolution/",
        data='{"event": "messages.upsert", "data": {"key": {"remoteJid": "unmonitored_group@g.us"}}}',
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="webhook_shared_secret_min_32_characters_random",
    )
    assert resp_unmon.status_code == 200
    assert resp_unmon.json().get("status") == "ignored"


@pytest.mark.django_db
def test_healthz_failure_and_evolution_mock(client, monkeypatch):
    """Testa healthcheck retornando 503 quando banco falha."""
    from django.db import connection

    monkeypatch.setattr(
        connection, "ensure_connection", MagicMock(side_effect=Exception("DB Down"))
    )

    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["database"] == "error"


@pytest.mark.django_db
def test_find_messages_and_sync_group_recent_messages(monitored_group, monkeypatch):
    """Testa busca de mensagens na Evolution e sincronização de mensagens do grupo."""
    from apps.groups.evolution_client import EvolutionReadOnlyClient
    from apps.messages_monitor.tasks import sync_group_recent_messages

    client = EvolutionReadOnlyClient()

    # 1. Sem URL/instância
    client.base_url = ""
    assert client.find_messages(monitored_group.jid) == []

    # 2. Com resposta de sucesso
    client.base_url = "http://mock-evo:8080"
    client.instance = "Mock_Bot"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": {
            "records": [
                {
                    "key": {
                        "id": "MSG_SYNC_001",
                        "remoteJid": monitored_group.jid,
                        "participant": "5511999990001@s.whatsapp.net",
                        "fromMe": False,
                    },
                    "pushName": "Usuario Sync",
                    "message": {"conversation": "Mensagem sincronizada"},
                    "messageTimestamp": 1700000000,
                }
            ]
        }
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    monkeypatch.setattr("httpx.Client", lambda **kwargs: mock_client)

    records = client.find_messages(monitored_group.jid)
    assert len(records) == 1
    assert records[0]["key"]["id"] == "MSG_SYNC_001"

    # Testar sync_group_recent_messages
    monkeypatch.setattr(
        EvolutionReadOnlyClient, "find_messages", lambda self, jid, limit=50: records
    )
    synced = sync_group_recent_messages(monitored_group)
    assert synced == 1
    assert Message.objects.filter(wa_id="MSG_SYNC_001").exists()

    # Re-executar não deve duplicar
    synced_again = sync_group_recent_messages(monitored_group)
    assert synced_again == 0

    # Grupo não monitorado retorna 0
    monitored_group.monitored = False
    monitored_group.save()
    assert sync_group_recent_messages(monitored_group) == 0


@pytest.mark.django_db
def test_handle_group_participants_update_variations(monitored_group):
    from apps.groups.models import GroupMembership
    from apps.messages_monitor.tasks import _handle_group_participants_update

    # 1. Grupo não monitorado
    unmonitored_event = WebhookEvent.objects.create(
        event_type="group-participants.update",
        payload={
            "id": "not_monitored@g.us",
            "action": "add",
            "participants": ["5511999990002@s.whatsapp.net"],
        },
    )
    _handle_group_participants_update(unmonitored_event, unmonitored_event.payload)
    assert unmonitored_event.status == WebhookEvent.Status.IGNORED

    # 2. Demote action
    event_demote = WebhookEvent.objects.create(
        event_type="group-participants.update",
        payload={
            "id": monitored_group.jid,
            "action": "demote",
            "participants": ["5511999990003@s.whatsapp.net"],
        },
    )
    _handle_group_participants_update(event_demote, event_demote.payload)
    assert event_demote.status == WebhookEvent.Status.PROCESSED
    m = GroupMembership.objects.filter(group=monitored_group, member__phone="5511999990003").first()
    assert m is not None
    assert m.role == GroupMembership.Role.MEMBER


@pytest.mark.django_db
def test_apply_retention_policy_webhook_events():
    from datetime import timedelta

    from apps.messages_monitor.tasks import apply_retention_policy_task

    old_time = timezone.now() - timedelta(days=10)
    evt = WebhookEvent.objects.create(
        event_type="test.event",
        payload={"test": 1},
        status=WebhookEvent.Status.PROCESSED,
    )
    WebhookEvent.objects.filter(id=evt.id).update(received_at=old_time)

    res = apply_retention_policy_task(0)
    assert "eventos removidos" in res
    assert not WebhookEvent.objects.filter(id=evt.id).exists()
