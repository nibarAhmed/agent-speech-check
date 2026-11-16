"""HTTPS that trusts what Windows trusts."""

from __future__ import annotations

import ssl
import urllib.request
from http.client import HTTPResponse


def urlopen(url: str | urllib.request.Request, timeout: float) -> HTTPResponse:
    """For every HTTPS request asc makes. Python's own check sees only the roots
    already in the Windows store; Windows fetches a missing one when it checks."""
    # Imported here: it adds about 30 ms to every asc command, and only downloads need it.
    import truststore  # noqa: PLC0415

    # A fresh context each time: truststore locks one context across its whole handshake.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    response: HTTPResponse = urllib.request.urlopen(url, timeout=timeout, context=context)  # noqa: S310 - URLs asc fixes
    return response
