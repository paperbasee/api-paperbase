from django.urls import path
from . import views

urlpatterns = [
    path('me/', views.CustomerProfileView.as_view()),
    path('addresses/', views.CustomerAddressListCreateView.as_view()),
    path('addresses/<str:public_id>/', views.CustomerAddressDetailView.as_view()),
]
