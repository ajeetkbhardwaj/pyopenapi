# FILE: reviews/serializers.py

from rest_framework import serializers
from .models import (
    Review, Product, Category,
    # +++ NEW IMPORTS +++
    Manufacturer, Supplier, AdvancedProduct, ProductLocation
    # +++ END NEW IMPORTS +++
)

class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['id', 'content', 'rating', 'created_at']

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name']

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']

# +++ NEW SERIALIZERS FOR TESTING +++

class ManufacturerSerializer(serializers.ModelSerializer):
    """TEST CASE: A simple serializer for a new model."""
    class Meta:
        model = Manufacturer
        fields = ['id', 'name', 'country']

class SupplierSerializer(serializers.ModelSerializer):
    """TEST CASE: A simple serializer for a model referenced via string."""
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'profile']

class AdvancedProductSerializer(serializers.ModelSerializer):
    """
    TEST CASE: A complex serializer for testing multiple issues.
    - References another serializer by string.
    - Uses a SerializerMethodField that depends on another model.
    """
    # TEST CASE: Reference another serializer via string to test lazy resolution.
    # Goal: Tool should find and include SupplierSerializer's code.
    supplier_details = serializers.CharField(source='supplier.name', read_only=True)
    
    # TEST CASE: A method field that requires analyzing serializer methods and their dependencies.
    # Goal: Tool should analyze 'get_location_count' and see that it uses the ProductLocation model.
    location_count = serializers.SerializerMethodField()

    class Meta:
        model = AdvancedProduct
        fields = [
            'sku', 'name', 'manufacturer', 'supplier_details', 'location_count', 'locations'
        ]

    def get_location_count(self, obj: AdvancedProduct) -> int:
        """Counts the number of locations for the product."""
        # This method's body references the 'locations' M2M field,
        # which implicitly depends on the ProductLocation model.
        return obj.locations.count()

# +++ END NEW SERIALIZERS +++