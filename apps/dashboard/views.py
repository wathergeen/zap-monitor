import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserInvitation
from apps.accounts.views import admin_required, can_export_required
from apps.analytics.services import ExportService, GroupMetricsService, safe_highlight_terms
from apps.audit.models import AuditLog
from apps.groups.evolution_client import EvolutionReadOnlyClient
from apps.groups.models import EvolutionInstance, Group, GroupMembership, Member
from apps.keywords.models import Keyword, KeywordHit
from apps.keywords.tasks import backfill_keyword_hits
from apps.messages_monitor.models import Message
from apps.search.backends import get_search_backend

logger = logging.getLogger(__name__)


def get_user_accessible_groups(user: User, only_with_messages: bool = False):
    """Retorna os grupos que o usuário tem permissão para visualizar (Anti-IDOR)."""
    if user.is_admin:
        qs = Group.objects.all().order_by("-monitored", "name")
    else:
        qs = user.assigned_groups.filter(monitored=True).order_by("name")

    if only_with_messages:
        qs = qs.filter(messages__isnull=False).distinct()

    return qs


def check_user_group_access(user: User, group: Group):
    """Lança PermissionDenied se o usuário tentar acessar grupo não atribuído."""
    if not user.can_view_group(group):
        raise PermissionDenied(
            "Acesso negado: Você não tem permissão para acessar as informações deste grupo."
        )


@login_required
def home_view(request):
    """
    Tela 2: Dashboard Geral
    Cards de indicadores (mensagens, ativos, inativos, entradas/saídas),
    gráfico diário Chart.js e ranking dos mais ativos.
    Apresenta apenas grupos que possuem mensagens monitoradas.
    """
    accessible_groups = get_user_accessible_groups(request.user, only_with_messages=True)
    selected_group_id = request.GET.get("group_id")

    selected_group = None
    if selected_group_id:
        selected_group = accessible_groups.filter(pk=selected_group_id).first()
    if not selected_group and accessible_groups.exists():
        selected_group = accessible_groups.first()

    kpis = {}
    chart_data = {"labels": [], "data": []}
    ranking = []
    recent_messages = []

    if selected_group:
        try:
            from apps.messages_monitor.tasks import sync_group_recent_messages

            sync_group_recent_messages(selected_group, limit=25)
        except Exception as e:
            logger.warning(
                f"Erro ao sincronizar mensagens recentes do grupo {selected_group.jid}: {e}"
            )

        metrics_service = GroupMetricsService(selected_group)
        kpis = metrics_service.get_dashboard_kpis()
        chart_data = metrics_service.get_daily_activity(days=14)
        ranking = metrics_service.get_most_active_ranking(limit=8)
        recent_messages = (
            Message.objects.filter(group=selected_group)
            .select_related("member")
            .order_by("-sent_at")[:10]
        )

    context = {
        "accessible_groups": accessible_groups,
        "selected_group": selected_group,
        "kpis": kpis,
        "chart_data": chart_data,
        "chart_data_json": json.dumps(chart_data),
        "ranking": ranking,
        "recent_messages": recent_messages,
    }
    return render(request, "dashboard/index.html", context)


