"""More than one account per engine, by separating an engine from a login.

Until now `provider` did two jobs at once. It named the engine - which decides
what a post may be, which platforms exist, what the limits are - and it also
chose the credentials, of which there was exactly one set per engine. That is
fine until somebody has two Buffer logins, or a second Zernio workspace for a
different set of accounts, at which point the second one has nowhere to go.

Those two jobs are separated here:

* an **engine** is a `ProviderDefinition` - capabilities, static, three of them;
* a **connection** is one set of credentials for an engine, and there may be
  as many as somebody has logins.

The identity trick, which is what keeps this cheap
--------------------------------------------------
A connection's id defaults to its engine's id. `zernio` is the name of the
engine *and* of the first Zernio connection. Everything already written down -
every campaign destination, publishing slot and publication execution carrying
`provider="zernio"` - therefore keeps resolving with no migration and no
rewrite, because that value is now a perfectly good connection id.

Second and later connections take a suffixed id (`zernio-2`, or a slug from the
name somebody gives it) and their credentials live under suffixed environment
keys (`ZERNIO_API_KEY__2`). The first connection keeps the unsuffixed keys, so
an existing `.env` and the documentation that describes it stay correct.

Where this lives
----------------
The same `.env` store that already holds the keys, because that is where the
secrets are and splitting them across two stores would mean two places to look
when one is wrong. The registry itself - which connections exist, and what to
call them - is one JSON value under `PUBLISHING_CONNECTIONS`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from trendrelay_api.env_store import effective_value, write_env_values

#: The one key holding the registry. Its value is a JSON list of objects with
#: `id`, `provider` and `label`; the default connection of each engine is not
#: listed, because it exists by virtue of the engine existing.
REGISTRY_KEY = "PUBLISHING_CONNECTIONS"

#: Separates a credential key from the connection it belongs to. Two underscores
#: rather than one, because engine keys already contain single underscores
#: (`ZERNIO_API_KEY`) and a single one could not be told from part of the name.
KEY_SEPARATOR = "__"

#: A ceiling, not a quota. Somebody with forty logins is doing something this
#: was not built for, and a runaway loop writing connections into `.env` should
#: hit something before it fills the file.
MAX_CONNECTIONS_PER_ENGINE = 25

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Connection:
    """One set of credentials for one engine."""

    id: str
    provider: str
    label: str
    #: True for the connection that uses the engine's unsuffixed keys. There is
    #: exactly one per engine and it cannot be removed - removing it would
    #: orphan every destination that predates connections.
    is_default: bool

    def key_for(self, base_key: str) -> str:
        """Which environment key holds this connection's copy of a credential."""
        if self.is_default:
            return base_key
        return f"{base_key}{KEY_SEPARATOR}{self.suffix}"

    @property
    def suffix(self) -> str:
        """The part of the id that is not the engine name, upper-cased."""
        tail = self.id[len(self.provider):].lstrip("-")
        return _SLUG.sub("_", tail).strip("_").upper()


def slugify(value: str) -> str:
    return _SLUG.sub("-", value.strip().casefold()).strip("-")


def default_connection_id(provider_id: str) -> str:
    """The connection that predates connections, named after its engine."""
    return provider_id


def _stored() -> list[dict[str, Any]]:
    """The registry as written, with anything malformed dropped.

    Dropped rather than raised on: this is read on the way to showing somebody
    their accounts, and one bad hand-edit in `.env` should cost that connection
    rather than the whole page.
    """
    raw = effective_value(REGISTRY_KEY).strip()
    if not raw:
        return []
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, dict) and item.get("id")]


def connections(providers: dict[str, Any]) -> list[Connection]:
    """Every connection, default ones first, in a stable order.

    `providers` is passed in rather than imported so this module does not
    import the engine registry that will import it back.
    """
    found: list[Connection] = []
    for provider_id, definition in providers.items():
        found.append(Connection(
            id=default_connection_id(provider_id),
            provider=provider_id,
            label=getattr(definition, "label", provider_id),
            is_default=True,
        ))
    known = set(providers)
    for item in _stored():
        provider_id = str(item.get("provider") or "")
        identifier = str(item["id"])
        # An engine that no longer exists, or a row that collides with a
        # default: skipped, because resolving it would send credentials to the
        # wrong engine or shadow the connection everything else already uses.
        if provider_id not in known or identifier in {row.id for row in found}:
            continue
        found.append(Connection(
            id=identifier,
            provider=provider_id,
            label=str(item.get("label") or identifier),
            is_default=False,
        ))
    return found


