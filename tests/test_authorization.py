import pytest
from django.test import Client

from apps.accounts.models import User
from apps.groups.models import Group


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(email="admin@test.com", password="StrongPassword123!")


@pytest.fixture
def analista_user(db):
    user = User.objects.create_user(
        email="analista@test.com", password="StrongPassword123!", role=User.Role.ANALISTA
    )
    return user


@pytest.fixture
def leitor_user(db):
    user = User.objects.create_user(
        email="leitor@test.com", password="StrongPassword123!", role=User.Role.LEITOR
    )
    return user


@pytest.fixture
def group_a(db):
    return Group.objects.create(jid="group_a@g.us", name="Grupo Autorizado A", monitored=True)


@pytest.fixture
def group_b(db):
    return Group.objects.create(jid="group_b@g.us", name="Grupo Restrito B", monitored=True)


@pytest.mark.django_db
def test_unauthenticated_requests_redirect_to_login(client, group_a):
    """Rotas protegidas redirecionam usuários anônimos para o login."""
    urls = [
        "/",
        "/groups/",
        f"/groups/{group_a.id}/participants/",
        f"/groups/{group_a.id}/keywords/",
        f"/groups/{group_a.id}/export/",
        "/settings/",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.headers["Location"]


@pytest.mark.django_db
def test_idor_protection_user_cannot_view_unassigned_group(client, analista_user, group_a, group_b):
    """
    Proteção anti-IDOR: O analista só tem acesso ao Grupo A.
    Tentativas de acessar o Grupo B devem retornar 403 Forbidden.
    """
    analista_user.assigned_groups.add(group_a)
    client.force_login(analista_user)

    # Acesso permitido ao Grupo A
    resp_ok = client.get(f"/groups/{group_a.id}/participants/")
    assert resp_ok.status_code == 200

    # Tentativa de IDOR contra o Grupo B (não atribuído)
    resp_forbidden = client.get(f"/groups/{group_b.id}/participants/")
    assert resp_forbidden.status_code == 403

    resp_keywords_forbidden = client.get(f"/groups/{group_b.id}/keywords/")
    assert resp_keywords_forbidden.status_code == 403


@pytest.mark.django_db
def test_role_permissions_leitor_cannot_export(client, leitor_user, group_a):
    """Usuário com papel 'leitor' não pode realizar exportações em CSV (HTTP 403)."""
    leitor_user.assigned_groups.add(group_a)
    client.force_login(leitor_user)

    # Consegue visualizar a lista de participantes
    resp_view = client.get(f"/groups/{group_a.id}/participants/")
    assert resp_view.status_code == 200

    # Tentativa de exportação bloqueada
    resp_export = client.get(f"/groups/{group_a.id}/export/")
    assert resp_export.status_code == 403


@pytest.mark.django_db
def test_only_admin_can_access_settings(client, admin_user, analista_user, leitor_user):
    """Apenas administradores podem acessar a rota de configurações e auditoria."""
    client.force_login(leitor_user)
    assert client.get("/settings/").status_code == 403

    client.force_login(analista_user)
    assert client.get("/settings/").status_code == 403

    client.force_login(admin_user)
    assert client.get("/settings/").status_code == 200
