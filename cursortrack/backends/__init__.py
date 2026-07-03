"""Backend resolution and retrieval interface."""

from __future__ import annotations

import sys

from cursortrack.backends.base import InputBackend
from cursortrack.backends.linux import LinuxBackend
from cursortrack.backends.macos import MacOSBackend
from cursortrack.backends.windows import WindowsBackend

BACKEND_CLASSES: dict[str, type[InputBackend]] = {
    "win": WindowsBackend,
    "linux": LinuxBackend,
    "macos": MacOSBackend,
}


def resolve_backend_name(name: str = "auto") -> str:
    """Resolve a backend name, mapping 'auto' to the detected operating system.

    Args:
        name: Name of the backend ('win', 'linux', 'macos', or 'auto').
              If 'auto', the current operating system is detected.

    Returns:
        A concrete backend key present in BACKEND_CLASSES.
    """
    resolved_name = name.lower()
    if resolved_name == "auto":
        if sys.platform.startswith("win"):
            resolved_name = "win"
        elif sys.platform.startswith("linux"):
            resolved_name = "linux"
        elif sys.platform.startswith("darwin"):
            resolved_name = "macos"
        else:
            raise RuntimeError(
                f"Unsupported platform: {sys.platform}. "
                "Specify a custom backend using the --backend option or programmatically."
            )

    if resolved_name not in BACKEND_CLASSES:
        raise ValueError(
            f"Unknown backend name: {name}. "
            f"Valid choices are: 'auto', {', '.join(repr(k) for k in BACKEND_CLASSES)}"
        )

    return resolved_name


def get_backend(name: str = "auto") -> InputBackend:
    """Instantiate and return the appropriate mouse tracking backend.

    Args:
        name: Name of the backend ('win', 'linux', 'macos', or 'auto').
              If 'auto', the current operating system is detected.
    """
    resolved_name = resolve_backend_name(name)
    return BACKEND_CLASSES[resolved_name]()
