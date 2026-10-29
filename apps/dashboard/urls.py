from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("groups/", views.groups_list_view, name="groups-list"),
    path(
        "groups/<int:group_id>/toggle-monitored/",
        views.group_toggle_monitored_view,
        name="group-toggle-monitored",
    ),
    path(
        "groups/<int:group_id>/participants/",
        views.group_participants_view,
        name="group-participants",
    ),
    path(
        "groups/<int:group_id>/participants/<int:member_id>/",
        views.member_profile_view,
        name="member-profile",
    ),
    path("groups/<int:group_id>/keywords/", views.keywords_view, name="keywords"),
    path("groups/<int:group_id>/export/", views.export_csv_view, name="export-csv"),
    path(
        "members/<int:member_id>/reveal-phone/", views.reveal_member_phone_view, name="reveal-phone"
    ),
    path("settings/", views.settings_view, name="settings"),
    path(
        "settings/evolution/test/<int:instance_id>/",
        views.test_evolution_instance_view,
        name="test-evolution-instance",
    ),
]
