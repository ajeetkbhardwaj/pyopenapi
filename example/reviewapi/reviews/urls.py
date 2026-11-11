# FILE: reviews/urls.py

from django.urls import path, include
from rest_framework_nested import routers
from .views import (
    ReviewViewSet, ProductViewSet, CategoryViewSet,
    # +++ NEW IMPORTS +++
    ManufacturerViewSet, AdvancedProductViewSet
    # +++ END NEW IMPORTS +++
)

router = routers.DefaultRouter()
router.register(r'categories', CategoryViewSet)
# +++ NEW REGISTRATIONS +++
router.register(r'manufacturers', ManufacturerViewSet)
router.register(r'advanced-products', AdvancedProductViewSet)
# +++ END NEW REGISTRATIONS +++

categories_router = routers.NestedDefaultRouter(router, r'categories', lookup='category')
categories_router.register(r'products', ProductViewSet, basename='category-products')

products_router = routers.NestedDefaultRouter(categories_router, r'products', lookup='product')
products_router.register(r'reviews', ReviewViewSet, basename='product-reviews')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(categories_router.urls)),
    path('', include(products_router.urls)),
]