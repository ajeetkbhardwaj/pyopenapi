      
# core/features.py
from rest_framework.pagination import PageNumberPagination
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django_filters import rest_framework as filters # Alias is good practice
from rest_framework.filters import BaseFilterBackend
from .models import Product

# --- Custom Pagination ---
class CustomResultsSetPagination(PageNumberPagination):
    """
    A custom pagination class.
    """
    page_size = 25  # Different default page size
    page_size_query_param = 'page_count' # Custom query param name
    max_page_size = 200

    def get_paginated_response_schema(self, schema):
        # Example override for schema generation if needed
        # In a real scenario, you might customize the OpenAPI schema here
        base_schema = super().get_paginated_response_schema(schema)
        base_schema['properties']['custom_pagination_info'] = {
             'type': 'string',
             'example': 'Using CustomResultsSetPagination'
        }
        return base_schema

# --- Custom Authentication ---
class ApiKeyAuthentication(BaseAuthentication):
    """
    A simple custom authentication class checking for an 'X-API-KEY' header.
    """
    keyword = 'ApiKey' # Example keyword if needed for scheme

    def authenticate(self, request):
        api_key = request.META.get('HTTP_X_API_KEY')
        if not api_key:
            # No key provided
            return None # Important to return None if no credentials attempt

        # Dummy check: Key must be 'valid-secret-key'
        if api_key == 'valid-secret-key':
            # In real auth, you'd return (user, auth_info)
            # For testing, return a placeholder tuple
            print("ApiKey Authentication Successful (dummy)")
            # Returning a simple tuple to indicate success, NOT a real user object
            return (True, self.keyword)
        else:
            # Invalid key provided
            raise AuthenticationFailed('Invalid API Key.')

    def authenticate_header(self, request):
        # Used for WWW-Authenticate header on 401 responses
        return f'{self.keyword} realm="Protected API"'

# --- Custom FilterSet ---
class ProductFilterSet(filters.FilterSet):
    """
    A custom FilterSet for the Product model.
    """
    # Filter by category name (case-insensitive exact match)
    category_name = filters.CharFilter(field_name='category__name', lookup_expr='iexact')
    # Filter by product name containing text (case-insensitive)
    name_search = filters.CharFilter(field_name='name', lookup_expr='icontains')
    # Add a boolean filter example (assuming Product had an 'is_featured' field)
    # is_featured = filters.BooleanFilter(field_name='is_featured')

    class Meta:
        model = Product
        # Define the fields available for filtering via this FilterSet
        fields = ['category_name', 'name_search'] # 'is_featured' excluded unless model has it

# --- Custom Filter Backend ---
class RequestTimingFilterBackend(BaseFilterBackend):
    """
    A custom filter backend example that logs request timing (dummy).
    """
    def filter_queryset(self, request, queryset, view):
        # In a real backend, this would modify the queryset based on request params.
        # Here, we just add a dummy print statement.
        print(f"RequestTimingFilterBackend applied to view: {view.__class__.__name__}")
        # You could access request parameters here, e.g.
        # timing_param = request.query_params.get('log_timing')
        # if timing_param == 'true':
        #     # Add timing logic...
        #     pass
        return queryset # Return queryset (possibly filtered)

    