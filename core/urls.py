from django.contrib import admin
from django.shortcuts import render
from django.urls import include, path

from apps.health.views import health_check_view
from apps.messages_monitor.views import evolution_webhook_view


def error_400(request, exception=None):
    return render(request, "errors/400.html", status=400)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)


handler400 = "core.urls.error_400"
handler403 = "core.urls.error_403"
handler404 = "core.urls.error_404"
handler500 = "core.urls.error_500"

urlpatterns = [
    # Painel interno do Django protegido em rota customizada
    path("internal-admin/", admin.site.urls),
    # Webhook da Evolution API (somente POST com X-Webhook-Secret)
    path("webhooks/evolution/", evolution_webhook_view, name="evolution-webhook"),
    # Healthcheck do sistema para orquestradores e Caddy
    path("healthz", health_check_view, name="healthz"),
    # Módulos do Sistema
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.dashboard.urls")),
]
