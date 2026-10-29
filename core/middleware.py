import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin


class SessionTimeoutMiddleware(MiddlewareMixin):
    """
    Controla expiração de sessão por inatividade (ex: 30 minutos)
    e tempo máximo absoluto de sessão (ex: 12 horas).
    """

    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return None

        current_time = int(time.time())
        inactivity_limit = getattr(settings, "SESSION_COOKIE_AGE", 1800)  # 30 min
        max_absolute_limit = getattr(settings, "SESSION_MAX_AGE", 43200)  # 12 horas

        last_activity = request.session.get("last_activity")
        session_created = request.session.get("session_created")

        if session_created is None:
            request.session["session_created"] = current_time

        if last_activity is not None:
            # Verifica inatividade
            if (current_time - last_activity) > inactivity_limit:
                logout(request)
                request.session.flush()
                messages.warning(
                    request, "Sua sessão expirou por inatividade. Faça login novamente."
                )
                return redirect("accounts:login")

            # Verifica tempo máximo absoluto
            if session_created and (current_time - session_created) > max_absolute_limit:
                logout(request)
                request.session.flush()
                messages.warning(request, "Tempo máximo de sessão atingido. Faça login novamente.")
                return redirect("accounts:login")

        request.session["last_activity"] = current_time
        return None


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Adiciona cabeçalhos de segurança obrigatórios:
    - Cache-Control: no-store para respostas autenticadas
    - Permissions-Policy e Cross-Origin-Opener-Policy
    """

    def process_response(self, request, response):
        # Para usuários autenticados, nunca armazenar cache no navegador ou proxies
        if hasattr(request, "user") and request.user.is_authenticated:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"

        # Cabeçalhos modernos de segurança do navegador
        response["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Resource-Policy"] = "same-origin"

        return response
