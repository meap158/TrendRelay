"""Typed MCP counterparts of the Campaign GUI's application operations.

This is an explicit allowlist, not an HTTP proxy or arbitrary function caller.
Each adapter uses the GUI endpoint's body model, role checks, workspace scope,
state rules and audit behavior. Mutation tools require confirmation because
even an ordinary edit can change an enabled autonomous campaign's next post.
"""

from __future__ import annotations

import inspect
from copy import copy
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, get_type_hints

from pydantic import BaseModel, Field, TypeAdapter, create_model
from pydantic.fields import FieldInfo
from starlette.requests import Request

from .access import confirm_external_action, require_access
from .library_attribution import SURFACES


@dataclass(frozen=True)
class Operation:
    endpoint: str
    module: str = "campaign_autopilot_api"
    mutation: bool = False
    external: bool = False


# All public Campaign API routes have a counterpart. Existing task-oriented
# tools remain convenient for drafts, Library IDs and slot selection.
OPERATIONS = {
    **{
        f"campaign_{name}": Operation(name)
        for name in (
            "read_autopilot",
            "campaign_day_slot_availability",
            "read_queue_item_context",
            "draft_offer_recommendations",
            "compose_queue_item",
            "offer_recommendations",
            "campaign_analytics",
            "preview_autopilot",
            "list_autopilot_executions",
            "campaign_conversation",
            "list_autopilot_exceptions",
            "list_campaign_products",
        )
    },
    **{
        f"campaign_{name}": Operation(name, mutation=True)
        for name in (
            "save_autopilot",
            "add_destination",
            "set_destination_placement",
            "set_destination_schedule",
            "set_destination_post_type",
            "remove_destination",
            "add_queue_item",
            "add_queue_assets",
            "assign_queue_item_slot",
            "update_queue_item",
            "remove_queue_item",
            "batch_queue_items",
            "set_conversation_state",
            "edit_autopilot_execution",
            "refreeze_autopilot_execution",
            "tag_campaign_products",
            "untag_campaign_product",
            "untag_campaign_products",
            "dismiss_autopilot_execution",
            "dismiss_autopilot_executions",
            "return_execution_to_rotation",
            "return_executions_to_rotation",
            "workspace_kill_switch",
        )
    },
    **{
        f"campaign_{name}": Operation(name, mutation=True, external=True)
        for name in (
            "publish_single_queue_item",
            "batch_publish_queue",
            "deploy_autopilot",
            "run_autopilot_now",
            "approve_autopilot_execution",
            "approve_autopilot_executions",
        )
    },
    "campaign_account_recommendations": Operation("account_recommendations", external=True),
    **{
        f"campaign_{name}": Operation(name, module="campaigns_api")
        for name in (
            "list_campaigns",
            "publication_calendar",
            "publication_plan",
        )
    },
    **{
        f"campaign_{name}": Operation(name, module="campaigns_api", mutation=True)
        for name in (
            "create_campaign",
            "reorder_campaigns",
            "update_campaign",
            "update_campaign_status",
            "duplicate_campaign",
            "create_publication_plan",
            "decide_publication_plan",
            "create_manual_package",
        )
    },
}


PUBLISH_OPERATIONS = {
    **{
        f"publish_{name}": Operation(name, module="publishing_api")
        for name in (
            "publishing_connection",
            "match_offers_for_draft",
            "publishing_capabilities",
            "list_publishing_connections",
            "publishing_slots",
        )
    },
    **{
        f"publish_{name}": Operation(name, module="publishing_api", mutation=True)
        for name in (
            "save_publishing_slots",
            "create_posting_preset",
            "update_posting_preset",
            "delete_posting_preset",
            "set_page_posting_preset",
        )
    },
    "publish_test_provider_credentials": Operation(
        "test_provider_credentials",
        module="publishing_api",
        external=True,
    ),
}

ATTRIBUTION_OPERATIONS = {
    f"{prefix}_{name}": Operation(name, module=module, mutation=confirmed, external=confirmed)
    for prefix, (module, reads, writes) in SURFACES.items()
    if prefix in {"attribution", "creative", "character"}
    for names, confirmed in ((reads, False), (writes, True))
    for name in names
}
LIBRARY_OPERATIONS = {
    f"{prefix}_{name}": Operation(name, module=module, mutation=confirmed, external=confirmed)
    for prefix, (module, reads, writes) in SURFACES.items()
    if prefix not in {"attribution", "creative", "character"}
    for names, confirmed in ((reads, False), (writes, True))
    for name in names
}
ALL_OPERATIONS = {
    **OPERATIONS,
    **PUBLISH_OPERATIONS,
    **ATTRIBUTION_OPERATIONS,
    **LIBRARY_OPERATIONS,
}


def endpoint(operation: Operation):
    from importlib import import_module

    module = import_module(f"trendrelay_api.{operation.module}")
    return getattr(module, operation.endpoint)


@lru_cache(maxsize=2)
def _library_body_schema(body_type):
    # Validate media shape after Library IDs resolve to actual media fields.
    return create_model(
        f"MCP{body_type.__name__}",
        **{
            name: (field.rebuild_annotation(), field)
            for name, field in body_type.model_fields.items()
        },
    )


