import pytest
from django.test import Client

from apps.accounts.models import RecoveryCode, User, UserInvitation


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        email="testuser@example.com",
        password="ValidPassword123!",
        full_name="Usuário de Teste",
        role=User.Role.ANALISTA,
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="admin2@example.com",
        password="ValidPassword123!",
        full_name="Admin Teste",
    )


@pytest.mark.django_db
def test_login_success_and_logout(client, regular_user):
    """Testa login com credenciais válidas e encerramento de sessão."""
    resp = client.post(
        "/accounts/login/",
        {"email": "testuser@example.com", "password": "ValidPassword123!"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"

    # Logout
    resp_logout = client.get("/accounts/logout/")
    assert resp_logout.status_code == 302
    assert "/accounts/login/" in resp_logout.headers["Location"]


@pytest.mark.django_db
def test_login_invalid_credentials_generic_message(client, regular_user):
    """Garante mensagem de erro genérica em caso de falha de autenticação."""
    resp = client.post(
        "/accounts/login/",
        {"email": "testuser@example.com", "password": "WrongPassword999!"},
    )
    assert resp.status_code == 200
    assert "Credenciais inválidas" in resp.content.decode("utf-8")


@pytest.mark.django_db
def test_password_reset_request_identical_response(client, regular_user):
    """Garante resposta indistinguível para e-mail existente e inexistente."""
    resp1 = client.post(
        "/accounts/password-reset/",
        {"email": "testuser@example.com"},
    )
    assert resp1.status_code == 302

    resp2 = client.post(
        "/accounts/password-reset/",
        {"email": "inexistente@example.com"},
    )
    assert resp2.status_code == 302


@pytest.mark.django_db
def test_user_invitation_lifecycle(client, admin_user):
    """Testa criação, validação e aceitação de convite de uso único."""
    inv = UserInvitation.create_invitation(
        email="convidado@example.com",
        role=User.Role.ANALISTA,
        invited_by=admin_user,
        valid_days=3,
    )
    assert inv.is_valid is True

    # Acessa tela de aceite
    resp_get = client.get(f"/accounts/invitation/{inv.token}/")
    assert resp_get.status_code == 200

    # Submete nova senha
    resp_post = client.post(
        f"/accounts/invitation/{inv.token}/",
        {
            "full_name": "Convidado Aceito",
            "password": "NewStrongPassword123!",
            "password_confirm": "NewStrongPassword123!",
        },
    )
    assert resp_post.status_code == 302
    assert resp_post.headers["Location"] == "/"

    inv.refresh_from_db()
    assert inv.used_at is not None
    assert inv.is_valid is False

    # Usuário foi criado e ativado
    new_user = User.objects.get(email="convidado@example.com")
    assert new_user.full_name == "Convidado Aceito"
    assert new_user.role == User.Role.ANALISTA
    assert new_user.check_password("NewStrongPassword123!") is True


@pytest.mark.django_db
def test_recovery_codes_generation_and_usage(regular_user):
    """Testa geração e validação de uso único de códigos de backup 2FA."""
    codes = RecoveryCode.generate_codes(regular_user, count=4)
    assert len(codes) == 4

    first_code = codes[0]
    # Uso inicial válido
    assert RecoveryCode.verify_and_use_code(regular_user, first_code) is True
    # Reutilização proibida (uso único)
    assert RecoveryCode.verify_and_use_code(regular_user, first_code) is False
    # Código inválido
    assert RecoveryCode.verify_and_use_code(regular_user, "codigo-falso-123") is False


@pytest.mark.django_db
def test_totp_setup_view(client, regular_user):
    """Testa carregamento da view de setup de 2FA."""
    client.force_login(regular_user)
    resp = client.get("/accounts/2fa/setup/")
    assert resp.status_code == 200
    assert "Configurar Autenticador TOTP" in resp.content.decode("utf-8")
