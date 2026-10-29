import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.groups.models import Group, GroupMembership, Member
from apps.keywords.models import Keyword
from apps.messages_monitor.models import Message


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="superadmin@example.com",
        password="SuperPassword123!",
    )


@pytest.fixture
def test_group(db):
    return Group.objects.create(
        jid="120363111111111111@g.us",
        name="Grupo Teste Painel",
        monitored=True,
    )


@pytest.fixture
def test_member(db, test_group):
    member = Member.objects.create(
        phone="5511988887777",
        push_name="Participante Teste",
    )
    GroupMembership.objects.create(
        group=test_group,
        member=member,
        role=GroupMembership.Role.MEMBER,
    )
    return member


@pytest.mark.django_db
def test_dashboard_home_view(client, admin_user, test_group):
    client.force_login(admin_user)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Visão Geral do Grupo" in resp.content.decode("utf-8")


@pytest.mark.django_db
def test_groups_list_and_toggle_monitored(client, admin_user, test_group):
    client.force_login(admin_user)
    resp = client.get("/groups/")
    assert resp.status_code == 200
    assert test_group.name in resp.content.decode("utf-8")

    # Toggle monitored
    resp_toggle = client.get(f"/groups/{test_group.id}/toggle-monitored/")
    assert resp_toggle.status_code == 302
    test_group.refresh_from_db()
    assert test_group.monitored is False


