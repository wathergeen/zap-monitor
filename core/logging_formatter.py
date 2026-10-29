import json
import logging
import re
from datetime import datetime, timezone

# Padrão para detecção e mascaramento de números de telefone em strings de log
PHONE_PATTERN = re.compile(r"(\+?\d{2}\s*\(?\d{2}\)?\s*)(\d{4,5})[-.\s]?(\d{4})")


def mask_phone_match(match: re.Match) -> str:
    prefix = match.group(1)
    middle = match.group(2)
    suffix = match.group(3)
    masked_middle = middle[0] + ("*" * (len(middle) - 1))
    return f"{prefix}{masked_middle}-{suffix}"


def mask_sensitive_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    return PHONE_PATTERN.sub(mask_phone_match, text)


class SafeJsonFormatter(logging.Formatter):
    """
    Formatador de logs estruturado em JSON:
    - Mascara números de telefone
    - Remove tokens, senhas e chaves de API
    - Não inclui conteúdo de mensagens nas saídas padrão
    """

    SENSITIVE_KEYS = {"password", "secret", "token", "key", "authorization", "cookie"}

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_sensitive_text(record.getMessage()),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Tratar dados extras com sanitização
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            clean_extra = {}
            for k, v in record.extra.items():
                if any(sens in k.lower() for sens in self.SENSITIVE_KEYS):
                    clean_extra[k] = "[REDACTED]"
                elif isinstance(v, str):
                    clean_extra[k] = mask_sensitive_text(v)
                else:
                    clean_extra[k] = v
            log_data["extra"] = clean_extra

        return json.dumps(log_data, ensure_ascii=False)
