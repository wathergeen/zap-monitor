import abc
import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import models
from django.db.models import QuerySet

from apps.messages_monitor.models import Message

logger = logging.getLogger("apps.search")


class SearchBackend(abc.ABC):
    """
    Interface abstrata para provedores de busca textual.
    Permite alternância transparente entre PostgreSQL FTS e Elasticsearch.
    """

    @abc.abstractmethod
    def search(
        self, query: str, group_id: Optional[int] = None, member_id: Optional[int] = None
    ) -> QuerySet:
        """Executa busca e retorna queryset de Message."""
        pass

    @abc.abstractmethod
    def index_message(self, message: Message) -> None:
        """Indexa uma única mensagem."""
        pass

    @abc.abstractmethod
    def reindex_all(self) -> int:
        """Reindexa todo o acervo de mensagens."""
        pass


class PostgresFTSBackend(SearchBackend):
    """
    Implementação padrão utilizando PostgreSQL Full Text Search com dicionário em português,
    índice GIN, unaccent e ranking por relevância.
    """

    def search(
        self, query: str, group_id: Optional[int] = None, member_id: Optional[int] = None
    ) -> QuerySet:
        from django.contrib.postgres.search import SearchQuery, SearchRank
        from django.db import connection

        qs = Message.objects.all().select_related("group", "member")
        if group_id:
            qs = qs.filter(group_id=group_id)
        if member_id:
            qs = qs.filter(member_id=member_id)

        clean_term = query.strip()
        if not clean_term:
            return qs

        if connection.vendor == "postgresql":
            # Busca FTS em português com ranking
            search_query = SearchQuery(clean_term, config="portuguese")
            qs = (
                qs.filter(search_vector=search_query)
                .annotate(rank=SearchRank("search_vector", search_query))
                .order_by("-rank", "-sent_at")
            )
            # Se não houver correspondência exata FTS, tentar busca com unaccent/icontains
            if not qs.exists():
                qs = Message.objects.filter(text__icontains=clean_term).select_related(
                    "group", "member"
                )
                if group_id:
                    qs = qs.filter(group_id=group_id)
                if member_id:
                    qs = qs.filter(member_id=member_id)
                qs = qs.order_by("-sent_at")
        else:
            # Fallback para SQLite/testes offline com suporte a unaccent em memória
            direct_qs = qs.filter(text__icontains=clean_term).order_by("-sent_at")
            if direct_qs.exists():
                return direct_qs

            import unicodedata

            clean_normalized = "".join(
                c
                for c in unicodedata.normalize("NFD", clean_term)
                if unicodedata.category(c) != "Mn"
            ).lower()
            matching_ids = []
            for m in qs:
                text_normalized = "".join(
                    c
                    for c in unicodedata.normalize("NFD", m.text)
                    if unicodedata.category(c) != "Mn"
                ).lower()
                if clean_normalized in text_normalized:
                    matching_ids.append(m.id)
            return qs.filter(id__in=matching_ids).order_by("-sent_at")

        return qs

    def index_message(self, message: Message) -> None:
        message.update_search_vector()

    def reindex_all(self) -> int:
        from django.contrib.postgres.search import SearchVector
        from django.db import connection

        if connection.vendor == "postgresql":
            count = Message.objects.filter(text__gt="").update(
                search_vector=SearchVector("text", config="portuguese")
            )
            return count
        return Message.objects.count()


