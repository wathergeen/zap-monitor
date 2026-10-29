import base64
import io
import logging

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit.models import AuditLog

from .forms import (
    AcceptInvitationForm,
    LoginForm,
    PasswordResetRequestForm,
    TOTPVerifyForm,
)
from .models import RecoveryCode, User, UserInvitation

logger = logging.getLogger("apps.accounts")


def role_required(*allowed_roles):
    """Decorator de autorização baseado em papéis (Princípio do Menor Privilégio)."""

    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("accounts:login")
            if request.user.is_superuser or request.user.role in allowed_roles:
                return view_func(request, *args, **kwargs)
            logger.warning(
                f"Acesso negado para usuário {request.user.email} (papel {request.user.role}) na view {view_func.__name__}"
            )
            raise PermissionDenied("Você não tem autorização para acessar esta funcionalidade.")

        return _wrapped_view

    return decorator


def admin_required(view_func):
    return role_required(User.Role.ADMIN)(view_func)


def can_export_required(view_func):
    """Permite acesso apenas a Admin ou Analista."""
    return role_required(User.Role.ADMIN, User.Role.ANALISTA)(view_func)


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    form = LoginForm(request.POST or None, request=request)
    if request.method == "POST":
        if form.is_valid():
            user = form.user
            # Checar exigência de 2FA
            has_totp = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
            totp_enforced = getattr(settings, "TOTP_ENFORCE_ADMIN", False) and user.is_admin

            if has_totp or totp_enforced:
                request.session["pre_2fa_user_id"] = user.id
                return redirect("accounts:totp-verify")

            # Login direto com rotação de sessão
            login(request, user)
            request.session.cycle_key()
            AuditLog.log(action=AuditLog.Action.LOGIN, request=request, user=user)
            messages.success(request, f"Bem-vindo de volta, {user.full_name or user.email}!")
            return redirect("dashboard:home")
        else:
            # Registrar falha de login na auditoria sem revelar existência de conta
            email = request.POST.get("email", "")
            AuditLog.log(
                action=AuditLog.Action.LOGIN_FAILED,
                request=request,
                target=f"Tentativa de login: {email[:50]}",
            )

    return render(request, "accounts/login.html", {"form": form})


@never_cache
def totp_verify_view(request):
    user_id = request.session.get("pre_2fa_user_id")
    if not user_id:
        return redirect("accounts:login")

    user = get_object_or_404(User, pk=user_id)
    form = TOTPVerifyForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip()

        # 1. Tentar validação com TOTP Device
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        is_valid = device and device.verify_token(code)

        # 2. Se falhar, tentar validação com código de recuperação de emergência
        if not is_valid:
            is_valid = RecoveryCode.verify_and_use_code(user, code)

        if is_valid:
            del request.session["pre_2fa_user_id"]
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session.cycle_key()
            AuditLog.log(
                action=AuditLog.Action.LOGIN, request=request, user=user, details={"2fa": "totp"}
            )
            messages.success(request, "Autenticação em duas etapas confirmada com sucesso.")
            return redirect("dashboard:home")
        else:
            AuditLog.log(
                action=AuditLog.Action.LOGIN_FAILED,
                request=request,
                user=user,
                details={"2fa_failure": True},
            )
            form.add_error("code", "Código de verificação incorreto ou expirado.")

    return render(request, "accounts/totp_verify.html", {"form": form, "user_email": user.email})


@login_required
@never_cache
def totp_setup_view(request):
    """Configuração inicial ou redefinição de 2FA TOTP pelo usuário."""
    user = request.user
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if not device:
        device = TOTPDevice.objects.create(user=user, name="Padrão", confirmed=False)

    qr_base64 = ""
    try:
        url = device.config_url
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_base64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        logger.error(f"Erro ao gerar QR Code TOTP: {e}")

    form = TOTPVerifyForm(request.POST or None)
    recovery_codes = []

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip()
        if device.verify_token(code):
            device.confirmed = True
            device.save()
            # Remove outros dispositivos não confirmados
            TOTPDevice.objects.filter(user=user, confirmed=False).delete()
            # Gera 8 códigos de recuperação de backup com hash
            recovery_codes = RecoveryCode.generate_codes(user)
            AuditLog.log(
                action=AuditLog.Action.PERMISSION_CHANGE,
                request=request,
                target="Ativação de 2FA TOTP",
            )
            messages.success(
                request, "2FA ativado com sucesso! Guarde seus códigos de backup com segurança."
            )
            return render(
                request,
                "accounts/totp_codes.html",
                {"recovery_codes": recovery_codes},
            )
        else:
            form.add_error(
                "code", "Código inválido. Digite o código exibido no seu aplicativo autenticador."
            )

    return render(
        request,
        "accounts/totp_setup.html",
        {"form": form, "qr_base64": qr_base64, "device": device},
    )


@never_cache
def logout_view(request):
    if request.user.is_authenticated:
        AuditLog.log(action=AuditLog.Action.LOGOUT, request=request)
    logout(request)
    request.session.flush()
    messages.info(request, "Você encerrou sua sessão com segurança.")
    return redirect("accounts:login")


@never_cache
def password_reset_request_view(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        AuditLog.log(
            action=AuditLog.Action.PASSWORD_RESET,
            request=request,
            target=f"Solicitação para: {email[:50]}",
        )
        # Resposta 100% idêntica sem enumerar se o e-mail existe
        messages.success(
            request,
            "Se o endereço informado estiver cadastrado no sistema, você receberá instruções de redefinição.",
        )
        return redirect("accounts:login")

    return render(request, "accounts/password_reset.html", {"form": form})


@never_cache
def accept_invitation_view(request, token):
    invitation = get_object_or_404(UserInvitation, token=token)
    if not invitation.is_valid:
        messages.error(
            request,
            "Este convite expirou ou já foi utilizado. Solicite um novo convite ao administrador.",
        )
        return redirect("accounts:login")

    user, created = User.objects.get_or_create(
        email=invitation.email,
        defaults={"role": invitation.role, "is_active": True},
    )

    form = AcceptInvitationForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        user.full_name = form.cleaned_data["full_name"]
        user.role = invitation.role
        user.set_password(form.cleaned_data["password"])
        user.is_active = True
        user.save()

        invitation.used_at = timezone.now()
        invitation.save(update_fields=["used_at"])

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.cycle_key()
        AuditLog.log(
            action=AuditLog.Action.LOGIN,
            request=request,
            user=user,
            details={"invitation_accepted": True},
        )
        messages.success(request, "Conta ativada com sucesso! Bem-vindo ao sistema.")
        return redirect("dashboard:home")

    return render(
        request,
        "accounts/accept_invitation.html",
        {"form": form, "invitation": invitation},
    )


def lockout_view(request, credentials=None):
    """Tela de bloqueio por proteção de força bruta (django-axes)."""
    return render(
        request,
        "accounts/lockout.html",
        {
            "cooloff_time": getattr(settings, "AXES_COOLOFF_TIME", 1),
        },
        status=429,
    )
