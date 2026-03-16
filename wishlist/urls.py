from django.urls import path

from . import views

urlpatterns = [
    path('', views.WishlistListView.as_view(), name='wishlist-list'),
    path('add/', views.WishlistAddView.as_view(), name='wishlist-add'),
    path('remove/<uuid:product_id>/', views.WishlistRemoveView.as_view(), name='wishlist-remove'),
    path('clear/', views.WishlistClearView.as_view(), name='wishlist-clear'),
]
