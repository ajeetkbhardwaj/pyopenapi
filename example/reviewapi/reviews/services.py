# reviewapi/reviews/services.py
from . import utils # Import the utils module

from .models import Product

def get_active_products_count():
    """
    A simple service function that interacts with a model.
    This is used to test if the tool can trace a dependency from a view to a service function.
    """
    print("--- Service Layer: Counting active products... ---")
    # A simple, direct use of a model.
    # The tool must identify that `Product` is a model used here.
    return Product.objects.filter(description__icontains="active").count()

class NotificationService:
    """
    A simple service for sending notifications (dummy implementation).
    NOW CALLS A UTILITY FUNCTION.
    """
    def notify_admin(self, message: str):
        """
        Sends a notification message to the admin, using formatted message.
        """
        # --- CALL THE UTILITY FUNCTION (Level 2 Dependency) ---
        formatted_message = utils.format_admin_message(message)
        print(f"--- Sending Admin Notification ---")
        print(f"{formatted_message}") # Use the formatted message
        print(f"--- Notification Sent ---")


    def send_email(self, recipient: str, subject: str, body: str):
        """
        Sends an email (dummy).
        """
        print(f"--- Sending Email ---")
        print(f"To: {recipient}")
        print(f"Subject: {subject}")
        print(f"Body: {body}")
        print(f"--- Email Sent ---")