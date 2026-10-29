import logging
from typing import Any, Dict, List

import httpx
from django.conf import settings

logger = logging.getLogger("apps.groups")


class EvolutionClientError(Exception):
    pass


class EvolutionReadOnlyClient:
    """
    Cliente HTTP ESTRITAMENTE SOMENTE LEITURA para comunicação com a Evolution API v2.
    Proibido qualquer envio de mensagens, áudios ou mídias.
    Utiliza timeouts rigorosos e cabeçalhos de autenticação seguros.
    """

    def __init__(self, instance=None):
        self.db_instance = instance
        if self.db_instance is None:
            try:
                from .models import EvolutionInstance

                self.db_instance = EvolutionInstance.objects.filter(active=True).first()
            except Exception:
                self.db_instance = None

        if self.db_instance:
            self.base_url = (self.db_instance.api_url or "").rstrip("/")
            self.api_key = self.db_instance.api_key or ""
            self.instance = self.db_instance.instance_name or ""
        else:
            self.base_url = getattr(settings, "EVOLUTION_API_URL", "").rstrip("/")
            self.api_key = getattr(settings, "EVOLUTION_API_KEY", "")
            self.instance = getattr(settings, "EVOLUTION_INSTANCE_NAME", "")

        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["apikey"] = self.api_key
        return headers

    def get_connection_state(self) -> Dict[str, Any]:
        """Consulta o estado da conexão do WhatsApp na Evolution API."""
        if not self.base_url or not self.instance:
            return {"state": "not_configured", "error": "URL ou nome da instância não configurados"}

        url = f"{self.base_url}/instance/connectionState/{self.instance}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                if response.status_code == 200:
                    data = response.json()
                    state = (
                        data.get("state") or data.get("instance", {}).get("state") or "connected"
                    )
                    if self.db_instance:
                        from django.utils import timezone

                        self.db_instance.last_state = state
                        self.db_instance.last_checked_at = timezone.now()
                        self.db_instance.save(update_fields=["last_state", "last_checked_at"])
                    return data

                state = "disconnected"
                if self.db_instance:
                    from django.utils import timezone

                    self.db_instance.last_state = state
                    self.db_instance.last_checked_at = timezone.now()
                    self.db_instance.save(update_fields=["last_state", "last_checked_at"])
                return {"state": state, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            logger.warning(f"Erro ao verificar conexão com Evolution: {e}")
            if self.db_instance:
                from django.utils import timezone

                self.db_instance.last_state = "unavailable"
                self.db_instance.last_checked_at = timezone.now()
                self.db_instance.save(update_fields=["last_state", "last_checked_at"])
            return {"state": "unavailable", "error": str(e)}

    def fetch_all_groups(self) -> List[Dict[str, Any]]:
        """Lista todos os grupos conhecidos pela instância."""
        if not self.base_url or not self.instance:
            logger.warning("Instância Evolution não configurada.")
            return []

        url = f"{self.base_url}/group/fetchAllGroups/{self.instance}?getParticipants=false"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, list) else []
                logger.error(f"Falha ao listar grupos: HTTP {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Erro de conexão ao buscar grupos: {e}")
            return []

    def get_group_participants(self, group_jid: str) -> List[Dict[str, Any]]:
        """
        Obtém a lista atual de participantes de um grupo específico.
        """
        if not self.base_url or not self.instance:
            logger.warning(
                f"Instância Evolution não configurada para obter participantes de {group_jid}."
            )
            return []

        url = f"{self.base_url}/group/participants/{self.instance}?groupJid={group_jid}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                if response.status_code == 200:
                    data = response.json()
                    # A Evolution API v2 retorna a lista de participantes direta ou dentro de { participants: [...] }
                    if isinstance(data, dict):
                        return data.get("participants", [])
                    elif isinstance(data, list):
                        return data
                logger.warning(
                    f"Não foi possível obter participantes de {group_jid}: HTTP {response.status_code}"
                )
                return []
        except Exception as e:
            logger.error(f"Exceção ao obter participantes do grupo {group_jid}: {e}")
            return []

    def find_messages(self, group_jid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Busca as mensagens mais recentes de um grupo na Evolution API (somente leitura).
        """
        if not self.base_url or not self.instance:
            logger.warning(
                f"Instância Evolution não configurada para buscar mensagens de {group_jid}."
            )
            return []

        url = f"{self.base_url}/chat/findMessages/{self.instance}"
        payload = {
            "where": {"key": {"remoteJid": group_jid}},
            "limit": limit,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                if response.status_code == 200:
                    data = response.json()
                    return data.get("messages", {}).get("records", [])
                logger.warning(
                    f"Falha ao buscar mensagens de {group_jid}: HTTP {response.status_code}"
                )
                return []
        except Exception as e:
            logger.error(f"Exceção ao buscar mensagens do grupo {group_jid}: {e}")
            return []
