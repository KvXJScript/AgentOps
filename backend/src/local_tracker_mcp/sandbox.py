"""In-process guard: no network, no subprocesses, no filesystem writes outside the data directory.

Implemented with a PEP 578 audit hook, installed once the server is initialised (audit hooks cannot be
removed). Reads are allowed only inside the data directory and the interpreter's own import roots
(`sys.prefix`, `sys.base_prefix`, `sys.path`), so late imports keep working. This is defence in depth
for a demo server, not an OS-level sandbox: code that is already malicious could bypass it via ctypes.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_BLOCKED_PREFIXES = ("socket.", "subprocess.", "os.exec", "os.spawn", "os.posix_spawn", "ctypes.", "webbrowser.")
_BLOCKED_EVENTS = frozenset({"os.system", "os.fork", "os.forkpty", "urllib.Request", "http.client.connect"})
_PATH_EVENTS = frozenset(
    {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.chmod", "os.chown", "os.symlink", "os.link", "os.truncate"}
)


class SandboxViolation(PermissionError):
    pass


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _as_path(value: Any) -> Path | None:
    if isinstance(value, int) or value is None:
        return None  # an already-open file descriptor
    try:
        return Path(os.path.abspath(os.fsdecode(value)))  # no symlink resolution inside the hook
    except (TypeError, ValueError):
        return None


def make_hook(data_dir: Path) -> Callable[[str, tuple[Any, ...]], None]:
    writable = (data_dir.resolve(),)
    readable_candidates = {Path(sys.prefix), Path(sys.base_prefix), *(Path(p) for p in sys.path if p)}
    readable = (*writable, *(p.resolve() for p in readable_candidates if p.exists()))

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event in _BLOCKED_EVENTS or event.startswith(_BLOCKED_PREFIXES):
            raise SandboxViolation(f"local-tracker sandbox: {event} is not allowed")
        if event == "open":
            path = _as_path(args[0])
            if path is None:
                return
            mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = bool(flags & _WRITE_FLAGS) or any(c in mode for c in "wax+")
            if _within(path, writable) or (not writing and _within(path, readable)):
                return
            raise SandboxViolation("local-tracker sandbox: file access outside the data directory")
        if event in _PATH_EVENTS:
            for value in args[:2]:
                path = _as_path(value)
                if path is not None and not _within(path, writable):
                    raise SandboxViolation("local-tracker sandbox: file change outside the data directory")

    return hook


def install(data_dir: Path) -> None:
    sys.addaudithook(make_hook(data_dir))
