"""What an MCP caller may invoke, decided here and nowhere else.

The boundary is the same one AdRelay arrived at: a model reaching the workspace
over a tunnel is not the operator sitting in front of it, so it may *read*, and
it may make *workspace writes that stay in the workspace* - a draft caption, a
first comment, a thread reply - but never a write that leaves: signing in,
connecting an account, approving a post, or publishing one.

Two rules hold however many operations are added:

- **Credentials and sessions are refused.** A provider token or an account
  login opens a window on someone's screen. It is not a remote caller's to do.
- **Approval and execution are refused.** An approval is only an approval if a
  person gives it. A caller that can propose a change and also approve it has
  not been governed - it has been given a longer arm.

Anything not named here is refused by `DEFAULT_REFUSAL`, so a new operation is
argued for rather than inherited. The classification is checked at call time in
the server, not trusted from the tool listing, because a caller may name any
tool it likes - including one it was never offered.
"""

from __future__ import annotations

from enum import StrEnum


class Access(StrEnum):
    """Why an operation is or is not reachable over MCP."""

    #: A read of the workspace. Always safe to expose.
    READ = "read"
    #: A write that stays in the workspace - a draft the operator still approves.
    WORKSPACE_WRITE = "workspace_write"
    #: Refused: it would sign in, mint a token, or connect a provider account.
    REFUSED_CREDENTIALS = "refused_credentials"
    #: Refused: it would approve, publish, deploy, or otherwise leave the app.
    REFUSED_EXECUTION = "refused_execution"


ALLOWED = frozenset({Access.READ, Access.WORKSPACE_WRITE})

#: The reason given when an operation is not named below. Refusal is the
#: default so that reaching a new capability is a decision someone makes here,
#: not something a tool inherits by existing.
DEFAULT_REFUSAL = (
    "Not named as reachable over MCP. The default is refusal, so a new operation "
    "is argued for here rather than inherited."
)

_REFUSAL_REASON: dict[Access, str] = {
    Access.REFUSED_CREDENTIALS: (
        "Refused over MCP: signing in and connecting accounts open a window on the "
        "operator's screen and are not a remote caller's to do."
    ),
    Access.REFUSED_EXECUTION: (
        "Refused over MCP: approving, publishing or deploying is a person's "
        "decision, made in the app. A model may write copy, never send it."
    ),
}

#: Every operation MCP knows about, classified on purpose. The reads and the
#: copy writes are the caption surface; the refused entries are named rather
#: than merely absent so a caller that asks for one by name is told why, and so
#: a test can prove the boundary holds against a caller that ignores the menu.
EXPOSURE: dict[str, Access] = {
    # --- Reads: context for writing copy -----------------------------------
    "list_campaigns": Access.READ,
    "list_posts_needing_copy": Access.READ,
    "get_post_context": Access.READ,
    "get_campaign_config": Access.READ,
    "list_sops": Access.READ,
    "get_sop": Access.READ,
    # --- Workspace writes: the copy itself, never its approval -------------
    "write_caption": Access.WORKSPACE_WRITE,
    "write_first_comment": Access.WORKSPACE_WRITE,
    "write_thread": Access.WORKSPACE_WRITE,
    "write_post_copy": Access.WORKSPACE_WRITE,
    "write_disclosure": Access.WORKSPACE_WRITE,
    "write_bio_hint": Access.WORKSPACE_WRITE,
    # --- Named, and refused ------------------------------------------------
    # Credentials and sessions.
    "sign_in": Access.REFUSED_CREDENTIALS,
    "connect_account": Access.REFUSED_CREDENTIALS,
    "save_engine_key": Access.REFUSED_CREDENTIALS,
    "connect_douyin": Access.REFUSED_CREDENTIALS,
    # Approval, execution, deployment.
    "approve_post": Access.REFUSED_EXECUTION,
    "publish_now": Access.REFUSED_EXECUTION,
    "deploy_campaign": Access.REFUSED_EXECUTION,
    "delete_post": Access.REFUSED_EXECUTION,
    "run_download": Access.REFUSED_EXECUTION,
    "save_autopilot": Access.REFUSED_EXECUTION,
}


def classify(operation: str) -> Access | None:
    """The named classification, or None when nothing was decided for it."""
    return EXPOSURE.get(operation)


def is_allowed(operation: str) -> bool:
    """Whether a caller may invoke this operation over MCP."""
    return EXPOSURE.get(operation) in ALLOWED


def refusal_reason(operation: str) -> str:
    """Why this operation is refused - its own reason, or the default."""
    access = EXPOSURE.get(operation)
    if access in ALLOWED:
        raise ValueError(f"{operation!r} is allowed, not refused.")
    if access is None:
        return DEFAULT_REFUSAL
    return _REFUSAL_REASON[access]


def allowed_operations() -> list[str]:
    """The operations a caller may invoke, sorted for a stable listing."""
    return sorted(name for name, access in EXPOSURE.items() if access in ALLOWED)
