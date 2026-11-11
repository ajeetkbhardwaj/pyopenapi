# FILE: reviews/models.py

from django.db import models

# Create your models here.
from django.db import models

class Category(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name

class Product(models.Model):
    name = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products')
    # +++ NEW FIELD FOR TESTING +++
    # Added to test a helper function used by a viewset
    status = models.CharField(max_length=20, default='available', choices=[('available', 'Available'), ('discontinued', 'Discontinued')])
    # +++ END NEW FIELD +++

    def __str__(self):
        return self.name

class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    content = models.TextField()
    rating = models.IntegerField(choices=[(i, i) for i in range(1, 6)])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Review for {self.product.name}"

# +++ NEW MODELS FOR TESTING COMPLEX RELATIONSHIPS AND REFERENCES +++

class Manufacturer(models.Model):
    """
    TEST CASE: A simple model to be used as a direct ForeignKey target.
    Goal: Verify the tool can fetch the code for this simple FK relationship.
    """
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class SupplierProfile(models.Model):
    """
    TEST CASE: A simple model to be used as a OneToOneField target.
    Goal: Verify OneToOne relationships are handled for context gathering.
    """
    contact_email = models.EmailField()
    website = models.URLField()
    
    def __str__(self):
        return self.contact_email

class Supplier(models.Model):
    """
    TEST CASE: A model referenced by a string in another model's ForeignKey.
    Also has a OneToOne relationship.
    Goal: Verify the tool can resolve string-based FKs and OneToOne fields.
    """
    name = models.CharField(max_length=150)
    profile = models.OneToOneField(SupplierProfile, on_delete=models.CASCADE, related_name="supplier")

    def __str__(self):
        return self.name

class ProductLocation(models.Model):
    """
    TEST CASE: A simple model to be used as a ManyToManyField target.
    Goal: Verify ManyToMany relationships are handled for context gathering.
    """
    name = models.CharField(max_length=100) # e.g., "Warehouse A", "Retail Store B"
    address = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class AdvancedProduct(models.Model):
    """
    TEST CASE HUB: A model with multiple complex relationships.
    - Direct ForeignKey to Manufacturer.
    - String-based ForeignKey to Supplier.
    - Direct ManyToManyField to ProductLocation.
    - ManyToManyField with a 'through' model to Category.
    Goal: A comprehensive test for fetching context from various relationship types.
    """
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)

    # Simple, direct FK relationship
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.SET_NULL, null=True, related_name="advanced_products")
    
    # String-based FK relationship to test resolving lazy relations
    supplier = models.ForeignKey('reviews.Supplier', on_delete=models.SET_NULL, null=True, related_name="advanced_products")

    # Simple M2M relationship
    locations = models.ManyToManyField(ProductLocation, related_name="advanced_products")

    # M2M relationship using a custom 'through' model
    categories = models.ManyToManyField(Category, through='ProductCategoryMembership', related_name="advanced_products")

    def __str__(self):
        return f"{self.name} ({self.sku})"

class ProductCategoryMembership(models.Model):
    """
    TEST CASE: An intermediary "through" model for a ManyToMany relationship.
    Goal: Test if the tool can navigate through an intermediary model to find the final related model (Category).
    """
    advanced_product = models.ForeignKey(AdvancedProduct, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    date_added = models.DateField(auto_now_add=True)

    class Meta:
        unique_together = ('advanced_product', 'category')

# +++ END NEW MODELS +++