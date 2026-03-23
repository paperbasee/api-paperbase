from django.urls import path

from . import system_views

urlpatterns = [
    path(
        "active/",
        system_views.ActiveSystemNotificationView.as_view(),
        name="system-notification-active",
    ),
    path(
        "<str:public_id>/dismiss/",
        system_views.DismissSystemNotificationView.as_view(),
        name="system-notification-dismiss",
    ),
]
