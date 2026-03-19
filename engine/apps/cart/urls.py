from django.urls import path

from . import views

urlpatterns = [
    path('', views.CartDetailView.as_view(), name='cart-detail'),
    path('add/', views.CartAddView.as_view(), name='cart-add'),
    path('items/<str:item_id>/update/', views.CartUpdateView.as_view(), name='cart-update'),
    path('items/<str:item_id>/remove/', views.CartRemoveView.as_view(), name='cart-remove'),
    path('remove-by-product/<uuid:product_id>/', views.CartRemoveByProductView.as_view(), name='cart-remove-by-product'),
    path('clear/', views.CartClearView.as_view(), name='cart-clear'),
]