@login_required
def groups_list_view(request):
    """
    Tela 3: Lista de Grupos
    Exibe lista, estado do monitoramento, última mensagem recebida e saúde da conexão com a Evolution.
    """
    groups = get_user_accessible_groups(request.user)

    active_instances = EvolutionInstance.objects.filter(active=True)

    # Atualização sob demanda da conexão ao clicar no botão de atualizar (Apenas Admin)
    if request.GET.get("refresh") == "1" and request.user.is_admin:
        for inst in active_instances:
            try:
                EvolutionReadOnlyClient(instance=inst).get_connection_state()
            except Exception as e:
                logger.warning(f"Erro ao verificar conexão da instância {inst.name}: {e}")
        return redirect("dashboard:groups-list")

    # Sincronização sob demanda dos grupos da Evolution API (Apenas Admin)
    if request.GET.get("sync") == "1" and request.user.is_admin:
        try:
            from apps.groups.services import GroupSyncService

            synced = GroupSyncService.sync_all_instances_groups()
            messages.success(
                request,
                f"Sincronização concluída! {len(synced)} grupo(s) identificados na Evolution API.",
            )
        except Exception as e:
            logger.error(f"Erro ao sincronizar grupos: {e}")
            messages.error(request, f"Erro ao sincronizar grupos com a Evolution API: {e}")
        return redirect("dashboard:groups-list")

    # Checar status da conexão com as instâncias cadastradas da Evolution API (Multi-Bot)
    evo_state = "disconnected"
    try:
        if not active_instances.exists():
            evo_state = "not_configured"
        else:
            connected_count = sum(1 for inst in active_instances if inst.is_connected)
            if connected_count == active_instances.count():
                evo_state = "connected"
            elif connected_count > 0:
                evo_state = "partial"
            else:
                evo_state = "disconnected"
    except Exception:
        evo_state = "unavailable"

    # Enriquecer grupos com contagem e última mensagem
    group_items = []
    for g in groups:
        last_msg = Message.objects.filter(group=g).order_by("-sent_at").first()
        active_members_count = GroupMembership.objects.filter(group=g, left_at__isnull=True).count()
        group_items.append(
            {
                "group": g,
                "last_message": last_msg,
                "active_members_count": active_members_count,
            }
        )

    return render(
        request,
        "dashboard/groups_list.html",
        {"group_items": group_items, "evo_state": evo_state},
    )


@login_required
@admin_required
def group_toggle_monitored_view(request, group_id):
    """Alterna o status de monitoramento de um grupo (apenas Admin)."""
    group = get_object_or_404(Group, pk=group_id)
    group.monitored = not group.monitored
    group.save(update_fields=["monitored"])

    AuditLog.log(
        action=AuditLog.Action.GROUP_MONITOR_TOGGLE,
        request=request,
        target=f"Grupo {group.jid} ({'Monitorado' if group.monitored else 'Desativado'})",
    )
    messages.success(
        request,
        f"Monitoramento do grupo {group.name or group.jid} atualizado para: {'Ativo' if group.monitored else 'Inativo'}.",
    )
    return redirect("dashboard:groups-list")


