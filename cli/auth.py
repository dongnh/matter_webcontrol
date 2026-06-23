"""Authentication + bind-safety helpers."""

import hmac
import ipaddress
import logging
import sys
from typing import Optional


def check_api_key(provided: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time API-key comparison.

    Returns True when no key is configured (``expected`` falsy). Otherwise
    compares with hmac.compare_digest to avoid a timing side-channel (S6)."""
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_secure_bind(host: str, api_key: Optional[str], insecure: bool) -> None:
    """Refuse to bind to a non-loopback host without a key (S8).

    Exits the process unless ``--insecure`` is set. A loopback bind or a
    configured key is always fine."""
    if api_key or is_loopback(host):
        return
    if insecure:
        logging.warning(
            "Bound to non-loopback host %s without an API key (--insecure). "
            "Anyone on the network can control and commission devices.",
            host,
        )
        return
    logging.error(
        "Refusing to bind to non-loopback host %s without an API key. "
        "Set MATTER_SRV_KEY / --api-key, or pass --insecure to override.",
        host,
    )
    sys.exit(1)
