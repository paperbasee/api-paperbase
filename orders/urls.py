from django.urls import path

from . import views

urlpatterns = [
    path('', views.OrderCreateView.as_view(), name='order-create'),
    path('direct/', views.DirectOrderCreateView.as_view(), name='order-create-direct'),
    path('initiate-checkout/', views.InitiateCheckoutView.as_view(), name='order-initiate-checkout'),
    path('my/', views.OrderListView.as_view(), name='order-list'),
    path('<str:id>/', views.OrderDetailView.as_view(), name='order-detail'),
]
