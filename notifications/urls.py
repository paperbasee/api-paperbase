from django.urls import path

from . import views

urlpatterns = [
    path('active/', views.ActiveNotificationListView.as_view(), name='notification-active'),
]
