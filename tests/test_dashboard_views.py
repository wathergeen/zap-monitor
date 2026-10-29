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
def test_dashboard_group_selector_filters_groups_without_messages(client, admin_user):
    """
    Testa que a tela inicial (Dashboard):
    1. Lista apenas grupos que possuem mensagens.
    2. Ignora grupos vazios (0 mensagens).
    3. Permite selecionar outro grupo via parâmetro group_id.
    """
    now = timezone.now()
    client.force_login(admin_user)

    # Grupo 1 com mensagens
    g1 = Group.objects.create(jid="120363001@g.us", name="Grupo Ativo Um", monitored=True)
    m1 = Member.objects.create(phone="5511999990001", push_name="User 1")
    Message.objects.create(
        wa_id="MSG_DASH_G1", group=g1, member=m1, text="Olá no grupo 1", sent_at=now
    )

    # Grupo 2 com mensagens
    g2 = Group.objects.create(jid="120363002@g.us", name="Grupo Ativo Dois", monitored=True)
    m2 = Member.objects.create(phone="5511999990002", push_name="User 2")
    Message.objects.create(
        wa_id="MSG_DASH_G2", group=g2, member=m2, text="Olá no grupo 2", sent_at=now
    )

    # Grupo 3 VAZIO (sem nenhuma mensagem)
    g3 = Group.objects.create(jid="120363003@g.us", name="Grupo Vazio Sem Msg", monitored=True)

    # Requisição padrão: deve conter g1 e g2, mas NÃO g3
    resp = client.get("/")
    assert resp.status_code == 200
    accessible = list(resp.context["accessible_groups"])
    assert g1 in accessible
    assert g2 in accessible
    assert g3 not in accessible

    content = resp.content.decode("utf-8")
    assert "Grupo Ativo Um" in content
    assert "Grupo Ativo Dois" in content
    assert "Grupo Vazio Sem Msg" not in content

    # Seleção de outro grupo via query param group_id (g2)
    resp_g2 = client.get(f"/?group_id={g2.id}")
    assert resp_g2.status_code == 200
    assert resp_g2.context["selected_group"] == g2

    # Tentativa de selecionar grupo vazio (g3) não deve selecioná-lo
    resp_g3 = client.get(f"/?group_id={g3.id}")
    assert resp_g3.status_code == 200
    assert resp_g3.context["selected_group"] != g3


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


