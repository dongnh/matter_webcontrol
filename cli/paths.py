"""Resolve a single data directory and derive every cache/storage path from it.

Why this exists (finding G1): caches and the Matter fabric ``--storage-path``
used bare CWD-relative paths, so launching from a different directory silently
forked all state and re-commissioned the whole fabric. Here we pin one data
directory at startup and hang every path off it.

Precedence (highest first):
  1. ``set_data_dir(path)`` — called once at startup from ``--data-dir``.
  2. ``MATTER_DATA_DIR`` environment variable.
  3. The current working directory (backwards compatible default).
"""

import os

ENV_VAR = "MATTER_DATA_DIR"

# Pinned once at startup by set_data_dir(); None means "resolve dynamically".
_data_dir: str | None = None


def _abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def set_data_dir(path: str | None) -> str:
    """Pin the data directory for the rest of the process and create it.

    Called once at startup. With no explicit ``path`` it pins the env var or the
    current working directory, so a later ``chdir`` can no longer fork state.
    """
    global _data_dir
    if path:
        _data_dir = _abs(path)
    elif os.environ.get(ENV_VAR):
        _data_dir = _abs(os.environ[ENV_VAR])
    else:
        _data_dir = _abs(os.getcwd())
    os.makedirs(_data_dir, exist_ok=True)
    return _data_dir


def resolve_data_dir() -> str:
    """Return the pinned data dir, or resolve it dynamically if not yet pinned."""
    if _data_dir is not None:
        return _data_dir
    env = os.environ.get(ENV_VAR)
    if env:
        return _abs(env)
    return _abs(os.getcwd())


def data_path(filename: str) -> str:
    return os.path.join(resolve_data_dir(), filename)


# -- Named cache / storage paths --------------------------------------------

def devices_cache() -> str:
    return data_path("devices_cache.txt")


def occupancy_cache() -> str:
    return data_path("occupancy_cache.json")


def names_cache() -> str:
    return data_path("names_cache.json")


def bridge_cache() -> str:
    return data_path("bridge_cache.json")


def schema_marker() -> str:
    return data_path("cache_schema.json")


def matter_storage() -> str:
    return data_path("matter_storage")
