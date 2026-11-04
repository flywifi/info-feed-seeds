"""What every route adapter shares: the error it raises and the shape it returns."""
from __future__ import annotations

from xml.parsers import expat


class AdapterError(ValueError):
    """The body is not something this adapter can read (not a feed, not a sitemap, not JSON).
    The poller records it as a parse error against the route and tries the next route."""


class DocumentRefused(AdapterError):
    """The document declares a DTD or entities; it is refused before it is parsed."""


def refuse_dtd(data: bytes, *, any_doctype: bool) -> None:
    """Raise DocumentRefused for an entity declaration, a DOCTYPE with an internal subset, or (with
    any_doctype) any DOCTYPE at all. The check runs inside an XML parser on the decoded document, so a
    UTF-16 body or a long leading comment cannot hide a declaration. A body that is not well-formed is
    left for the caller's own parser to report."""
    parser = expat.ParserCreate()

    def doctype(name, system_id, public_id, has_internal_subset):
        if any_doctype or has_internal_subset:
            raise DocumentRefused("document declares a DTD or entities; refused")

    def entity(*_):
        raise DocumentRefused("document declares a DTD or entities; refused")

    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = entity
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        return
