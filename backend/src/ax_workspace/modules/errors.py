"""Shared public absence semantics; owning modules decide resource visibility."""

RESOURCE_NOT_FOUND_MESSAGE = "대상을 찾을 수 없습니다"


class ResourceNotFound(Exception):
    """A missing and an unreadable resource have the same public response."""
