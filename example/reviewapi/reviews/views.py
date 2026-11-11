# FILE: reviews/views.py

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Review, Product, Category,
    # +++ NEW IMPORTS +++
    Manufacturer, AdvancedProduct
    # +++ END NEW IMPORTS +++
)
from .serializers import (
    ReviewSerializer, ProductSerializer, CategorySerializer,
    # +++ NEW IMPORTS +++
    ManufacturerSerializer, AdvancedProductSerializer
    # +++ END NEW IMPORTS +++
)
from .features import (
    CustomResultsSetPagination,
    ApiKeyAuthentication,
    ProductFilterSet,
    RequestTimingFilterBackend
)
# +++ NEW IMPORT FOR HELPER +++
from . import helpers
# +++ END NEW IMPORT +++

class ReviewViewSet(viewsets.ModelViewSet):
    """
    Handles Reviews. Uses standard DRF filtering.
    """
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    # --- Standard Features ---
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['rating', 'product__name']
    # --- End Standard Features ---

    def get_queryset(self):
        product_pk = self.kwargs.get('product_pk')
        category_pk = self.kwargs.get('category_pk')

        queryset = Review.objects.all()
        if product_pk:
            queryset = queryset.filter(product_id=product_pk)
        return queryset

    @action(detail=True, methods=['post'])
    def submit_comment(self, request, pk=None):
         review = self.get_object()
         comment_text = request.data.get('comment')
         if not comment_text:
             return Response({'error': 'Comment text required'}, status=400)
         print(f"Comment '{comment_text}' submitted for review {pk}")
         return Response({'status': 'comment submitted', 'review_id': pk})


class ProductViewSet(viewsets.ModelViewSet):
    """
    Handles Products. Uses custom Pagination, Authentication, and Filtering.
    """
    queryset = Product.objects.select_related('category').all()
    serializer_class = ProductSerializer

    # --- Custom Features Applied ---
    pagination_class = CustomResultsSetPagination
    authentication_classes = [ApiKeyAuthentication]
    filter_backends = [
        RequestTimingFilterBackend,
        DjangoFilterBackend
    ]
    filterset_class = ProductFilterSet
    # --- End Custom Features Applied ---

    @action(detail=False, methods=['get'])
    def top_products(self, request):
         top = Product.objects.order_by('name')[:3]
         page = self.paginate_queryset(top)
         if page is not None:
              serializer = self.get_serializer(page, many=True)
              return self.get_paginated_response(serializer.data)

         serializer = self.get_serializer(top, many=True)
         return Response(serializer.data)


class CategoryViewSet(viewsets.ModelViewSet):
    """
    Handles Categories. Kept simple without custom features.
    """
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    @action(detail=True, methods=['get'])
    def list_products(self, request, pk=None):
        category = self.get_object()
        products = category.products.all()
        serializer = ProductSerializer(products, many=True)
        return Response(serializer.data)

# +++ NEW VIEWS FOR TESTING +++

class ManufacturerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    TEST CASE: A simple ViewSet to expose a new model.
    Goal: Ensure basic new endpoints are picked up correctly.
    """
    queryset = Manufacturer.objects.all()
    serializer_class = ManufacturerSerializer

class AdvancedProductViewSet(viewsets.ModelViewSet):
    """
    TEST CASE: A viewset to test indirect model usage.
    """
    queryset = AdvancedProduct.objects.all()
    serializer_class = AdvancedProductSerializer

    @action(detail=True, methods=['get'])
    def get_competitors(self, request, pk=None):
        """
        TEST CASE: An action that uses a *different model* (`Product`) than the
        viewset's main model (`AdvancedProduct`).
        Goal: Verify the tool analyzes this method's code and includes context
              for the `Product` model and `ProductSerializer`, even though they
              aren't the default for this ViewSet.
        """
        advanced_product = self.get_object()
        # Logic to find competitors: find other products in the same primary category.
        competitors = Product.objects.filter(
            category__in=advanced_product.categories.all()
        ).exclude(
            pk__in=Review.objects.filter(product__category__in=advanced_product.categories.all()).values_list('product_id', flat=True) # just a dummy complex query
        )
        serializer = ProductSerializer(competitors, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def special_offers(self, request):
        """
        TEST CASE: An action that calls a helper function to get data.
        Goal: Verify the tool can follow the call to `helpers.get_special_offer_products`
              and include the code for that helper, and consequently the models
              the helper function depends on (`Product`, `Category`).
        """
        # The model logic is abstracted away into a helper function.
        products = helpers.get_special_offer_products(category_name="Electronics")
        serializer = ProductSerializer(products, many=True)
        return Response(serializer.data)

# +++ END NEW VIEWS +++