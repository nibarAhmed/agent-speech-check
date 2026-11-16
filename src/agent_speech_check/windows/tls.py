"""HTTPS that trusts what Windows trusts."""

from __future__ import annotations

import ssl
from functools import cache

import truststore


@cache
def context() -> ssl.SSLContext:
    """For every HTTPS request asc makes. Python's own check sees only the roots
    already in the Windows store; Windows fetches a missing one when it checks."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