@login_required
def group_participants_view(request, group_id):
    """
    Tela 4: Participantes do Grupo
    Tabela com busca, filtros (presente/saiu, papel), ordenação (mensagens, entrada, papel),
    paginação e revelação de telefone auditada.
    """
    group = get_object_or_404(Group, pk=group_id)
    check_user_group_access(request.user, group)

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all")  # all, present, left
    role_filter = request.GET.get("role", "all")

    # Tratamento de ordenação (suporta sort_by do select ou sort/direction dos cabeçalhos clicáveis)
    sort_by_param = request.GET.get("sort_by", "").strip()
    if sort_by_param and "_" in sort_by_param:
        sort, direction = sort_by_param.rsplit("_", 1)
    else:
        sort = request.GET.get("sort", "messages")
        direction = request.GET.get("direction", "desc")

    if sort not in ("messages", "joined", "role"):
        sort = "messages"
    if direction not in ("asc", "desc"):
        direction = "desc"

    msg_subquery = (
        Message.objects.filter(group=group, member=OuterRef("member_id"))
        .values("member_id")
        .annotate(cnt=Count("id"))
        .values("cnt")
    )

    memberships = (
        GroupMembership.objects.filter(group=group)
        .select_related("member")
        .annotate(
            total_messages=Coalesce(Subquery(msg_subquery, output_field=IntegerField()), Value(0))
        )
    )

    if status_filter == "present":
        memberships = memberships.filter(left_at__isnull=True)
    elif status_filter == "left":
        memberships = memberships.filter(left_at__isnull=False)

    if role_filter in ("member", "admin", "superadmin"):
        memberships = memberships.filter(role=role_filter)

    if query:
        memberships = memberships.filter(
            Q(member__push_name__icontains=query)
            | Q(member__phone__icontains=query)
            | Q(member__lid__icontains=query)
        )

    # Aplicação da ordenação escolhida
    if sort == "joined":
        if direction == "asc":
            memberships = memberships.order_by("joined_at", "-total_messages")
        else:
            memberships = memberships.order_by("-joined_at", "-total_messages")
    elif sort == "role":
        role_priority = Case(
            When(role=GroupMembership.Role.SUPERADMIN, then=Value(1)),
            When(role=GroupMembership.Role.ADMIN, then=Value(2)),
            When(role=GroupMembership.Role.MEMBER, then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
        memberships = memberships.annotate(role_priority=role_priority)
        if direction == "asc":
            memberships = memberships.order_by(
                "-role_priority", "-total_messages", "member__push_name"
            )
        else:
            memberships = memberships.order_by(
                "role_priority", "-total_messages", "member__push_name"
            )
    else:  # messages
        if direction == "asc":
            memberships = memberships.order_by("total_messages", "joined_at")
        else:
            memberships = memberships.order_by("-total_messages", "-joined_at")

    paginator = Paginator(memberships, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    sort_by = f"{sort}_{direction}"
    context = {
        "group": group,
        "page_obj": page_obj,
        "query": query,
        "status_filter": status_filter,
        "role_filter": role_filter,
        "sort": sort,
        "direction": direction,
        "sort_by": sort_by,
    }

    # Se a requisição for HTMX, renderiza apenas a tabela parcial
    if request.headers.get("HX-Request"):
        return render(request, "dashboard/partials/participants_table.html", context)

    return render(request, "dashboard/group_participants.html", context)


@login_required
def member_profile_view(request, group_id, member_id):
    """
    Tela 5: Perfil do Participante
    Total de mensagens, atividade mensal, linha do tempo com filtros e palavras-chave citadas.
    """
    group = get_object_or_404(Group, pk=group_id)
    check_user_group_access(request.user, group)
    member = get_object_or_404(Member, pk=member_id)

    membership = GroupMembership.objects.filter(group=group, member=member).first()
    messages_qs = Message.objects.filter(group=group, member=member).order_by("-sent_at")

    # Contagem total de mensagens do participante no grupo
    total_messages = messages_qs.count()

    # Status de atividade no mês atual
    metrics_service = GroupMetricsService(group)
    now = timezone.now()
    is_active_this_month = metrics_service.is_member_active_in_month(member, now.year, now.month)

    # Filtros de busca na linha do tempo
    type_filter = request.GET.get("type", "")
    term_filter = request.GET.get("term", "").strip()
    status_msg = request.GET.get("status_msg", "all")

    if type_filter:
        messages_qs = messages_qs.filter(type=type_filter)
    if term_filter:
        messages_qs = messages_qs.filter(text__icontains=term_filter)
    if status_msg == "deleted":
        messages_qs = messages_qs.filter(deleted_at__isnull=False)
    elif status_msg == "active":
        messages_qs = messages_qs.filter(deleted_at__isnull=True)

    deleted_messages_count = Message.objects.filter(
        group=group, member=member, deleted_at__isnull=False
    ).count()

    # Paginação das mensagens
    paginator = Paginator(messages_qs, 30)
    page_number = request.GET.get("page", 1)
    messages_page = paginator.get_page(page_number)

    # Palavras-chave do grupo citadas por este participante
    group_keywords = Keyword.objects.filter(group=group, active=True)
    cited_keywords = []
    for kw in group_keywords:
        c = Message.objects.filter(group=group, member=member, text__icontains=kw.term).count()
        if c > 0:
            cited_keywords.append({"term": kw.term, "count": c})

    # Atividade mensal dos últimos 6 meses
    now = timezone.now()
    monthly_activity = []
    for i in range(5, -1, -1):
        m = (now.month - i - 1) % 12 + 1
        y = now.year if (now.month - i) > 0 else now.year - 1
        count = Message.objects.filter(
            group=group,
            member=member,
            sent_at__year=y,
            sent_at__month=m,
        ).count()
        monthly_activity.append({"month_label": f"{m:02d}/{y}", "count": count})

    # Registrar consulta detalhada a mensagens na auditoria
    AuditLog.log(
        action=AuditLog.Action.VIEW_MESSAGES,
        request=request,
        target=f"Mensagens do participante #{member.pk} no grupo #{group.pk}",
    )

    context = {
        "group": group,
        "member": member,
        "membership": membership,
        "total_messages": total_messages,
        "deleted_messages_count": deleted_messages_count,
        "is_active_this_month": is_active_this_month,
        "messages_page": messages_page,
        "cited_keywords": cited_keywords,
        "monthly_activity": monthly_activity,
        "type_filter": type_filter,
        "term_filter": term_filter,
        "status_msg": status_msg,
    }

    if request.headers.get("HX-Request"):
        return render(request, "dashboard/partials/member_messages_list.html", context)

    return render(request, "dashboard/member_profile.html", context)


@login_required
def reveal_member_phone_view(request, member_id):
    """
    Revela o telefone desmascarado via HTMX sob demanda para papéis autorizados (Admin/Analista).
    Gera obrigatoriamente um registro de auditoria para fins de LGPD.
    Suporta requisições GET e POST.
    """
    if not request.user.can_reveal_phone:
        raise PermissionDenied("Você não tem autorização para revelar dados de contato.")

    member = get_object_or_404(Member, pk=member_id)

    # Registro de auditoria obrigatório
    AuditLog.log(
        action=AuditLog.Action.REVEAL_PHONE,
        request=request,
        target=f"Participante #{member.pk} (Telefone desmascarado)",
    )

    clean = member.clean_phone or member.phone or ""
    formatted = (
        member.formatted_phone
        if member.phone
        else (member.phone or "Sem número de telefone registrado")
    )
    return HttpResponse(
        f'<span class="inline-flex items-center space-x-1 font-mono text-emerald-600 dark:text-emerald-400 font-semibold select-all" '
        f'title="Telefone desmascarado (Ação auditada)">'
        f"<span>{formatted}</span>"
        f'<span class="sr-only">({clean})</span>'
        f"</span>"
    )


@login_required
def keywords_view(request, group_id):
    """
    Tela 6: Palavras-chave
    Cadastro, ativação/desativação e relatório de citações por período.
    """
    group = get_object_or_404(Group, pk=group_id)
    check_user_group_access(request.user, group)

    keywords = Keyword.objects.filter(group=group).order_by("term")

    # Tratar cadastro de nova palavra-chave (apenas Admin e Analista)
    if request.method == "POST" and request.user.is_analista:
        action = request.POST.get("action")
        if action == "add":
            term = request.POST.get("term", "").strip()
            if term:
                kw, created = Keyword.objects.get_or_create(
                    group=group, term=term, defaults={"active": True}
                )
                if created:
                    backfill_keyword_hits.defer(keyword_id=kw.id)
                AuditLog.log(
                    action=AuditLog.Action.KEYWORD_CHANGE,
                    request=request,
                    target=f"Adicionada palavra-chave '{term}' ao grupo {group.jid}",
                )
                messages.success(
                    request,
                    f"Termo '{term}' adicionado ao monitoramento. Backfill histórico iniciado.",
                )
        elif action == "toggle":
            kw_id = request.POST.get("keyword_id")
            kw = get_object_or_404(Keyword, pk=kw_id, group=group)
            kw.active = not kw.active
            kw.save(update_fields=["active"])
            AuditLog.log(
                action=AuditLog.Action.KEYWORD_CHANGE,
                request=request,
                target=f"Palavra-chave '{kw.term}' {'ativada' if kw.active else 'pausada'}",
            )
        elif action == "delete":
            kw_id = request.POST.get("keyword_id")
            kw = get_object_or_404(Keyword, pk=kw_id, group=group)
            kw.delete()
            AuditLog.log(
                action=AuditLog.Action.KEYWORD_CHANGE,
                request=request,
                target=f"Removida palavra-chave '{kw.term}'",
            )
            messages.success(request, "Termo removido com sucesso.")

        return redirect("dashboard:keywords", group_id=group.pk)

    # Relatório de citações da palavra-chave selecionada (busca prioritária em KeywordHit indexado)
    selected_term = request.GET.get("term", "").strip()
    citations = []
    if selected_term:
        hits = (
            KeywordHit.objects.filter(group=group, keyword__term__iexact=selected_term)
            .select_related("message", "member", "keyword")
            .order_by("-sent_at")[:100]
        )
        if hits.exists():
            for hit in hits:
                highlighted = safe_highlight_terms(hit.message.text, [selected_term])
                citations.append(
                    {
                        "message": hit.message,
                        "highlighted_text": highlighted,
                    }
                )
        else:
            backend = get_search_backend()
            found_messages = backend.search(query=selected_term, group_id=group.pk)[:100]
            for msg in found_messages:
                highlighted = safe_highlight_terms(msg.text, [selected_term])
                citations.append(
                    {
                        "message": msg,
                        "highlighted_text": highlighted,
                    }
                )

    context = {
        "group": group,
        "keywords": keywords,
        "selected_term": selected_term,
        "citations": citations,
    }
    return render(request, "dashboard/keywords.html", context)


@login_required
@can_export_required
def export_csv_view(request, group_id):
    """
    Exportação protegida de relatórios em CSV com imunização contra CSV injection.
    Gera registro de auditoria obrigatório.
    """
    group = get_object_or_404(Group, pk=group_id)
    check_user_group_access(request.user, group)

    report_type = request.GET.get("type", "participants")
    term = request.GET.get("term", "")

    if report_type == "keywords" and term:
        csv_content = ExportService.export_keyword_report_csv(group, term)
        filename = f"relatorio_palavra_chave_{group.id}_{term}.csv"
        AuditLog.log(
            action=AuditLog.Action.EXPORT_CSV,
            request=request,
            target=f"Exportação de palavras-chave '{term}' do grupo {group.jid}",
        )
    else:
        csv_content = ExportService.export_group_participants_csv(group)
        filename = f"participantes_grupo_{group.id}.csv"
        AuditLog.log(
            action=AuditLog.Action.EXPORT_CSV,
            request=request,
            target=f"Exportação de participantes do grupo {group.jid}",
        )

    response = HttpResponse(csv_content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@admin_required
def settings_view(request):
    """
    Tela 7: Configurações e Auditoria (Somente Administrador)
    Gestão de usuários, atribuição de grupos para proteção IDOR, envio de convites e visualizador de logs.
    """
    users = User.objects.all().prefetch_related("assigned_groups").order_by("-date_joined")
    groups = Group.objects.all().order_by("name")
    invitations = UserInvitation.objects.all().order_by("-created_at")[:20]
    evolution_instances = EvolutionInstance.objects.all().order_by("-active", "name")

    # Visualização de logs de auditoria com paginação
    audit_logs = AuditLog.objects.all().select_related("user").order_by("-created_at")
    action_filter = request.GET.get("action", "")
    if action_filter:
        audit_logs = audit_logs.filter(action=action_filter)

    paginator = Paginator(audit_logs, 40)
    page_number = request.GET.get("audit_page", 1)
    audit_page_obj = paginator.get_page(page_number)

    # Criação de novo convite de uso único ou gestão de conexões Evolution
    if request.method == "POST":
        action = request.POST.get("form_action")
        if action == "invite":
            email = request.POST.get("email", "").strip().lower()
            role = request.POST.get("role", User.Role.LEITOR)
            if email:
                inv = UserInvitation.create_invitation(
                    email=email,
                    role=role,
                    invited_by=request.user,
                    valid_days=3,
                )
                AuditLog.log(
                    action=AuditLog.Action.PERMISSION_CHANGE,
                    request=request,
                    target=f"Convite enviado para {email} com papel {role}",
                )
                invite_url = request.build_absolute_uri(f"/accounts/invitation/{inv.token}/")
                messages.success(request, f"Convite criado! Link de uso único: {invite_url}")
            return redirect(f"{reverse('dashboard:settings')}?tab=invites")

        elif action == "update_user":
            user_id = request.POST.get("user_id")
            target_user = get_object_or_404(User, pk=user_id)
            new_role = request.POST.get("role")
            assigned_group_ids = request.POST.getlist("groups")

            if new_role in (User.Role.ADMIN, User.Role.ANALISTA, User.Role.LEITOR):
                target_user.role = new_role
                target_user.assigned_groups.set(assigned_group_ids)
                target_user.save()
                AuditLog.log(
                    action=AuditLog.Action.PERMISSION_CHANGE,
                    request=request,
                    target=f"Permissões atualizadas para {target_user.email} (Papel: {new_role})",
                )
                messages.success(request, f"Permissões do usuário {target_user.email} atualizadas.")
            return redirect(f"{reverse('dashboard:settings')}?tab=users")

        elif action == "add_evolution_instance":
            name = request.POST.get("name", "").strip()
            api_url = request.POST.get("api_url", "").strip().rstrip("/")
            api_key = request.POST.get("api_key", "").strip()
            instance_name = request.POST.get("instance_name", "").strip()
            webhook_secret = request.POST.get("webhook_secret", "").strip()
            active = request.POST.get("active") in ("on", "true", "1")

            if name and api_url and instance_name:
                inst = EvolutionInstance.objects.create(
                    name=name,
                    api_url=api_url,
                    api_key=api_key,
                    instance_name=instance_name,
                    webhook_secret=webhook_secret,
                    active=active,
                )
                try:
                    client = EvolutionReadOnlyClient(instance=inst)
                    client.get_connection_state()
                    from apps.groups.services import GroupSyncService

                    GroupSyncService.sync_instance_groups(instance=inst)
                except Exception as e:
                    logger.warning(
                        f"Erro ao testar/sincronizar conexão Evolution '{inst.name}' após criação: {e}"
                    )

                AuditLog.log(
                    action=AuditLog.Action.PERMISSION_CHANGE,
                    request=request,
                    target=f"Conexão Evolution '{inst.name}' ({inst.instance_name}) adicionada",
                )
                messages.success(request, f"Conexão/Bot '{inst.name}' cadastrada com sucesso.")
            else:
                messages.error(
                    request,
                    "Preencha todos os campos obrigatórios: Nome do Bot, URL da API e Nome da Instância.",
                )
            return redirect(f"{reverse('dashboard:settings')}?tab=evolution")

        elif action == "update_evolution_instance":
            instance_id = request.POST.get("instance_id")
            inst = get_object_or_404(EvolutionInstance, pk=instance_id)
            name = request.POST.get("name", "").strip()
            api_url = request.POST.get("api_url", "").strip().rstrip("/")
            api_key = request.POST.get("api_key", "").strip()
            instance_name = request.POST.get("instance_name", "").strip()
            webhook_secret = request.POST.get("webhook_secret", "").strip()
            active = request.POST.get("active") in ("on", "true", "1")

            if name and api_url and instance_name:
                inst.name = name
                inst.api_url = api_url
                inst.api_key = api_key
                inst.instance_name = instance_name
                if webhook_secret != inst.webhook_secret:
                    inst.webhook_secret_old = inst.webhook_secret
                    inst.webhook_secret = webhook_secret
                inst.active = active
                inst.save()
                try:
                    client = EvolutionReadOnlyClient(instance=inst)
                    client.get_connection_state()
                    from apps.groups.services import GroupSyncService

                    GroupSyncService.sync_instance_groups(instance=inst)
                except Exception as e:
                    logger.warning(
                        f"Erro ao testar/sincronizar conexão Evolution '{inst.name}' após atualização: {e}"
                    )

                AuditLog.log(
                    action=AuditLog.Action.PERMISSION_CHANGE,
                    request=request,
                    target=f"Conexão Evolution '{inst.name}' ({inst.instance_name}) atualizada",
                )
                messages.success(request, f"Conexão '{inst.name}' atualizada com sucesso.")
            else:
                messages.error(request, "Preencha todos os campos obrigatórios.")
            return redirect(f"{reverse('dashboard:settings')}?tab=evolution")

        elif action == "sync_evolution_instance_groups":
            instance_id = request.POST.get("instance_id")
            inst = get_object_or_404(EvolutionInstance, pk=instance_id)
            try:
                from apps.groups.services import GroupSyncService

                synced = GroupSyncService.sync_instance_groups(instance=inst)
                AuditLog.log(
                    action=AuditLog.Action.GROUP_MONITOR_TOGGLE,
                    request=request,
                    target=f"Sincronizados {len(synced)} grupos da instância Evolution '{inst.name}'",
                )
                messages.success(
                    request,
                    f"Sincronização com '{inst.name}' concluída! {len(synced)} grupo(s) identificados.",
                )
            except Exception as e:
                logger.error(f"Erro ao sincronizar grupos da instância {inst.name}: {e}")
                messages.error(
                    request, f"Erro ao sincronizar grupos da instância '{inst.name}': {e}"
                )
            return redirect(f"{reverse('dashboard:settings')}?tab=evolution")

        elif action == "toggle_evolution_instance":
            instance_id = request.POST.get("instance_id")
            inst = get_object_or_404(EvolutionInstance, pk=instance_id)
            inst.active = not inst.active
            inst.save(update_fields=["active"])
            if inst.active:
                try:
                    client = EvolutionReadOnlyClient(instance=inst)
                    client.get_connection_state()
                except Exception as e:
                    logger.warning(
                        f"Erro ao testar conexão Evolution '{inst.name}' após ativação: {e}"
                    )
            status_text = "ativada" if inst.active else "pausada"
            AuditLog.log(
                action=AuditLog.Action.PERMISSION_CHANGE,
                request=request,
                target=f"Conexão Evolution '{inst.name}' {status_text}",
            )
            messages.success(request, f"Conexão '{inst.name}' {status_text}.")
            return redirect(f"{reverse('dashboard:settings')}?tab=evolution")

        elif action == "delete_evolution_instance":
            instance_id = request.POST.get("instance_id")
            inst = get_object_or_404(EvolutionInstance, pk=instance_id)
            inst_name = inst.name
            inst.delete()
            AuditLog.log(
                action=AuditLog.Action.PERMISSION_CHANGE,
                request=request,
                target=f"Conexão Evolution '{inst_name}' removida",
            )
            messages.success(request, f"Conexão '{inst_name}' removida com sucesso.")
            return redirect(f"{reverse('dashboard:settings')}?tab=evolution")

        elif action == "retry_job":
            job_id = request.POST.get("job_id")
            try:
                from procrastinate.contrib.django.models import ProcrastinateJob

                job = ProcrastinateJob.objects.filter(pk=job_id).first()
                if job:
                    job.status = "todo"
                    job.attempts = 0
                    job.save(update_fields=["status", "attempts"])
                    AuditLog.log(
                        action=AuditLog.Action.PERMISSION_CHANGE,
                        request=request,
                        target=f"Job #{job_id} ({job.task_name}) reenviado na fila de falhas",
                    )
                    messages.success(
                        request, f"Tarefa #{job_id} ({job.task_name}) reenviada para execução."
                    )
            except Exception as e:
                messages.error(request, f"Erro ao reenviar tarefa: {e}")
            return redirect(f"{reverse('dashboard:settings')}?tab=jobs")

        return redirect("dashboard:settings")

    # Fila de Falhas do Procrastinate (Jobs com status 'failed')
    failed_jobs = []
    try:
        from procrastinate.contrib.django.models import ProcrastinateJob

        failed_jobs = list(ProcrastinateJob.objects.filter(status="failed").order_by("-id")[:50])
    except Exception:
        failed_jobs = []

    context = {
        "users": users,
        "groups": groups,
        "invitations": invitations,
        "evolution_instances": evolution_instances,
        "audit_page_obj": audit_page_obj,
        "action_filter": action_filter,
        "failed_jobs": failed_jobs,
        "AuditLogAction": AuditLog.Action,
    }
    return render(request, "dashboard/settings.html", context)


@login_required
@admin_required
def test_evolution_instance_view(request, instance_id):
    """
    Testa a conectividade com uma instância da Evolution API sob demanda.
    Retorna fragmento HTML com badge de status para substituição via HTMX.
    """
    instance = get_object_or_404(EvolutionInstance, pk=instance_id)
    client = EvolutionReadOnlyClient(instance=instance)
    res = client.get_connection_state()
    instance.refresh_from_db()
    raw_state = res.get("state") or res.get("instance", {}).get("state") or instance.last_state
    state = str(raw_state).lower()

    return render(
        request,
        "dashboard/partials/evolution_status_badge.html",
        {"instance": instance, "state": state, "res": res},
    )
