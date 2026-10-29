import logging
import re
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .models import EvolutionInstance, Group, GroupMembership, Member

logger = logging.getLogger("apps.groups")


def clean_phone_number(raw_identifier: str) -> Optional[str]:
    """
    Extrai o número de telefone limpo (ex: 5511999998888) a partir de jids como:
    - 5511999998888@s.whatsapp.net
    - 5511999998888:1@s.whatsapp.net
    - +55 11 99999-8888
    """
    if not raw_identifier or "@lid" in raw_identifier:
        return None

    # Remover sufixos de servidor
    ident = raw_identifier.split("@")[0].split(":")[0]
    digits = re.sub(r"\D", "", ident)
    return digits if len(digits) >= 8 else None


def extract_lid(raw_identifier: str) -> Optional[str]:
    """
    Extrai o identificador LID caso presente (ex: 123456789@lid).
    """
    if not raw_identifier:
        return None
    if "@lid" in raw_identifier:
        return raw_identifier.split("@")[0].split(":")[0]
    return None


class MemberResolutionService:
    """
    Serviço de resolução e unificação de participantes.
    Evita duplicação quando a Evolution envia hora o LID (@lid) hora o número (@s.whatsapp.net).
    """

    @classmethod
    @transaction.atomic
    def get_or_create_member(
        cls,
        identifier: str,
        push_name: str = "",
        lid: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Tuple[Member, bool]:
        extracted_phone = clean_phone_number(phone) if phone else clean_phone_number(identifier)
        extracted_lid = lid or extract_lid(identifier)

        member = None
        created = False

        # 1. Tentar buscar por telefone se disponível
        if extracted_phone:
            member = Member.objects.filter(phone=extracted_phone).first()

        # 2. Se não encontrou por telefone, tentar por LID
        if not member and extracted_lid:
            member = Member.objects.filter(lid=extracted_lid).first()

        # 2.1 Se não tem identifier com @lid/@s.whatsapp.net mas push_name é numérico (LID na Evolution)
        if (
            not member
            and not extracted_phone
            and not extracted_lid
            and push_name
            and push_name.strip().isdigit()
        ):
            extracted_lid = push_name.strip()
            member = Member.objects.filter(lid=extracted_lid).first()

        # 3. Se ainda não encontrou, criar novo membro (apenas se tiver pelo menos telefone ou LID)
        if not member:
            if not extracted_phone and not extracted_lid:
                # Não cria membro fantasma sem identificador válido
                logger.warning(
                    f"Tentativa de criar membro sem identificador rejeitada: {identifier}, push_name={push_name}"
                )
                return None, False

            member = Member.objects.create(
                phone=extracted_phone,
                lid=extracted_lid,
                push_name=push_name.strip() if push_name.strip() != extracted_lid else "",
                first_seen_at=timezone.now(),
            )
            created = True
        else:
            # Atualiza informações que faltavam para unificação
            updated = False
            if member.phone and ("@" in member.phone or ":" in member.phone):
                cleaned = clean_phone_number(member.phone)
                if (
                    cleaned
                    and not Member.objects.filter(phone=cleaned).exclude(pk=member.pk).exists()
                ):
                    member.phone = cleaned
                    updated = True
            if extracted_phone and not member.phone:
                # Verificar se o telefone não pertence a outro registro para evitar colisão
                if not Member.objects.filter(phone=extracted_phone).exclude(pk=member.pk).exists():
                    member.phone = extracted_phone
                    updated = True
            if extracted_lid and not member.lid:
                if not Member.objects.filter(lid=extracted_lid).exclude(pk=member.pk).exists():
                    member.lid = extracted_lid
                    updated = True
            if push_name and push_name.strip() and not member.push_name:
                member.push_name = push_name.strip()
                updated = True

            if updated:
                member.save()

        return member, created

    @classmethod
    @transaction.atomic
    def record_membership(
        cls,
        group: Group,
        member: Member,
        role: str = GroupMembership.Role.MEMBER,
        is_leaving: bool = False,
    ) -> GroupMembership:
        """
        Atualiza o histórico de associação ao grupo.
        Se is_leaving=True, registra a saída (left_at).
        Se is_leaving=False, garante presença ativa (left_at=None).
        """
        now = timezone.now()
        active_membership = GroupMembership.objects.filter(
            group=group,
            member=member,
            left_at__isnull=True,
        ).first()

        if is_leaving:
            if active_membership:
                active_membership.left_at = now
                active_membership.save(update_fields=["left_at"])
                return active_membership
            else:
                # Cria registro já como tendo saído
                return GroupMembership.objects.create(
                    group=group,
                    member=member,
                    role=role,
                    joined_at=now,
                    left_at=now,
                )
        else:
            if active_membership:
                if active_membership.role != role:
                    active_membership.role = role
                    active_membership.save(update_fields=["role"])
                return active_membership
            else:
                # Membro acabou de entrar ou reentrar
                return GroupMembership.objects.create(
                    group=group,
                    member=member,
                    role=role,
                    joined_at=now,
                    left_at=None,
                )


class GroupSyncService:
    """
    Serviço de descoberta e sincronização de grupos conhecidos pela Evolution API v2.
    Importa e atualiza grupos no banco local, associando-os à instância Evolution.
    """

    @classmethod
    def sync_instance_groups(cls, instance: Optional[EvolutionInstance] = None) -> list[Group]:
        """
        Busca todos os grupos da instância na Evolution API e cadastra/atualiza no banco.
        Retorna a lista de instâncias Group sincronizadas.
        """
        from .evolution_client import EvolutionReadOnlyClient

        client = EvolutionReadOnlyClient(instance=instance)
        raw_groups = client.fetch_all_groups()
        synced_groups = []

        for item in raw_groups:
            jid = str(item.get("id") or item.get("jid") or "").strip()
            if not jid.endswith("@g.us"):
                continue

            name = str(item.get("subject") or item.get("name") or "").strip()
            group, _ = Group.objects.get_or_create(
                jid=jid,
                defaults={
                    "name": name,
                    "instance": instance,
                    "monitored": True,
                },
            )
            updates = []
            if name and group.name != name:
                group.name = name
                updates.append("name")
            if instance and group.instance_id != instance.id:
                group.instance = instance
                updates.append("instance")
            if updates:
                group.save(update_fields=updates)

            # Sincroniza participantes do grupo
            try:
                from .tasks import sync_group_participants

                sync_group_participants(group_id=group.id)
            except Exception as e:
                logger.warning(f"Erro ao sincronizar participantes do grupo {jid}: {e}")

            synced_groups.append(group)

        return synced_groups

    @classmethod
    def sync_all_instances_groups(cls) -> list[Group]:
        """
        Sincroniza os grupos de todas as instâncias ativas cadastradas.
        """
        all_synced = []
        for inst in EvolutionInstance.objects.filter(active=True):
            try:
                groups = cls.sync_instance_groups(instance=inst)
                all_synced.extend(groups)
            except Exception as e:
                logger.warning(f"Erro ao sincronizar grupos da instância {inst.name}: {e}")
        return all_synced
