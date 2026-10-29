from django.core.management.base import BaseCommand

from apps.search.backends import get_search_backend


class Command(BaseCommand):
    help = "Reindexa todas as mensagens no backend de busca configurado (Postgres FTS ou Elasticsearch)."

    def handle(self, *args, **options):
        backend = get_search_backend()
        self.stdout.write(f"Iniciando reindexação com backend: {backend.__class__.__name__}...")
        total = backend.reindex_all()
        self.stdout.write(
            self.style.SUCCESS(
                f"Reindexação concluída com sucesso! Total de mensagens indexadas: {total}"
            )
        )
