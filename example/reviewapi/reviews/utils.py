# reviewapi/reviews/utils.py
from datetime import datetime # Example standard library import

# +++ NEW FUNCTION +++
def format_admin_message(base_message: str) -> str:
    """Formats a message specifically for admin notifications."""
    # Maybe add a timestamp or prefix
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Formatting admin message at {timestamp}") # Add print for debugging trace
    return f"[ADMIN][{timestamp}] {base_message}"
# +++ END NEW FUNCTION +++

def user_has_special_access(user) -> bool:
    """
    Check if a user has special access privileges (dummy logic).
    """
    # Example: Superusers have special access
    return user and user.is_authenticated and user.is_superuser

def is_user_restricted(user) -> bool:
    """
    Check if a user has restricted access (dummy logic).
    """
    # Example: Anonymous users are restricted
    # Or check for a specific group or flag
    return not user or not user.is_authenticated

def log_action(message: str, level: str = "INFO"):
    """
    Logs an action (dummy implementation).
    """
    print(f"[{level}] {message}")