from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("2fa/verify/", views.totp_verify_view, name="totp-verify"),
    path("2fa/setup/", views.totp_setup_view, name="totp-setup"),
    path("password-reset/", views.password_reset_request_view, name="password-reset"),
    path("invitation/<str:token>/", views.accept_invitation_view, name="accept-invitation"),
    path("lockout/", views.lockout_view, name="lockout"),
]
