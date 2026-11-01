"""App-level dependency registry for objects created at startup.

Provides a lightweight module-level registry so that components called
from inside investigate() (which has no access to FastAPI's DI system)
can still reach the storage and meta_client singletons without a global
import cycle.

Usage:
  # At app startup (main.py lifespan):
  from .deps import register_app_deps
  register_app_deps(storage=storage, meta_client=meta_client)

  # Inside investigate.py:
  from .deps import get_storage_instance, get_meta_client_instance
  storage = get_storage_instance()   # raises if not registered
"""
from __future__ import annotations

from typing import Optional

_storage = None
_meta_client = None


def register_app_deps(storage, meta_client) -> None:
    """Register storage and meta_client at app startup."""
    global _storage, _meta_client
    _storage = storage
    _meta_client = meta_client


def get_storage_instance():
    """Return the registered storage backend. Raises RuntimeError if unregistered."""
    if _storage is None:
        raise RuntimeError("App deps not registered (call register_app_deps at startup)")
    return _storage


def get_meta_client_instance():
    """Return the registered meta_client. Raises RuntimeError if unregistered."""
    if _meta_client is None:
        raise RuntimeError("App deps not registered (call register_app_deps at startup)")
    return _meta_client


def is_registered() -> bool:
    """True when both storage and meta_client have been registered."""
    return _storage is not None and _meta_client is not None