class ElasticsearchBackend(SearchBackend):
    """
    Implementação opcional para busca de alto desempenho via Elasticsearch 8.
    Desligado por padrão, ativado via SEARCH_BACKEND=elasticsearch.
    """

    INDEX_NAME = "zapbot_messages"

    def __init__(self):
        try:
            from elasticsearch import Elasticsearch
        except ImportError:
            raise NotImplementedError(
                "Elasticsearch está fora do escopo na Versão 2 (stack enxuta sem Elasticsearch). "
                "Utilize PostgreSQL FTS (padrão) ou consulte docs/escalabilidade.md para planejamento futuro."
            ) from None

        self.es = Elasticsearch(
            hosts=[getattr(settings, "ELASTICSEARCH_URL", "http://elasticsearch:9200")],
            basic_auth=(
                getattr(settings, "ELASTICSEARCH_USER", "elastic"),
                getattr(settings, "ELASTICSEARCH_PASSWORD", ""),
            ),
            request_timeout=5.0,
        )

    def _ensure_index(self):
        try:
            if not self.es.indices.exists(index=self.INDEX_NAME):
                self.es.indices.create(
                    index=self.INDEX_NAME,
                    body={
                        "settings": {
                            "analysis": {
                                "analyzer": {
                                    "pt_analyzer": {
                                        "tokenizer": "standard",
                                        "filter": [
                                            "lowercase",
                                            "asciifolding",
                                            "portuguese_stemmer",
                                        ],
                                    }
                                },
                                "filter": {
                                    "portuguese_stemmer": {
                                        "type": "stemmer",
                                        "language": "portuguese",
                                    }
                                },
                            }
                        },
                        "mappings": {
                            "properties": {
                                "wa_id": {"type": "keyword"},
                                "group_id": {"type": "integer"},
                                "member_id": {"type": "integer"},
                                "text": {"type": "text", "analyzer": "pt_analyzer"},
                                "sent_at": {"type": "date"},
                            }
                        },
                    },
                )
        except Exception as e:
            logger.warning(f"Erro ao verificar/criar índice no Elasticsearch: {e}")

    def search(
        self, query: str, group_id: Optional[int] = None, member_id: Optional[int] = None
    ) -> QuerySet:
        self._ensure_index()
        clean_term = query.strip()
        if not clean_term:
            qs = Message.objects.all().select_related("group", "member")
            if group_id:
                qs = qs.filter(group_id=group_id)
            if member_id:
                qs = qs.filter(member_id=member_id)
            return qs

        must_clauses: List[Dict[str, Any]] = [
            {"match": {"text": {"query": clean_term, "fuzziness": "AUTO"}}}
        ]
        if group_id:
            must_clauses.append({"term": {"group_id": group_id}})
        if member_id:
            must_clauses.append({"term": {"member_id": member_id}})

        try:
            response = self.es.search(
                index=self.INDEX_NAME,
                body={"query": {"bool": {"must": must_clauses}}, "size": 100},
            )
            hits = response.get("hits", {}).get("hits", [])
            wa_ids = [hit["_id"] for hit in hits]
            if not wa_ids:
                return Message.objects.none()

            # Preserva ordem dos resultados do Elasticsearch
            preserved_order = models.Case(
                *[models.When(wa_id=wa_id, then=pos) for pos, wa_id in enumerate(wa_ids)]
            )
            return (
                Message.objects.filter(wa_id__in=wa_ids)
                .select_related("group", "member")
                .order_by(preserved_order)
            )
        except Exception as e:
            logger.error(f"Erro ao buscar no Elasticsearch, acionando fallback Postgres: {e}")
            return PostgresFTSBackend().search(query, group_id, member_id)

    def index_message(self, message: Message) -> None:
        self._ensure_index()
        if not message.text:
            return
        try:
            self.es.index(
                index=self.INDEX_NAME,
                id=message.wa_id,
                document={
                    "wa_id": message.wa_id,
                    "group_id": message.group_id,
                    "member_id": message.member_id,
                    "text": message.text,
                    "sent_at": message.sent_at.isoformat(),
                },
            )
        except Exception as e:
            logger.warning(f"Falha ao indexar mensagem #{message.pk} no Elasticsearch: {e}")

    def reindex_all(self) -> int:
        self._ensure_index()
        count = 0
        for msg in Message.objects.filter(text__gt="").iterator(chunk_size=500):
            self.index_message(msg)
            count += 1
        return count


def get_search_backend() -> SearchBackend:
    """Factory para instanciar o backend de busca configurado."""
    backend_choice = getattr(settings, "SEARCH_BACKEND", "postgres").lower()
    if backend_choice == "elasticsearch":
        try:
            return ElasticsearchBackend()
        except Exception as e:
            logger.error(
                f"Não foi possível inicializar ElasticsearchBackend: {e}. Usando Postgres FTS."
            )
            return PostgresFTSBackend()
    return PostgresFTSBackend()