@pytest.mark.django_db
def test_group_participants_htmx_search(client, admin_user, test_group, test_member):
    client.force_login(admin_user)
    # Requisição HTMX com query
    resp = client.get(
        f"/groups/{test_group.id}/participants/?q=Participante",
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    assert "Participante Teste" in resp.content.decode("utf-8")


@pytest.mark.django_db
def test_member_profile_view(client, admin_user, test_group, test_member):
    client.force_login(admin_user)
    Message.objects.create(
        wa_id="MSG_PROFILE_01",
        group=test_group,
        member=test_member,
        text="Mensagem para o perfil",
        sent_at=timezone.now(),
    )

    resp = client.get(f"/groups/{test_group.id}/participants/{test_member.id}/")
    assert resp.status_code == 200
    assert "Perfil do Participante" in resp.content.decode("utf-8")

    # HTMX filter on timeline
    resp_htmx = client.get(
        f"/groups/{test_group.id}/participants/{test_member.id}/?term=perfil",
        HTTP_HX_REQUEST="true",
    )
    assert resp_htmx.status_code == 200
    assert "Mensagem para o perfil" in resp_htmx.content.decode("utf-8")


@pytest.mark.django_db
def test_reveal_phone_audited(client, admin_user, test_member):
    client.force_login(admin_user)

    # 1. Teste via POST
    resp = client.post(f"/members/{test_member.id}/reveal-phone/")
    assert resp.status_code == 200
    assert "5511988887777" in resp.content.decode("utf-8")

    # 2. Teste via GET (usado pelo HTMX)
    resp_get = client.get(f"/members/{test_member.id}/reveal-phone/")
    assert resp_get.status_code == 200
    assert "5511988887777" in resp_get.content.decode("utf-8")
    assert "+55 (11) 98888-7777" in resp_get.content.decode("utf-8")


@pytest.mark.django_db
def test_reveal_phone_unauthorized(client, test_member):
    # Não autenticado -> Redireciona para login
    resp = client.get(f"/members/{test_member.id}/reveal-phone/")
    assert resp.status_code == 302


def test_member_phone_formatting():
    from apps.groups.models import Member

    m1 = Member(phone="5563992231596")
    assert m1.clean_phone == "5563992231596"
    assert m1.formatted_phone == "+55 (63) 99223-1596"
    assert m1.masked_phone == "+55 (63) 9****-1596"

    m2 = Member(phone="551187654321")
    assert m2.clean_phone == "551187654321"
    assert m2.formatted_phone == "+55 (11) 8765-4321"

    m3 = Member(phone="11999998888")
    assert m3.clean_phone == "11999998888"
    assert m3.formatted_phone == "(11) 99999-8888"

    m3_8 = Member(phone="1188887777")
    assert m3_8.clean_phone == "1188887777"
    assert m3_8.formatted_phone == "(11) 8888-7777"

    m4 = Member(phone=None, lid="1234567890123456")
    assert m4.clean_phone is None
    assert m4.formatted_phone == "LID:1234567890123456"
    assert m4.masked_phone == "LID:1234...3456"

    m5 = Member(phone=None, lid=None)
    assert m5.clean_phone is None
    assert m5.formatted_phone == "Sem telefone"
    assert m5.masked_phone == "Sem telefone"

    m6 = Member(phone="5511988887777@s.whatsapp.net")
    assert m6.clean_phone == "5511988887777"
    assert m6.formatted_phone == "+55 (11) 98888-7777"


@pytest.mark.django_db
def test_keywords_crud_and_citations(client, admin_user, test_group, test_member):
    client.force_login(admin_user)

    # 1. Adicionar palavra-chave
    resp_add = client.post(
        f"/groups/{test_group.id}/keywords/",
        {"action": "add", "term": "promocao"},
    )
    assert resp_add.status_code == 302
    assert Keyword.objects.filter(group=test_group, term="promocao").exists()

    kw = Keyword.objects.get(group=test_group, term="promocao")

    # 2. Toggle active
    resp_toggle = client.post(
        f"/groups/{test_group.id}/keywords/",
        {"action": "toggle", "keyword_id": kw.id},
    )
    assert resp_toggle.status_code == 302
    kw.refresh_from_db()
    assert kw.active is False

    # 3. Criar mensagem com a palavra-chave e consultar relatório
    Message.objects.create(
        wa_id="MSG_KW_01",
        group=test_group,
        member=test_member,
        text="Temos uma promocao especial hoje!",
        sent_at=timezone.now(),
    )

    resp_report = client.get(f"/groups/{test_group.id}/keywords/?term=promocao")
    assert resp_report.status_code == 200
    assert "promocao" in resp_report.content.decode("utf-8")

    # 4. Exportar relatório de palavras-chave em CSV
    resp_csv = client.get(f"/groups/{test_group.id}/export/?type=keywords&term=promocao")
    assert resp_csv.status_code == 200
    assert resp_csv.headers["Content-Type"].startswith("text/csv")
    assert "MSG_KW_01" in resp_csv.content.decode("utf-8")

    # 5. Remover palavra-chave
    resp_del = client.post(
        f"/groups/{test_group.id}/keywords/",
        {"action": "delete", "keyword_id": kw.id},
    )
    assert resp_del.status_code == 302
    assert not Keyword.objects.filter(group=test_group, term="promocao").exists()


@pytest.mark.django_db
def test_admin_settings_view_actions(client, admin_user, test_group):
    client.force_login(admin_user)

    # Criação de convite via settings
    resp_invite = client.post(
        "/settings/",
        {"form_action": "invite", "email": "novo@empresa.com", "role": "leitor"},
    )
    assert resp_invite.status_code == 302

    # Renderização da página de configurações e verificação do modal de permissões
    resp_get = client.get("/settings/")
    assert resp_get.status_code == 200
    assert "openUserPermsModal" in resp_get.content.decode("utf-8")
    assert "Gerenciar Permissões de Acesso" in resp_get.content.decode("utf-8")

    # Atualização de usuário
    analista = User.objects.create_user(email="analista2@empresa.com", password="Password12345!")
    resp_update = client.post(
        "/settings/",
        {
            "form_action": "update_user",
            "user_id": analista.id,
            "role": "analista",
            "groups": [test_group.id],
        },
    )
    assert resp_update.status_code == 302
    analista.refresh_from_db()
    assert analista.role == User.Role.ANALISTA
    assert analista.assigned_groups.filter(id=test_group.id).exists()