def find(providers: dict[str, Any], connection_id: str | None) -> Connection | None:
    """The connection with this id, or None. Never raises on unknown input."""
    if not connection_id:
        return None
    for row in connections(providers):
        if row.id == connection_id:
            return row
    return None


def for_provider(providers: dict[str, Any], provider_id: str) -> list[Connection]:
    return [row for row in connections(providers) if row.provider == provider_id]


def next_id(providers: dict[str, Any], provider_id: str, label: str) -> str:
    """A free id for a new connection on this engine.

    Derived from the operator's own label where that yields something usable,
    because `buffer-brand-b` is worth more in a log line than `buffer-3`. Falls
    back to counting, and counts past anything already taken.
    """
    taken = {row.id for row in connections(providers)}
    slug = slugify(label)
    if slug and slug != provider_id:
        candidate = f"{provider_id}-{slug}"[:60]
        if candidate not in taken:
            return candidate
    index = 2
    while f"{provider_id}-{index}" in taken:
        index += 1
    return f"{provider_id}-{index}"


def add(providers: dict[str, Any], provider_id: str, label: str) -> Connection:
    """Register another login for an engine.

    No credentials here. Adding the connection and filling in its key are two
    steps on purpose: the second one needs somewhere to put the key, which is
    what this creates.
    """
    if provider_id not in providers:
        raise ValueError(f"There is no publishing engine called {provider_id!r}.")
    existing = for_provider(providers, provider_id)
    if len(existing) >= MAX_CONNECTIONS_PER_ENGINE:
        raise ValueError(
            f"{provider_id} already has {len(existing)} connections, which is the most "
            "TrendRelay keeps for one engine."
        )
    clean = label.strip() or f"{provider_id} {len(existing) + 1}"
    row = Connection(
        id=next_id(providers, provider_id, clean),
        provider=provider_id,
        label=clean[:80],
        is_default=False,
    )
    stored = _stored()
    stored.append({"id": row.id, "provider": row.provider, "label": row.label})
    write_env_values({REGISTRY_KEY: json.dumps(stored, ensure_ascii=False)})
    return row


def rename(providers: dict[str, Any], connection_id: str, label: str) -> Connection:
    row = find(providers, connection_id)
    if not row:
        raise ValueError(f"There is no connection called {connection_id!r}.")
    if row.is_default:
        # Its name is the engine's name, which belongs to the engine.
        raise ValueError("The first connection of an engine is named after the engine.")
    clean = label.strip()
    if not clean:
        raise ValueError("A connection needs a name.")
    stored = [
        {**item, "label": clean[:80]} if item.get("id") == connection_id else item
        for item in _stored()
    ]
    write_env_values({REGISTRY_KEY: json.dumps(stored, ensure_ascii=False)})
    return Connection(id=row.id, provider=row.provider, label=clean[:80], is_default=False)


def remove(providers: dict[str, Any], connection_id: str, credential_keys: tuple[str, ...]) -> None:
    """Forget a connection, and the credentials that were only for it.

    The keys are cleared as well as the registry row. A key left behind would
    be picked up again by the next connection to be given the same id, which is
    a surprising way to publish to somebody else's account.
    """
    row = find(providers, connection_id)
    if not row:
        raise ValueError(f"There is no connection called {connection_id!r}.")
    if row.is_default:
        raise ValueError(
            "An engine's first connection cannot be removed. Clear its key instead."
        )
    stored = [item for item in _stored() if item.get("id") != connection_id]
    write_env_values({
        REGISTRY_KEY: json.dumps(stored, ensure_ascii=False),
        **{row.key_for(key): "" for key in credential_keys},
    })


def payload(row: Connection, *, provider_label: str) -> dict[str, Any]:
    """What the interface is told about a connection."""
    return {
        "id": row.id,
        "provider": row.provider,
        "provider_label": provider_label,
        "label": row.label,
        "is_default": row.is_default,
    }