@pytest.mark.django_db
def test_group_participants_sorting(client, admin_user, test_group):
    """
    Testa ordenação da tabela de participantes por:
    - Quantidade de mensagens (desc e asc)
    - Data de entrada (desc e asc)
    - Papel no grupo (desc e asc)
    """
    import datetime

    client.force_login(admin_user)
    now = timezone.now()

    # Membro 1: 10 mensagens, entrou há 5 dias, Membro
    m1 = Member.objects.create(phone="5511911111111", push_name="Membro Dez Msgs")
    GroupMembership.objects.create(
        group=test_group,
        member=m1,
        role=GroupMembership.Role.MEMBER,
        joined_at=now - datetime.timedelta(days=5),
    )
    for i in range(10):
        Message.objects.create(
            wa_id=f"MSG_SORT_M1_{i}",
            group=test_group,
            member=m1,
            text=f"Msg {i}",
            sent_at=now - datetime.timedelta(hours=i),
        )

    # Membro 2: 2 mensagens, entrou há 10 dias, Admin
    m2 = Member.objects.create(phone="5511922222222", push_name="Admin Duas Msgs")
    GroupMembership.objects.create(
        group=test_group,
        member=m2,
        role=GroupMembership.Role.ADMIN,
        joined_at=now - datetime.timedelta(days=10),
    )
    for i in range(2):
        Message.objects.create(
            wa_id=f"MSG_SORT_M2_{i}",
            group=test_group,
            member=m2,
            text=f"Msg admin {i}",
            sent_at=now - datetime.timedelta(hours=i),
        )

    # Membro 3: 0 mensagens, entrou há 1 dia, Superadmin
    m3 = Member.objects.create(phone="5511933333333", push_name="Superadmin Zero Msgs")
    GroupMembership.objects.create(
        group=test_group,
        member=m3,
        role=GroupMembership.Role.SUPERADMIN,
        joined_at=now - datetime.timedelta(days=1),
    )

    url = f"/groups/{test_group.id}/participants/"

    # 1. Ordenação por mensagens desc
    resp = client.get(f"{url}?sort=messages&direction=desc")
    assert resp.status_code == 200
    page = resp.context["page_obj"].object_list
    assert page[0].member == m1
    assert page[1].member == m2
    assert page[2].member == m3

    # 2. Ordenação por mensagens asc
    resp = client.get(f"{url}?sort=messages&direction=asc")
    assert resp.status_code == 200
    page = resp.context["page_obj"].object_list
    assert page[0].member == m3
    assert page[1].member == m2
    assert page[2].member == m1

    # 3. Ordenação por data de entrada desc (mais recente primeiro)
    resp = client.get(f"{url}?sort=joined&direction=desc")
    assert resp.status_code == 200
    page = resp.context["page_obj"].object_list
    assert page[0].member == m3  # 1 dia atrás
    assert page[1].member == m1  # 5 dias atrás
    assert page[2].member == m2  # 10 dias atrás

    # 4. Ordenação por data de entrada asc (mais antigo primeiro)
    resp = client.get(f"{url}?sort=joined&direction=asc")
    assert resp.status_code == 200
    page = resp.context["page_obj"].object_list
    assert page[0].member == m2  # 10 dias atrás
    assert page[1].member == m1  # 5 dias atrás
    assert page[2].member == m3  # 1 dia atrás

    # 5. Ordenação por papel desc (superadmin primeiro, depois admin, depois member)
    resp = client.get(f"{url}?sort=role&direction=desc")
    assert resp.status_code == 200
    page = resp.context["page_obj"].object_list
    assert page[0].member == m3  # Superadmin
    assert page[1].member == m2  # Admin
    assert page[2].member == m1  # Member

    # 6. Ordenação por papel asc (membros primeiro)
    resp = client.get(f"{url}?sort=role&direction=asc")
    assert resp.status_code == 200
    page = resp.context["page_obj"].object_list
    assert page[0].member == m1  # Member
    assert page[1].member == m2  # Admin
    assert page[2].member == m3  # Superadmin

    # 7. Teste com parâmetro sort_by unificado (do select da tela)
    resp_select = client.get(f"{url}?sort_by=joined_desc")
    assert resp_select.status_code == 200
    assert resp_select.context["sort"] == "joined"
    assert resp_select.context["direction"] == "desc"
    assert resp_select.context["page_obj"].object_list[0].member == m3


@pytest.mark.django_db
def test_messages_presentation_and_removed_indicator(client, admin_user, test_group, test_member):
    """
    Testa a apresentação simplificada e compacta das mensagens,
    e a exibição do texto 'Removida' em vermelho ao lado do tipo 'Texto'.
    """
    client.force_login(admin_user)
    now = timezone.now()

    # Mensagem normal ativa
    Message.objects.create(
        wa_id="MSG_PRES_ACTIVE_001",
        group=test_group,
        member=test_member,
        type=Message.MessageType.TEXT,
        text="Conteúdo de mensagem normal ativa",
        sent_at=now,
    )

    # Mensagem apagada/removida
    Message.objects.create(
        wa_id="MSG_PRES_DELETED_002",
        group=test_group,
        member=test_member,
        type=Message.MessageType.TEXT,
        text="Conteúdo que foi removido pelo usuário",
        sent_at=now,
        deleted_at=now,
    )

    # 1. Página inicial (Dashboard)
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    content_home = resp_home.content.decode("utf-8")

    # Verifica tipo 'Texto'
    assert "Texto" in content_home
    # Verifica a mensagem 'Removida' em vermelho
    assert "Removida" in content_home
    assert "text-red-600" in content_home

    # 2. Página de perfil do participante
    profile_url = f"/groups/{test_group.id}/participants/{test_member.id}/"
    resp_prof = client.get(profile_url)
    assert resp_prof.status_code == 200
    content_prof = resp_prof.content.decode("utf-8")
    assert "Removida" in content_prof
    assert "text-red-600" in content_prof

    # 3. Filtro por status na linha do tempo do participante (HTMX)
    # Apenas ativas
    resp_act = client.get(f"{profile_url}?status_msg=active", HTTP_HX_REQUEST="true")
    content_act = resp_act.content.decode("utf-8")
    assert "Conteúdo de mensagem normal ativa" in content_act
    assert "Conteúdo que foi removido" not in content_act

    # Apenas removidas
    resp_del = client.get(f"{profile_url}?status_msg=deleted", HTTP_HX_REQUEST="true")
    content_del = resp_del.content.decode("utf-8")
    assert "Conteúdo que foi removido" in content_del
    assert "Removida" in content_del
    assert "Conteúdo de mensagem normal ativa" not in content_del
