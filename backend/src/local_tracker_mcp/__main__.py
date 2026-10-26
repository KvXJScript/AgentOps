"""`python -m local_tracker_mcp [--data-dir DIR]` — serve the local tracker over stdio."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio

from local_tracker_mcp import sandbox
from local_tracker_mcp.server import build_server
from local_tracker_mcp.store import IssueStore

DATA_DIR_ENV = "LOCAL_TRACKER_DATA_DIR"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local_tracker_mcp", description="Local demo MCP issue tracker (stdio).")
    parser.add_argument(
        "--data-dir", default=os.environ.get(DATA_DIR_ENV), help=f"storage directory (or ${DATA_DIR_ENV})"
    )
    args = parser.parse_args(argv)
    if not args.data_dir:
        parser.error(f"--data-dir or {DATA_DIR_ENV} is required")
    data_dir = Path(args.data_dir).resolve()
    store = IssueStore(data_dir)
    server = build_server(store)

    async def serve() -> None:
        # Installed inside the running loop: the event loop's own self-pipe socket already exists.
        sandbox.install(data_dir)
        await server.run_stdio_async()

    anyio.run(serve)
    return 0


if __name__ == "__main__":
    sys.exit(main())
