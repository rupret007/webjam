"""Inject a real directory-sync failure after regular workspace publication."""
from contextlib import contextmanager
from unittest.mock import patch

from core import file_io


@contextmanager
def fail_primary_sync():
    real_sync = file_io._fsync_parent_directory
    calls = 0

    def sync(parent):
        nonlocal calls
        calls += 1
        # A regular save publishes its backup, then its primary.
        if calls == 2:
            raise OSError("directory sync failed after primary publication")
        return real_sync(parent)

    with patch.object(file_io, "_fsync_parent_directory", side_effect=sync):
        yield
    assert calls == 2


@contextmanager
def fail_create_sync():
    """Inject a directory-sync failure after new-workspace publication."""
    with patch.object(
        file_io,
        "_fsync_parent_directory",
        side_effect=OSError("directory sync failed after create publication"),
    ):
        yield
