"""Rename a publishing connection's id, and everything that points at it.

A connection's id is minted from its label when it is created and never
rewritten, because four separate things reference it: the registry in `.env`,
the environment key holding its credential, every campaign destination, and
every publication it has ever made. Rename the label afterwards and the id stays
a fossil of what the label used to be.

That is normally invisible - the id is internal - but it surfaces wherever a
label is missing, and `zernio-zernio-2` reads as a bug rather than as a second
Zernio login named "2".

    python scripts/rename_connection_id.py zernio-zernio-2 zernio-2

Run it with nothing publishing. Between the first write and the last there is a
moment when a destination points at an id the registry no longer offers, and a
post attempted in that moment would fail to find its account.

The credential is moved by renaming the key around it. Its value is never read,
and nothing but ids is printed.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = (
    ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
)

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

# The app's own reader and writer, rather than a second implementation of them.
# A `.env` value is escaped on the way in, and a registry parsed by a lookalike
# that got the escaping wrong would be rewritten wrong - silently, since what it
# wrote would still look like JSON.
from trendrelay_api.env_store import _quote, _unquote  # noqa: E402
from trendrelay_api.publishing_connections import Connection  # noqa: E402

ENV = ROOT / ".env"
DB = ROOT / ".data" / "trendrelay.db"
REGISTRY_KEY = "PUBLISHING_CONNECTIONS"

#: Columns holding a connection id. Each is somewhere a post would be orphaned.
REFERENCES = (
    ("publication_executions", "provider"),
    ("campaign_destinations", "provider"),
)

#: Columns holding a JSON document that names a connection inside it.
#:
#: A queued publish carries the account it is for in its payload, so one left
#: behind would wake up addressed to a login that no longer exists. The finished
#: ones are rewritten with it: they are the record a retry or an investigation
#: reads back, and a record pointing at nothing is worse than a rewritten one.
EMBEDDED = (
    ("durable_jobs", "payload"),
    ("durable_jobs", "result"),
)


def credential_tail(connection_id: str, provider: str) -> str:
    """What this id appends to a credential key, as the engine derives it."""
    connection = Connection(
        id=connection_id, provider=provider, label="", is_default=False
    )
    return connection.key_for("")


def main(old: str, new: str) -> int:
    if old == new:
        print("Those are the same id.")
        return 1
    if not ENV.is_file() or not DB.is_file():
        print("Run this in a workspace that has a .env and a database.")
        return 1

    lines = ENV.read_text(encoding="utf-8-sig").splitlines()
    registry_at = next(
        (i for i, line in enumerate(lines) if line.startswith(f"{REGISTRY_KEY}=")), None
    )
    if registry_at is None:
        print(f"No {REGISTRY_KEY} in .env; there is nothing to rename.")
        return 1

    connections = json.loads(_unquote(lines[registry_at].split("=", 1)[1].strip()))
    found = next((item for item in connections if item.get("id") == old), None)
    already = next((item for item in connections if item.get("id") == new), None)
    if found is not None and already is not None:
        print(f"{new!r} is already taken by another connection.")
        return 1
    if found is None and already is None:
        print(f"No connection called {old!r}.")
        return 1
    provider = str((found or already or {}).get("provider") or "")
    if not new.startswith(f"{provider}-"):
        print(f"A {provider} connection's id has to start with {provider!r}.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, DB.with_name(f"{DB.name}.backup-rename-{stamp}"))
    if found is not None:
        shutil.copy2(ENV, ROOT / f".env.backup-{stamp}")
    print(f"backed up the database{' and .env' if found else ''} ({stamp})")

    # Idempotent, so a sweep interrupted partway can simply be run again: with
    # the registry already moved there is nothing here to do, and the references
    # below are matched on the old id and so pass over what has already moved.
    if found is None:
        print("registry already names it; sweeping what still points at the old id")
    else:
        found["id"] = new
        lines[registry_at] = f"{REGISTRY_KEY}={_quote(json.dumps(connections))}"
        old_tail = credential_tail(old, provider)
        new_tail = credential_tail(new, provider)
        renamed = 0
        for i, line in enumerate(lines):
            name, separator, value = line.partition("=")
            if separator and name.endswith(old_tail):
                # Only the key moves. The value is carried across as the text it
                # already was, so the secret is neither parsed nor printed.
                lines[i] = f"{name[: -len(old_tail)]}{new_tail}={value}"
                renamed += 1
        ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"registry rewritten; {renamed} credential key(s) renamed")

    db = sqlite3.connect(DB)
    with db:
        for table, column in REFERENCES:
            moved = db.execute(
                f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old)
            ).rowcount
            print(f"  {table}.{column}: {moved} row(s)")
        for table, column in EMBEDDED:
            # Quoted, so the id is only replaced where it is a whole JSON value.
            # Unquoted it is a prefix of any longer id and would corrupt them.
            moved = db.execute(
                f'UPDATE {table} SET {column} = replace({column}, ?, ?) '
                f"WHERE {column} LIKE ?",
                (f'"{old}"', f'"{new}"', f'%"{old}"%'),
            ).rowcount
            print(f"  {table}.{column}: {moved} document(s)")
    db.close()
    print(f"{old} -> {new}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