def _public_signature(fn, operation: Operation | None = None) -> inspect.Signature:
    from trendrelay_api.integrations.publishing import MAX_CAROUSEL_IMAGES

    hints = get_type_hints(fn, include_extras=True)
    parameters = []
    for param in inspect.signature(fn).parameters.values():
        if param.name in {"workspace_id", "session", "user", "request", "background_tasks"}:
            continue
        annotation = hints[param.name]
        if (
            param.name == "body"
            and operation
            and operation.endpoint in {"add_queue_item", "update_queue_item"}
        ):
            annotation = _library_body_schema(annotation)
        default = param.default
        if isinstance(default, FieldInfo):
            field = copy(default)
            # GUI query aliases belong to HTTP, not Python keyword arguments.
            field.alias = field.validation_alias = field.serialization_alias = None
            annotation = Annotated[annotation, field]
            from pydantic_core import PydanticUndefined

            default = (
                inspect.Parameter.empty if field.default is PydanticUndefined else field.default
            )
        parameters.append(
            param.replace(
                annotation=annotation, default=default, kind=inspect.Parameter.KEYWORD_ONLY
            )
        )
    if operation and operation.endpoint in {"add_queue_item", "update_queue_item"}:
        parameters.append(
            inspect.Parameter(
                "asset_ids",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[list[str] | None, Field(max_length=MAX_CAROUSEL_IMAGES)],
            )
        )
    parameters.append(
        inspect.Parameter(
            "confirm_external_action",
            inspect.Parameter.KEYWORD_ONLY,
            default=False,
            annotation=bool,
        )
    )
    return inspect.Signature(parameters, return_annotation=dict[str, Any])


def invoke(session, workspace_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    operation = ALL_OPERATIONS[name]
    arguments = dict(arguments)
    confirmed = arguments.pop("confirm_external_action", False)
    if operation.mutation or operation.external:
        confirm_external_action(confirmed)
    actor = require_access(
        session,
        workspace_id,
        {"owner", "editor", "approver", "analyst"},
        governed=operation.mutation or operation.external,
    )
    fn = endpoint(operation)
    public = _public_signature(fn, operation)
    bound = public.bind(**arguments)
    bound.apply_defaults()
    arguments = {
        key: TypeAdapter(public.parameters[key].annotation).validate_python(value)
        for key, value in bound.arguments.items()
        if key != "confirm_external_action"
    }
    # Tools validate their typed input before this point. Revalidate here too
    # so direct callers cannot bypass the GUI's body validators.
    body = arguments.get("body")
    asset_ids = arguments.pop("asset_ids", None)
    fields = body.model_dump(exclude_unset=True) if isinstance(body, BaseModel) else body
    if asset_ids is not None:
        from .publishing import library_media

        fields = library_media(
            session,
            workspace_id,
            asset_ids,
            fields,
        )
    if operation.endpoint in {"add_queue_item", "update_queue_item"}:
        body_type = get_type_hints(fn)["body"]
        arguments["body"] = body = body_type.model_validate(fields)
    confirmation_fields = (
        {
            key: confirmed
            for key in ("confirm_external_action", "confirm")
            if key in type(body).model_fields
        }
        if isinstance(body, BaseModel)
        else {}
    )
    if confirmation_fields:
        arguments["body"] = type(body).model_validate(
            {
                **body.model_dump(exclude_unset=True),
                **confirmation_fields,
            }
        )
    elif isinstance(body, dict) and (operation.mutation or operation.external):
        arguments["body"] = {**body, "confirm_external_action": confirmed}
    values = {**arguments, "workspace_id": workspace_id, "session": session, "user": actor}
    tasks = None
    if "background_tasks" in inspect.signature(fn).parameters:
        from fastapi import BackgroundTasks

        tasks = values["background_tasks"] = BackgroundTasks()
    if "request" in inspect.signature(fn).parameters:
        values["request"] = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "query_string": b"",
                "headers": [(b"x-request-id", f"mcp:{name}".encode())],
            }
        )
    try:
        result = fn(**values)
        if operation.mutation:
            session.commit()
        if tasks and tasks.tasks:
            import anyio

            # The sync MCP handler runs in a worker thread. Match HTTP's
            # commit-before-background-work order and keep the returned job IDs.
            anyio.run(tasks)
        return result
    except Exception:
        session.rollback()
        raise


def install(server, workspace_id: str, call) -> None:
    for name, operation in ALL_OPERATIONS.items():
        fn = endpoint(operation)
        signature = _public_signature(fn, operation)

        # Defaults are captured in a separate factory so each registered tool
        # dispatches its own allowlisted operation, not the loop's final one.
        def adapter(tool_name, tool_signature):
            def dispatch(**arguments):
                return call(tool_name, lambda s: invoke(s, workspace_id, tool_name, arguments))

            dispatch.__name__ = tool_name
            dispatch.__signature__ = tool_signature
            dispatch.__annotations__ = {
                p.name: p.annotation for p in tool_signature.parameters.values()
            } | {"return": dict[str, Any]}
            return dispatch

        description = inspect.getdoc(fn) or operation.endpoint.replace("_", " ")
        procedure = {
            "publishing_api": "publishing.inspect-and-preview",
            "attribution_api": "attribution.manage-library-data",
            "product_creative_api": "attribution.manage-library-data",
            "media_library_api": "library.manage-media",
            "characters_api": "attribution.manage-library-data",
            **{
                module: "library.manage-media"
                for module in (
                    "autocut_api",
                    "storytelling_api",
                    "carousels_api",
                    "hook_overlays_api",
                    "creation_drafts_api",
                    "production_api",
                )
            },
        }.get(operation.module, "campaigns.manage-social-posting")
        description += (
            " Uses the GUI operation and its workspace/role/state validation. "
            f"Read {procedure} before acting. "
        )
        if operation.endpoint in {"add_queue_item", "update_queue_item"}:
            description += (
                "Optional asset_ids selects ordered Library media using the latest cut; omit paths "
                "in body when using it. One video or a list of images, never both. "
            )
        if operation.mutation or operation.external:
            description += (
                "Requires explicit user consent and confirm_external_action=true. "
                "This may affect a live autonomous campaign or contact a provider."
            )
        else:
            description += "Read or preview only; does not deliver a post."
        server.add_tool(adapter(name, signature), name=name, description=description)
