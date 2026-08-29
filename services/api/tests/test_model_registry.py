"""Alembic has to be able to see every table.

`Base.metadata` knows only what has been imported. `env.py` imported `models`
alone, which is thirteen of the thirty-seven tables, so autogenerate compared
the database against a third of itself and called the rest drift - and the
first foreign key into a table it had not heard of, `publication_plans.offer_id`
to `product_offers`, failed to resolve at all. `alembic check` did not run.

The registry discovers modules by the `_models` suffix rather than listing
them, so these tests hold the convention up: a module that declares tables and
does not follow it goes missing from a migration silently, which is the same
failure wearing a different hat.
"""

from __future__ import annotations

import pkgutil


def test_every_model_module_is_loaded():
    """The registry finds each one, and loading them registers their tables."""
    from trendrelay_api.model_registry import load_all_models, model_module_names

    names = model_module_names()
    assert names, "no model modules discovered at all"

    metadata = load_all_models().metadata
    # The table whose absence broke `alembic check`, named so a regression
    # points straight at the symptom that was actually observed.
    assert "product_offers" in metadata.tables
    assert len(metadata.tables) > 30


def test_no_module_declares_tables_outside_the_naming_rule():
    """A model module that is not named `*_models` is invisible to the registry.

    Asserted by importing every module of the package and checking that the
    metadata does not grow: if it does, some module declares mapped tables
    under a name the discovery rule does not match.
    """
    import importlib

    import trendrelay_api
    from trendrelay_api.model_registry import (
        MODEL_MODULE_SUFFIX,
        load_all_models,
        model_module_names,
    )

    metadata = load_all_models().metadata
    known = set(metadata.tables)
    discovered = set(model_module_names())

    missed: list[str] = []
    for _, name, is_package in pkgutil.iter_modules(trendrelay_api.__path__):
        if is_package or name in discovered or name.startswith("_"):
            continue
        try:
            importlib.import_module(f"trendrelay_api.{name}")
        except Exception:
            # A module that will not import on its own is not this test's
            # business; it declares nothing until something imports it.
            continue
        grew = set(metadata.tables) - known
        if grew:
            missed.append(f"{name} declares {sorted(grew)}")
            known |= grew

    assert not missed, (
        "these modules declare mapped tables but are not named "
        f"`*{MODEL_MODULE_SUFFIX}`, so migrations cannot see them: {missed}"
    )
