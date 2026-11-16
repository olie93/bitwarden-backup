"""Minimal backup lifecycle events for external freshness monitoring."""

import json
from contextlib import suppress


def emit_backup_terminal(status: str, completed_at: str) -> None:
    """Emit a terminal result without vault data, job output or credentials."""
    if status not in {"completed", "failed", "cancelled"}:
        return
    event = {
        "event": "vaultsync_backup_terminal",
        "status": status,
        "completed_at": completed_at,
    }
    # A broken output stream must never change a persisted backup outcome.
    with suppress(OSError):
        print(json.dumps(event), flush=True)  # noqa: T201 -- deliberate machine-readable stdout event
