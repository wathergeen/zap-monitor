from unittest.mock import MagicMock, patch

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.groups.evolution_client import EvolutionReadOnlyClient
from apps.groups.models import EvolutionInstance, Group, GroupMembership
from apps.groups.tasks import sync_group_participants
from apps.messages_monitor.models import WebhookEvent
from apps.messages_monitor.tasks import process_webhook_event_task
from apps.messages_monitor.views import verify_webhook_secret


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="admin_evo@example.com",
        password="SuperPassword123!",
    )


@pytest.fixture
def test_instance(db):
    return EvolutionInstance.objects.create(
        name="Bot Principal Teste",
        api_url="http://evolution-api:8080",
        api_key="test-api-key-123",
        instance_name="bot-principal",
        webhook_secret="evo-secret-db-999",
        webhook_secret_old="evo-secret-old-888",
        active=True,
        last_state="open",
    )


@pytest.mark.django_db
def test_evolution_instance_model(test_instance):
    assert str(test_instance) == "Bot Principal Teste (bot-principal)"
    assert test_instance.is_connected is True

    test_instance.last_state = "close"
    assert test_instance.is_connected is False

    test_instance.last_state = "connected"
    assert test_instance.is_connected is True


@pytest.mark.django_db
def test_evolution_client_with_instance(test_instance):
    client = EvolutionReadOnlyClient(instance=test_instance)
    assert client.base_url == "http://evolution-api:8080"
    assert client.api_key == "test-api-key-123"
    assert client.instance == "bot-principal"
    assert client._get_headers()["apikey"] == "test-api-key-123"

    # Test get_connection_state with mocked 200 response
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "open"}
        mock_get.return_value = mock_resp

        res = client.get_connection_state()
        assert res.get("state") == "open"
        test_instance.refresh_from_db()
        assert test_instance.last_state == "open"
        assert test_instance.last_checked_at is not None

    # Test get_connection_state with HTTP error
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        res = client.get_connection_state()
        assert res.get("state") == "disconnected"
        test_instance.refresh_from_db()
        assert test_instance.last_state == "disconnected"

    # Test get_connection_state with connection exception
    with patch("httpx.Client.get", side_effect=Exception("Connection refused")):
        res = client.get_connection_state()
        assert res.get("state") == "unavailable"
        test_instance.refresh_from_db()
        assert test_instance.last_state == "unavailable"


@pytest.mark.django_db
def test_webhook_secret_validation_from_db(test_instance):
    factory = Client()

    # Requisição com secret ativo no banco
    req1 = factory.get("/", HTTP_X_WEBHOOK_SECRET="evo-secret-db-999").wsgi_request
    assert verify_webhook_secret(req1) is True

    # Requisição com secret antigo (rotação) no banco
    req2 = factory.get("/", HTTP_X_WEBHOOK_SECRET="evo-secret-old-888").wsgi_request
    assert verify_webhook_secret(req2) is True

    # Requisição com secret incorreto
    req3 = factory.get("/", HTTP_X_WEBHOOK_SECRET="wrong-secret").wsgi_request
    assert verify_webhook_secret(req3) is False

    # Instância inativa não deve validar
    test_instance.active = False
    test_instance.save()
    req4 = factory.get("/", HTTP_X_WEBHOOK_SECRET="evo-secret-db-999").wsgi_request
    assert verify_webhook_secret(req4) is False


@pytest.mark.django_db
def test_dashboard_settings_evolution_crud(client, admin_user):
    client.force_login(admin_user)

    # 1. Adicionar nova instância
    resp_add = client.post(
        "/settings/",
        {
            "form_action": "add_evolution_instance",
            "name": "Bot Suporte Comercial",
            "api_url": "https://evo.comercial.com/",
            "api_key": "key-comercial-456",
            "instance_name": "suporte-comercial",
            "webhook_secret": "secret-comercial-789",
            "active": "on",
        },
    )
    assert resp_add.status_code == 302
    inst = EvolutionInstance.objects.get(instance_name="suporte-comercial")
    assert inst.name == "Bot Suporte Comercial"
    assert inst.api_url == "https://evo.comercial.com"  # Trailing slash removido
    assert inst.active is True
    assert AuditLog.objects.filter(target__icontains="Bot Suporte Comercial").exists()

    # 2. Atualizar instância (com rotação de secret)
    resp_update = client.post(
        "/settings/",
        {
            "form_action": "update_evolution_instance",
            "instance_id": inst.id,
            "name": "Bot Suporte Comercial Renomeado",
            "api_url": "https://evo.comercial.com",
            "api_key": "key-comercial-456",
            "instance_name": "suporte-comercial",
            "webhook_secret": "secret-comercial-NOVO",
            "active": "on",
        },
    )
    assert resp_update.status_code == 302
    inst.refresh_from_db()
    assert inst.name == "Bot Suporte Comercial Renomeado"
    assert inst.webhook_secret == "secret-comercial-NOVO"
    assert inst.webhook_secret_old == "secret-comercial-789"

    # 3. Pausar / Alternar status
    resp_toggle = client.post(
        "/settings/",
        {
            "form_action": "toggle_evolution_instance",
            "instance_id": inst.id,
        },
    )
    assert resp_toggle.status_code == 302
    inst.refresh_from_db()
    assert inst.active is False

    # 4. Remover instância
    resp_del = client.post(
        "/settings/",
        {
            "form_action": "delete_evolution_instance",
            "instance_id": inst.id,
        },
    )
    assert resp_del.status_code == 302
    assert not EvolutionInstance.objects.filter(id=inst.id).exists()


