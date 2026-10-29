from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.groups.models import Group, Member
from apps.messages_monitor.models import Message
from apps.search.backends import ElasticsearchBackend, PostgresFTSBackend


@pytest.fixture
def search_group(db):
    return Group.objects.create(jid="search_grp@g.us", name="Grupo Busca", monitored=True)


@pytest.fixture
def search_member(db):
    return Member.objects.create(phone="5511999994444", push_name="Buscador")


@pytest.mark.django_db
def test_postgres_search_case_accents_and_terms(search_group, search_member):
    """
    Testa busca do PostgresFTSBackend para diferenciação de maiúsculas/minúsculas e acentuação.
    """
    backend = PostgresFTSBackend()

    Message.objects.create(
        wa_id="SEARCH_001",
        group=search_group,
        member=search_member,
        text="Relatório financeiro de inflação e preços de alimentos",
        sent_at=timezone.now(),
    )
    Message.objects.create(
        wa_id="SEARCH_002",
        group=search_group,
        member=search_member,
        text="Discussão sobre tecnologia e computadores quânticos",
        sent_at=timezone.now(),
    )

    # 1. Busca sem acento para palavra com acento
    res1 = backend.search("inflacao", group_id=search_group.id)
    assert res1.count() >= 1

    # 2. Busca case insensitive
    res2 = backend.search("INFLAÇÃO", group_id=search_group.id)
    assert res2.count() >= 1

    # 3. Busca por termo diferente
    res3 = backend.search("quanticos", group_id=search_group.id)
    assert res3.count() >= 1

    # 4. Termo inexistente
    res4 = backend.search("palavra_inexistente_xyz", group_id=search_group.id)
    assert res4.count() == 0


@pytest.mark.django_db
def test_elasticsearch_backend_contract_mocked(monkeypatch, search_group, search_member):
    """
    Testa contrato do ElasticsearchBackend com mock do cliente HTTP da biblioteca Elasticsearch.
    """
    import sys

    monkeypatch.setitem(sys.modules, "elasticsearch", MagicMock())

    Message.objects.create(
        wa_id="ES_MSG_001",
        group=search_group,
        member=search_member,
        text="Contrato de prestação de serviços e consultoria",
        sent_at=timezone.now(),
    )

    es_mock = MagicMock()
    es_mock.indices.exists.return_value = True
    es_mock.search.return_value = {"hits": {"hits": [{"_id": "ES_MSG_001"}]}}

    backend = ElasticsearchBackend()
    backend.es = es_mock

    results = backend.search("consultoria", group_id=search_group.id)
    assert results.count() == 1
    assert results.first().wa_id == "ES_MSG_001"
