"""Information sources. Importing this package registers the built-in types."""

from tentacles.sources.base import (
    SOURCE_TYPES, FetchContext, FetchResult, Source, build_source, register, validate_source,
)
from tentacles.sources import imap, rss  # noqa: F401  (registers built-in types)

__all__ = ["SOURCE_TYPES", "FetchContext", "FetchResult", "Source", "build_source",
           "register", "validate_source"]
