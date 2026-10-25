"""What every route adapter shares: the error it raises and the shape it returns."""
from __future__ import annotations


class AdapterError(ValueError):
    """The body is not something this adapter can read (not a feed, not a sitemap, not JSON).
    The poller records it as a parse error against the route and tries the next route."""
