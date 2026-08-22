"""The MCP server: the allowed operations, and nothing else, over Streamable HTTP.

Built for one workspace and served on loopback. Every tool re-checks the policy
at the moment it runs rather than trusting that it was only registered because
it is allowed - a caller may name any tool it likes, and the boundary is the
check, not the listing. The refused operations in the policy are never
registered here, so they are absent from the listing and refused by name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trendrelay_api.integrations.mcp import context, policy, sops, writes

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

INSTRUCTIONS = (
    "This is a TrendRelay workspace. Help write copy for campaign posts that are "
    "queued without a caption yet.\n\n"
    "Before an action, use `list_sops` and `get_sop` to load the reviewed SOP "
    "that matches it. For campaign copy, start with action "
    "`campaigns.fill-needs-copy`, then use `list_posts_needing_copy` to see what "
    "needs writing and "
    "`get_post_context` for one post: it gives the video, the attached product and "
    "what it pays, every destination the post reaches and where a first comment or "
    "thread reply lands there, and the campaign's brief. Write with "
    "`write_caption`, `write_first_comment` and `write_thread` (or `write_post_copy` "
    "for several at once).\n\n"
    "You write drafts only. You cannot approve, publish, deploy, connect an account "
    "or sign in - those stay a person's decision in the app."
)


def _guard(operation: str) -> None:
    """Refuse an operation the policy does not allow, at call time.

    Every tool below is allowed, so this never fires in normal use. It is here
    because the boundary must be the check and not the registration: if a
    refused operation were ever wired up by mistake, it still would not run.
    """
    if not policy.is_allowed(operation):
        raise PermissionError(policy.refusal_reason(operation))


def build_server(workspace_id: str) -> FastMCP:
    """A Streamable-HTTP MCP server scoped to one workspace."""
    from mcp.server.fastmcp import FastMCP

    from trendrelay_api.integrations.mcp import service

    # Whatever the supervisor settled on, which is not always the configured
    # port: it hands this server's URL to the tunnel, so it picks a port it has
    # checked is free rather than one this process would discover is taken.
    mcp_port = service.port()
    server = FastMCP(
        name="TrendRelay",
        instructions=sops.server_instructions(INSTRUCTIONS),
        host="127.0.0.1",
        port=mcp_port,
        # Stateful Streamable HTTP, the mode a standard MCP client and the tunnel
        # expect: the session is negotiated on initialize and carried by header.
        # Stateless mode terminates the handshake a normal client makes.
        stateless_http=False,
    )

    # RFC 9728 resource metadata, at both paths a client looks for it. Answered
    # rather than left to 404 so a caller can tell "this resource advertises no
    # authorization server" from "this resource did not answer": tunnel-client
    # asks on every connection, and the difference is whether its warning
    # describes a deliberate configuration or a broken one. No authorization
    # server is named, because there is none - saying so is the point.
    async def _resource_metadata(_request):
        from starlette.responses import JSONResponse

        return JSONResponse(
            {
                "resource": f"http://127.0.0.1:{mcp_port}/mcp",
                "resource_name": "TrendRelay workspace",
            },
            headers={"Cache-Control": "no-store"},
        )

    _well_known = (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    )
    for _path in _well_known:
        server.custom_route(_path, methods=["GET"])(_resource_metadata)

    @server.resource(
        "trendrelay://mcp/guide",
        name="TrendRelay MCP guide",
        description="The canonical instructions for an AI connected to TrendRelay MCP.",
        mime_type="text/markdown",
    )
    def mcp_guide() -> str:
        return sops.mcp_guide_markdown()

    @server.resource(
        "trendrelay://mcp/control-tower",
        name="TrendRelay MCP control tower",
        description="Routes an intended action to the reviewed SOP and first live operation.",
        mime_type="text/markdown",
    )
    def mcp_control_tower() -> str:
        return sops.control_tower_markdown()

    @server.resource(
        "trendrelay://sops",
        name="TrendRelay SOP catalog",
        description="Reviewed operating procedures, indexed by the action being performed.",
        mime_type="text/markdown",
    )
    def sop_catalog() -> str:
        return sops.catalogue_markdown()

    @server.resource(
        "trendrelay://sops/{action}",
        name="TrendRelay SOP by action",
        description="The reviewed operating procedure for one canonical action or alias.",
        mime_type="text/markdown",
    )
    def sop_for_action(action: str) -> str:
        return sops.get_sop(action)["markdown"]

    def _call(operation: str, fn) -> Any:
        """Run one tool: refuse it if the policy does not allow it, then hand it
        a session. Reads and writes share this - a write commits its own session
        inside `fn` - so the boundary check has one home."""
        from trendrelay_api.database import SessionFactory

        _guard(operation)
        with SessionFactory() as session:
            return fn(session)

    @server.tool(
        name="list_sops",
        description=(
            "List reviewed TrendRelay SOPs by action. Call this before acting, then "
            "use get_sop with the matching canonical action."
        ),
    )
    def list_sops(action: str | None = None) -> list[dict[str, Any]]:
        _guard("list_sops")
        return sops.list_sops(action)

    @server.tool(
        name="get_sop",
        description=(
            "Read the reviewed SOP for an action, id or alias. This returns the "
            "procedure and metadata; use it before the related workspace tools."
        ),
    )
    def get_sop(action: str) -> dict[str, Any]:
        _guard("get_sop")
        return sops.get_sop(action)

    @server.tool(
        name="list_campaigns",
        description="Every campaign in the workspace, with how many posts still need a caption.",
    )
    def list_campaigns() -> list[dict[str, Any]]:
        return _call("list_campaigns", lambda s: context.list_campaigns(s, workspace_id))

    @server.tool(
        name="list_posts_needing_copy",
        description=(
            "Posts that are queued but have no caption written yet. Pass a "
            "campaign_id to narrow it. Each entry says what the clip is and what it sells."
        ),
    )
    def list_posts_needing_copy(campaign_id: str | None = None) -> list[dict[str, Any]]:
        return _call(
            "list_posts_needing_copy",
            lambda s: context.list_posts_needing_copy(s, workspace_id, campaign_id),
        )

    @server.tool(
        name="get_post_context",
        description=(
            "Everything needed to write one post's copy: the video, the attached "
            "product and its commission, every destination and where a follow-up "
            "lands there, the campaign brief, and any copy already written."
        ),
    )
    def get_post_context(item_id: str) -> dict[str, Any]:
        return _call(
            "get_post_context",
            lambda s: context.get_post_context(s, workspace_id, item_id),
        )

    @server.tool(
        name="get_campaign_config",
        description=(
            "A campaign's brief and posting configuration: objective, audience, "
            "markets, languages, disclosure line, product mode and cadence."
        ),
    )
    def get_campaign_config(campaign_id: str) -> dict[str, Any]:
        return _call(
            "get_campaign_config",
            lambda s: context.get_campaign_config(s, workspace_id, campaign_id),
        )

    @server.tool(
        name="write_caption",
        description=(
            "Write the caption (main post text) for a queued post. This is what "
            "flips it from needing copy to ready for the operator to approve."
        ),
    )
    def write_caption(
        item_id: str, caption: str, hashtags: list[str] | None = None
    ) -> dict[str, Any]:
        return _call(
            "write_caption",
            lambda s: writes.write_post_copy(
                s, workspace_id, item_id, caption=caption, hashtags=hashtags
            ),
        )

    @server.tool(
        name="write_first_comment",
        description=(
            "Write the first comment for a post - the reply that carries the "
            "affiliate link on networks that hide a link in the caption. Check "
            "get_post_context first for where it will land."
        ),
    )
    def write_first_comment(item_id: str, first_comment: str) -> dict[str, Any]:
        return _call(
            "write_first_comment",
            lambda s: writes.write_post_copy(
                s, workspace_id, item_id, first_comment=first_comment
            ),
        )

    @server.tool(
        name="write_thread",
        description=(
            "Write the thread replies for a post - the follow-up posts on Threads, "
            "X, Mastodon and Bluesky. Each list entry is one reply, in order."
        ),
    )
    def write_thread(item_id: str, replies: list[str]) -> dict[str, Any]:
        return _call(
            "write_thread",
            lambda s: writes.write_post_copy(s, workspace_id, item_id, thread=replies),
        )

    @server.tool(
        name="write_post_copy",
        description=(
            "Write several copy fields for a post at once - any of caption, "
            "first_comment, thread, hashtags, title, disclosure. A field left unset "
            "is not changed."
        ),
    )
    def write_post_copy(
        item_id: str,
        caption: str | None = None,
        first_comment: str | None = None,
        thread: list[str] | None = None,
        hashtags: list[str] | None = None,
        title: str | None = None,
        disclosure: str | None = None,
    ) -> dict[str, Any]:
        return _call(
            "write_post_copy",
            lambda s: writes.write_post_copy(
                s, workspace_id, item_id,
                caption=caption, first_comment=first_comment,
                thread=thread, hashtags=hashtags, title=title, disclosure=disclosure,
            ),
        )

    @server.tool(
        name="write_disclosure",
        description=(
            "Set this post's own disclosure line - the affiliate/ad disclosure it "
            "carries, overriding the campaign's default. An empty string clears the "
            "override and falls the post back to the campaign's disclosure."
        ),
    )
    def write_disclosure(item_id: str, disclosure: str) -> dict[str, Any]:
        return _call(
            "write_disclosure",
            lambda s: writes.write_post_copy(
                s, workspace_id, item_id, disclosure=disclosure
            ),
        )

    return server


def exposed_tool_names() -> list[str]:
    """The tool names a caller will be offered - the allowed operations."""
    return policy.allowed_operations()
