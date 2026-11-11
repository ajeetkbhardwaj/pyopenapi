# FILE: reviews/helpers.py

# +++ NEW IMPORTS FOR HELPER +++
from .models import Product, Category
# +++ END NEW IMPORTS +++

def filter_products_by_status(queryset, status: str):
    """
    Filters a product queryset by a given status.
    """
    if status:
        return queryset.filter(status=status)
    return queryset

# +++ NEW HELPER FUNCTION FOR TESTING +++
def get_special_offer_products(category_name: str):
    """
    A helper function that encapsulates logic involving other models.
    Called by AdvancedProductViewSet.special_offers action.
    This function depends on the `Category` and `Product` models.
    """
    try:
        # This line requires knowledge of the Category model.
        category = Category.objects.get(name__iexact=category_name)
        # This line requires knowledge of the Product model and its relation to Category.
        return Product.objects.filter(category=category, status='available')
    except Category.DoesNotExist:
        return Product.objects.none()
# +++ END NEW HELPER FUNCTION +++