import logging

from django.db import connection
from django.http import JsonResponse

from apps.groups.evolution_client import EvolutionReadOnlyClient

logger = logging.getLogger("apps.health")


def health_check_view(request):
    """
    Endpoint de monitoramento de integridade: GET /healthz
    Verifica conexões fundamentais sem expor credenciais, versões internas ou detalhes sensíveis.
    Versão 2: sem Redis, valida PostgreSQL (fonte da verdade e broker de tarefas Procrastinate).
    """
    health_status = {
        "status": "healthy",
        "database": "unknown",
        "task_queue": "unknown",
        "evolution_api": "unknown",
    }
    is_healthy = True

    # 1. Checagem do Banco de Dados PostgreSQL
    try:
        connection.ensure_connection()
        health_status["database"] = "ok"
        health_status["task_queue"] = "ok"  # Fila baseada em PostgreSQL (Procrastinate)
    except Exception as e:
        logger.error(f"Healthcheck falhou no banco de dados: {e}")
        health_status["database"] = "error"
        health_status["task_queue"] = "error"
        is_healthy = False

    # 2. Checagem da Evolution API (informativa para multi-bot)
    try:
        from apps.groups.models import EvolutionInstance

        active_instances = EvolutionInstance.objects.filter(active=True)
        if not active_instances.exists():
            health_status["evolution_api"] = "no_instances_configured"
        else:
            first_inst = active_instances.first()
            evo_client = EvolutionReadOnlyClient(instance=first_inst)
            evo_state = evo_client.get_connection_state()
            state = evo_state.get("state") or evo_state.get("instance", {}).get("state")
            health_status["evolution_api"] = state or "reachable"
    except Exception as e:
        logger.warning(f"Healthcheck Evolution API: {e}")
        health_status["evolution_api"] = "unreachable"

    status_code = 200 if is_healthy else 503
    return JsonResponse(health_status, status=status_code)
