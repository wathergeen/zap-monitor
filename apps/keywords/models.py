import unicodedata

from django.db import models

from apps.groups.models import Group, Member
from apps.messages_monitor.models import Message


def normalize_keyword_text(text: str) -> str:
    """
    Normalização canônica para correspondência de palavras-chave:
    1. Converte para minúsculas usando casefold() (compatível com Unicode).
    2. Remove acentos e caracteres diacríticos (unaccent via NFKD).
    3. Normaliza espaços múltiplos.
    """
    if not text:
        return ""
    # NFKD decompõe caracteres acentuados (ex: 'ç' -> 'c' + '¸', 'ã' -> 'a' + '~')
    nfkd = unicodedata.normalize("NFKD", text)
    # Remove marcas de combinação diacrítica (Mn = Mark, Nonspacing)
    no_accents = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return " ".join(no_accents.casefold().split())


class Keyword(models.Model):
    """
    Palavras-chave ou termos monitorados por grupo.
    term_normalized armazena o termo pré-processado para busca ultrarrápida.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="keywords", verbose_name="Grupo"
    )
    term = models.CharField("Termo / Palavra-chave", max_length=100)
    term_normalized = models.CharField(
        "Termo Normalizado", max_length=100, db_index=True, blank=True
    )
    active = models.BooleanField("Ativo", default=True, db_index=True)
    created_at = models.DateTimeField("Data de Cadastro", auto_now_add=True)

    class Meta:
        verbose_name = "Palavra-chave"
        verbose_name_plural = "Palavras-chave"
        unique_together = ("group", "term")
        ordering = ["term"]

    def __str__(self):
        return f"{self.term} ({self.group.name or self.group.jid})"

    def save(self, *args, **kwargs):
        self.term_normalized = normalize_keyword_text(self.term)
        super().save(*args, **kwargs)


class KeywordHit(models.Model):
    """
    Ocorrência de palavra-chave detectada durante a ingestão da mensagem.
    Transforma consultas analíticas pesadas em leituras simples de tabela indexada.
    """

    keyword = models.ForeignKey(
        Keyword, on_delete=models.CASCADE, related_name="hits", verbose_name="Palavra-chave"
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="keyword_hits", verbose_name="Mensagem"
    )
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="keyword_hits", verbose_name="Grupo"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="keyword_hits", verbose_name="Participante"
    )
    sent_at = models.DateTimeField("Data de Envio", db_index=True)

    class Meta:
        verbose_name = "Ocorrência de Palavra-chave"
        verbose_name_plural = "Ocorrências de Palavras-chave"
        unique_together = ("keyword", "message")
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["group", "keyword", "sent_at"], name="idx_hit_grp_kw_sent"),
            models.Index(fields=["member", "keyword"], name="idx_hit_mbr_kw"),
        ]

    def __str__(self):
        return f"Hit: '{self.keyword.term}' por {self.member} em {self.sent_at:%d/%m/%Y %H:%M}"
