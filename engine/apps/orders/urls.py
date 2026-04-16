from django.urls import path

from . import views

urlpatterns = [
    path('', views.OrderCreateView.as_view(), name='order-create'),
    path('<str:public_id>/', views.OrderDetailView.as_view(), name='order-detail'),
]
