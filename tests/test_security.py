import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.analytics.services import safe_highlight_terms, sanitize_csv_cell
from apps.audit.models import AuditLog
from apps.groups.models import Group, Member
from apps.messages_monitor.models import Message


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(email="admin_sec@test.com", password="StrongPassword123!")


@pytest.fixture
def group_sec(db):
    return Group.objects.create(jid="group_sec@g.us", name="Grupo Segurança", monitored=True)


@pytest.mark.django_db
def test_stored_xss_protection_in_messages(client, admin_user, group_sec):
    """
    Testa mensagem maliciosa com payload de XSS armazenado:
    Garante que o template do Django e os helpers de destaque escapem 100% o HTML.
    """
    member = Member.objects.create(phone="5511999993333", push_name="Hacker")
    malicious_text = "<script>alert('XSS')</script><img src=x onerror=alert(1)> <b>Negrito</b>"

    Message.objects.create(
        wa_id="XSS_MSG_001",
        group=group_sec,
        member=member,
        text=malicious_text,
        sent_at=timezone.now(),
    )

    client.force_login(admin_user)
    response = client.get(f"/groups/{group_sec.id}/participants/{member.id}/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    # Não deve conter a tag <script> desescapada
    assert "<script>alert('XSS')</script>" not in content
    # Deve conter a versão escapada
    assert (
        "&lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;" in content
        or "&lt;script&gt;" in content
    )


def test_safe_highlight_terms_neutralizes_xss():
    """Garante que a função de highlight em palavras-chave não introduza vulnerabilidade XSS."""
    malicious_text = "<script>alert(1)</script> termo_importante"
    result = safe_highlight_terms(malicious_text, ["termo_importante"])

    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert '<mark class="' in result
    assert "termo_importante</mark>" in result


def test_csv_injection_formula_sanitization():
    """Garante que células iniciadas com =, +, -, @ ou tabs sejam prefixadas com apóstrofo."""
    dangerous_inputs = [
        "=cmd|'/C calc'!A0",
        "+123456",
        "-5+5",
        "@SUM(A1:A10)",
        "\t=2+2",
    ]
    for inp in dangerous_inputs:
        sanitized = sanitize_csv_cell(inp)
        assert sanitized.startswith("'"), f"Falha na sanitização de {inp}: {sanitized}"

    # Entradas normais não devem ser alteradas
    assert sanitize_csv_cell("Texto normal") == "Texto normal"
    assert sanitize_csv_cell("12345") == "12345"


@pytest.mark.django_db
def test_security_headers_present(client, admin_user):
    """Verifica a presença dos cabeçalhos de segurança obrigatórios."""
    client.force_login(admin_user)
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, private"
    assert "Permissions-Policy" in response.headers
    assert response.headers.get("Cross-Origin-Opener-Policy") == "same-origin"


@pytest.mark.django_db
def test_audit_log_immutability():
    """Garante que registros de auditoria sejam estritamente append-only (sem update ou delete)."""
    log = AuditLog.objects.create(action=AuditLog.Action.LOGIN, target="Teste Imutabilidade")

    # Tentativa de edição deve lançar PermissionDenied
    with pytest.raises(PermissionDenied):
        log.target = "Editado"
        log.save()

    # Tentativa de exclusão direta deve lançar PermissionDenied
    with pytest.raises(PermissionDenied):
        log.delete()