@pytest.mark.django_db
def test_test_evolution_instance_view(client, admin_user, test_instance):
    client.force_login(admin_user)

    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "open"}
        mock_get.return_value = mock_resp

        resp = client.post(f"/settings/evolution/test/{test_instance.id}/")
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "Conectado" in content
        assert "open" in content
        assert "Verificado:" in content

        test_instance.refresh_from_db()
        assert test_instance.last_state == "open"
        assert test_instance.last_checked_at is not None


@pytest.mark.django_db
def test_test_evolution_instance_csrf_support(admin_user, test_instance):
    """Garante que requisições HTMX com token CSRF via cabeçalho HTTP_X_CSRFTOKEN são aceitas."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)

    # Obter token CSRF acessando settings
    resp_get = client.get("/settings/")
    assert resp_get.status_code == 200
    csrf_token = resp_get.cookies["csrftoken"].value

    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "open"}
        mock_get.return_value = mock_resp

        # Requisição HTMX simulada enviando X-CSRFToken
        resp = client.post(
            f"/settings/evolution/test/{test_instance.id}/",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        assert "Conectado" in resp.content.decode("utf-8")


@pytest.mark.django_db
def test_groups_list_refresh_connection(client, admin_user, test_instance):
    client.force_login(admin_user)
    with patch(
        "apps.groups.evolution_client.EvolutionReadOnlyClient.get_connection_state"
    ) as mock_state:
        mock_state.return_value = {"state": "open"}
        resp = client.get("/groups/?refresh=1")
        assert resp.status_code == 302
        assert resp.url == "/groups/"
        mock_state.assert_called_once()


@pytest.mark.django_db
def test_group_sync_service_and_actions(client, admin_user, test_instance):
    import io

    from django.core.management import call_command

    from apps.groups.services import GroupSyncService

    client.force_login(admin_user)

    fake_groups = [
        {"id": "120363000000000001@g.us", "subject": "Grupo Fake 1"},
        {"id": "120363000000000002@g.us", "subject": "Grupo Fake 2"},
    ]

    with (
        patch(
            "apps.groups.evolution_client.EvolutionReadOnlyClient.fetch_all_groups",
            return_value=fake_groups,
        ),
        patch(
            "apps.groups.tasks.sync_group_participants",
            return_value="OK",
        ),
    ):
        # 1. Test via Service directly
        synced = GroupSyncService.sync_instance_groups(test_instance)
        assert len(synced) == 2
        assert Group.objects.filter(jid="120363000000000001@g.us").exists()

        # 2. Test via groups list view ?sync=1
        resp_sync = client.get("/groups/?sync=1")
        assert resp_sync.status_code == 302
        assert resp_sync.url == "/groups/"

        # 3. Test via settings view sync_evolution_instance_groups action
        resp_action = client.post(
            "/settings/",
            {
                "form_action": "sync_evolution_instance_groups",
                "instance_id": test_instance.id,
            },
        )
        assert resp_action.status_code == 302
        assert "?tab=evolution" in resp_action.url

        # 4. Test management command sync_groups
        out = io.StringIO()
        call_command("sync_groups", stdout=out)
        assert "sincronizados com sucesso" in out.getvalue()


@pytest.mark.django_db
def test_sync_group_participants_with_instance(test_instance):
    group = Group.objects.create(
        jid="120363999999999999@g.us",
        name="Grupo Teste Instância",
        monitored=True,
        instance=test_instance,
    )

    fake_participants = [
        {
            "id": "5511999998888@s.whatsapp.net",
            "phoneNumber": "5511999998888",
            "pushName": "Membro Evo",
            "admin": "admin",
        }
    ]

    with patch(
        "apps.groups.evolution_client.EvolutionReadOnlyClient.get_group_participants"
    ) as mock_p:
        mock_p.return_value = fake_participants
        result = sync_group_participants(group_id=group.id)
        assert "Sincronizados 1 participantes" in result
        assert GroupMembership.objects.filter(group=group).exists()


@pytest.mark.django_db
def test_webhook_event_auto_assigns_instance(test_instance):
    group = Group.objects.create(
        jid="120363555555555555@g.us",
        name="Grupo Auto Assign",
        monitored=True,
    )
    assert group.instance is None

    payload = {
        "event": "messages.upsert",
        "instance": "bot-principal",
        "data": {
            "key": {
                "remoteJid": group.jid,
                "fromMe": False,
                "id": "MSG_EVO_01",
                "participant": "5511977776666@s.whatsapp.net",
            },
            "message": {"conversation": "Olá mundo do bot!"},
            "messageTimestamp": 1700000000,
        },
    }

    event = WebhookEvent.objects.create(
        event_type="MESSAGES.UPSERT",
        status=WebhookEvent.Status.PENDING,
        payload=payload,
    )

    process_webhook_event_task(event.id)
    group.refresh_from_db()
    assert group.instance == test_instance
