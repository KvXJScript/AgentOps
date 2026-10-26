"""`local-tracker`: a small deterministic issue tracker exposed as an MCP server over stdio.

This is a LOCAL DEMO server that ships with AgentOps, not a real external service. It stores issues
in one JSON file inside its data directory, has no network access and refuses filesystem access
outside that directory (see `sandbox.py`). Run it with `python -m local_tracker_mcp --data-dir DIR`
(or `LOCAL_TRACKER_DATA_DIR`); the AgentOps backend spawns it as a subprocess.
"""

SERVER_NAME = "local-tracker"
