"""Shared filesystem-safety helpers used by multiple CLI subcommands."""

from __future__ import annotations

import os
import stat
import tempfile
from types import TracebackType
from typing import Optional

import typer
from rich.console import Console


def refuse_overwrite(path: str, force: bool, console: Console) -> None:
    """Abort with exit code 1 if `path` already exists and `force` is not set."""
    if os.path.exists(path) and not force:
        console.print(
            f"[bold red]Error:[/bold red] Refusing to overwrite existing file: {path}. "
            "Use --force to overwrite it anyway."
        )
        raise typer.Exit(code=1)


class AtomicOutput:
    """Write a replacement beside its destination and publish it atomically."""

    def __init__(self, destination: str):
        self.destination = os.path.abspath(destination)
        directory = os.path.dirname(self.destination)
        basename = os.path.basename(self.destination)
        stem, suffix = os.path.splitext(basename)
        fd, self.path = tempfile.mkstemp(
            prefix=f".{stem}.",
            suffix=f"{suffix}.tmp",
            dir=directory,
        )
        os.close(fd)
        self._active = True

    def __enter__(self) -> str:
        return self.path

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        del exc_value, traceback
        if exc_type is None:
            try:
                self.commit()
            except BaseException:
                self.discard()
                raise
        else:
            self.discard()

    def commit(self) -> None:
        """Durably replace the destination with the completed temporary file."""
        if not self._active:
            return
        # Windows' _commit() rejects a read-only descriptor with EBADF, so
        # reopen read/write even though fsync itself does not modify contents.
        with open(self.path, "rb+") as f:
            os.fsync(f.fileno())
        if os.path.exists(self.destination):
            mode = stat.S_IMODE(os.stat(self.destination).st_mode)
            os.chmod(self.path, mode)
        os.replace(self.path, self.destination)
        self._active = False
        self._fsync_parent()

    def discard(self) -> None:
        """Remove an unpublished temporary file."""
        if not self._active:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass
        self._active = False

    def _fsync_parent(self) -> None:
        """Persist the directory entry where the platform permits it."""
        directory = os.path.dirname(self.destination)
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
