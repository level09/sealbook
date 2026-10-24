"""Click commands, grouped by concern. `register_commands` registers everything here."""

from stk.anchor.cli import anchor, verify_anchors_cmd
from stk.cli.agent import (
    doctor_cmd,
    inspect_cmd,
    new_module,
    report,
    run,
    shell,
    smoke,
    verify,
)
from stk.cli.base import console, run_async
from stk.cli.database import (
    create_db,
    db,
)
from stk.cli.runs import worker
from stk.cli.users import (
    add_role,
    browser_token,
    cleanup_sessions,
    create,
    install,
    protect_mfa,
    reset,
)
from stk.discovery.cli import discover, enrich
from stk.maps.cli import basemap
from stk.monitor.seed import demo_seed
from stk.people.cli import erase_cmd
from stk.places.cli import places
from stk.speech.cli import speech_model

__all__ = [
    "add_role",
    "anchor",
    "basemap",
    "browser_token",
    "cleanup_sessions",
    "console",
    "create",
    "create_db",
    "db",
    "demo_seed",
    "discover",
    "erase_cmd",
    "enrich",
    "doctor_cmd",
    "inspect_cmd",
    "install",
    "new_module",
    "places",
    "protect_mfa",
    "report",
    "reset",
    "run",
    "run_async",
    "shell",
    "smoke",
    "speech_model",
    "verify",
    "verify_anchors_cmd",
    "worker",
]
