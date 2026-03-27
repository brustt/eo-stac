class EostacError(Exception):
    """Base exception for all eostac errors."""


class SearchError(EostacError):
    """Raised when a STAC search fails."""


class DownloadError(EostacError):
    """Raised when an asset download fails."""


class StorageError(EostacError):
    """Raised when a path or storage operation fails."""
