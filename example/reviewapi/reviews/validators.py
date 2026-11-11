      
# core/validators.py
import re

_PROFANITIES = ["darn", "heck", "shucks"] # Keep it simple

def is_profane(text: str) -> bool:
    """
    Checks if the given text contains profanity (simple check).
    """
    if not text:
        return False
    text_lower = text.lower()
    for word in _PROFANITIES:
        # Use word boundaries for slightly better matching
        if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
            return True
    return False

    