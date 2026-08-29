"""Every mapped table, in one import.

`Base.metadata` only knows the tables whose modules have been imported, and
the API never needs them all at once - a request imports the two or three
models it touches, and that has always been enough. Alembic is the caller for
which it is not: `env.py` imported `models` alone, so autogenerate and
`alembic check` compared the database against thirteen of the thirty-seven
tables and reported the rest as drift. The first foreign key into a table it
had not heard of - `publication_plans.offer_id` to `product_offers` - failed
resolution outright, so `alembic check` did not run at all.

Imported for the side effect of registering, which is the one place in this
codebase where an unused import is the point. Discovered rather than listed:
every mapped module already ends in `_models`, and a list would be a thing to
forget to add to. `test_model_registry` holds the naming convention up, so a
module that breaks it fails a test instead of quietly going missing from a
migration.
"""

from __future__ import annotations

import importlib
import pkgutil

from trendrelay_api.models import Base

#: The suffix that marks a module as declaring mapped tables.
MODEL_MODULE_SUFFIX = "_models"


def model_module_names() -> list[str]:
    """Which modules of this package declare tables, by the naming rule."""
    import trendrelay_api

    return sorted(
        name
        for _, name, _ in pkgutil.iter_modules(trendrelay_api.__path__)
        if name.endswith(MODEL_MODULE_SUFFIX)
    )


def load_all_models() -> type[Base]:
    """Import every model module and hand back the now-complete metadata owner."""
    for name in model_module_names():
        importlib.import_module(f"trendrelay_api.{name}")
    return Base


__all__ = ["Base", "MODEL_MODULE_SUFFIX", "load_all_models", "model_module_names"]
