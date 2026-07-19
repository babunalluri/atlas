import re
import uuid

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])?$")


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def validate_slug(value: str) -> str:
    slug = value.strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError("Slug must be 2-64 chars of lowercase letters, digits, and hyphens")
    return slug
