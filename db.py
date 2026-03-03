"""Backwards-compatible re-export -- all logic now lives in storage.db."""

from storage.db import *  # noqa: F401,F403
from storage.db import init_db, get_db  # noqa: F401 -- explicit for common uses
