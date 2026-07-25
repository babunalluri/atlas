"""Helpers for cloning catalog entities with unique slugs."""

from collections.abc import Awaitable, Callable


def copy_name(name: str) -> str:
    return f"{name} (copy)"


async def unique_copy_slug(
    base_slug: str,
    is_taken: Callable[[str], Awaitable[bool]],
    *,
    max_len: int = 64,
) -> str:
    """Allocate ``{slug}-copy`` / ``{slug}-copy-N`` within ``max_len``."""
    for index in range(1, 100):
        suffix = "-copy" if index == 1 else f"-copy-{index}"
        root = base_slug[: max(1, max_len - len(suffix))].rstrip("-")
        candidate = f"{root}{suffix}"
        if not await is_taken(candidate):
            return candidate
    raise ValueError("Unable to allocate a unique slug for the copy")
