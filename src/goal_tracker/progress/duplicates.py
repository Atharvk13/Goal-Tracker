import hashlib
import unicodedata


def normalize_entry_text(text: str) -> str:
    """Normalize text for duplicate review without changing the original."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = " ".join(normalized.split())

    if not normalized:
        raise ValueError("Entry text cannot be blank.")

    return normalized


def calculate_entry_hash(text: str) -> str:
    """Calculate a stable fingerprint of normalized journal text."""
    normalized = normalize_entry_text(text)

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()