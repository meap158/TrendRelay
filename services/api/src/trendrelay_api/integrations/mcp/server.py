"""The MCP server: the allowed operations, and nothing else, over Streamable HTTP.

Built for one workspace and served on loopback. Every tool re-checks the policy
at the moment it runs rather than trusting that it was only registered because
it is allowed - a caller may name any tool it likes, and the boundary is the
check, not the listing. The refused operations in the policy are never
registered here, so they are absent from the listing and refused by name.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from trendrelay_api.integrations.mcp import context, intake, policy, schedules, sops, writes

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class OpenAIFile(BaseModel):
    """The exact ChatGPT file-parameter object, not an arbitrary dictionary.

    OpenAI's plugin scanner requires all four fields to be declared, exactly
    the two transferable identifiers to be required, and no undeclared keys.
    Keeping this as a named model also makes FastMCP emit one reusable `$defs`
    schema for both upload tools.
    """

    model_config = ConfigDict(extra="forbid")

    download_url: str
    file_id: str
    # OpenAI requires these to be declared as strings but not required. A
    # non-optional annotation with a None default produces exactly that JSON
    # Schema: `type: string`, absent from `required`, defaulting when omitted.
    mime_type: str = None  # type: ignore[assignment]
    file_name: str = None  # type: ignore[assignment]


def _file_value(value: OpenAIFile | None) -> dict[str, Any] | None:
    return value.model_dump(exclude_none=True) if value is not None else None

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
    "To add a new post: `list_library_assets` finds media the workspace already "
    "holds - look before uploading, because re-importing a file the Library "
    "has records provenance that is not true. `upload_media` brings in one "
    "that is new, video or image (attach it in chat, pass media_url, or send "
    "media_base64 when the client cannot materialize its private file); "
    "`upload_image` is the same thing for pictures only. "
    "`get_import_status` reports when its "
    "asset_id exists, and `create_campaign_post` proposes a post from Library "
    "assets into a campaign. The two halves can arrive in either order: create "
    "with no assets to draft the words first, then `set_post_media` attaches "
    "the clip once its upload lands - or media first and copy later; the "
    "campaign skips a half-finished post with a note until it is whole. A post "
    "you create arrives as a draft outside the "
    "rotation - the operator promotes it in the app - so say it is waiting for "
    "them.\n\n"
    "For when things post, `list_posting_times` gives the workspace's times, the "
    "presets available and which pages are assigned one; "
    "`get_campaign_posting_times` says what each of a campaign's accounts posts at "
    "and which level decided it. To change a rhythm, `create_posting_preset` names "
    "a set of times without putting it in front of anything, then "
    "`set_campaign_posting_times`, `set_page_posting_times` or "
    "`set_workspace_posting_times` puts it into effect. Times are HH:MM on the "
    "workspace's own clock, not UTC.\n\n"
    "You write drafts and schedules only. You cannot approve, publish, deploy, "
    "connect an account or sign in - those stay a person's decision in the app. "
    "Changing a schedule moves when already-approved posts go out; it never sends "
    "one, so say what you are about to reschedule before you do it."
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
        description=(
            "Every campaign in the workspace, with how many posts still need "
            "a caption and whether its accounts can post a picture carousel "
            "(`accepts_carousel`). Check that before uploading images for one."
        ),
    )
    def list_campaigns() -> list[dict[str, Any]]:
        return _call("list_campaigns", lambda s: context.list_campaigns(s, workspace_id))

    @server.tool(
        name="list_published_posts",
        description=(
            "Posts that already went out, ranked by the interactions they "
            "earned - likes, comments, shares and saves - with the copy that "
            "earned them: caption, first comment, thread, the products it "
            "linked and a permalink. Use it to write from what worked rather "
            "than from the brief alone. Narrow with campaign_id or platform; "
            "sort_by takes interactions (default), views, likes, comments, "
            "shares, saves or watch_seconds. A post nobody has read back yet "
            "says measured: false and carries no figures - that is not the "
            "same as a post that got nothing, and it never outranks one."
        ),
    )
    def list_published_posts(
        campaign_id: str | None = None,
        platform: str | None = None,
        sort_by: str = "interactions",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return _call(
            "list_published_posts",
            lambda s: context.list_published_posts(
                s, workspace_id, campaign_id, platform, sort_by, limit,
            ),
        )
    @server.tool(
        name="list_posts_needing_copy",
        description=(
            "Posts that are queued but have no caption written yet. Pass a "
            "campaign_id to narrow it. Each entry says what the clip is, how "
            "long it runs, and what it sells."
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
            "Everything needed to write one post's copy: the video and how long "
            "it runs, the attached product and its commission, every destination "
            "and where a follow-up lands there, the campaign brief, and any copy "
            "already written."
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
            "first_comment, thread, hashtags, title, disclosure, bio_hint, "
            "topic, or post_types. `topic` is Threads' single topic tag (no "
            "leading #, up to 50 characters, no full stop or ampersand), "
            "delivered only on Threads through an engine that can attach it; "
            "an empty string clears it. `post_types` maps campaign destination "
            "ids to format ids (for example reel, story or short); omitted "
            "accounts inherit their campaign default. A field left unset is "
            "not changed."
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
        bio_hint: str | None = None,
        topic: str | None = None,
        post_types: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return _call(
            "write_post_copy",
            lambda s: writes.write_post_copy(
                s, workspace_id, item_id,
                caption=caption, first_comment=first_comment,
                thread=thread, hashtags=hashtags, title=title, disclosure=disclosure,
                bio_hint=bio_hint, topic=topic, post_types=post_types,
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

    @server.tool(
        name="write_bio_hint",
        description=(
            "Set this post's own profile-bio hint - the words the campaign turns "
            "into a bio line for networks that carry the link there, overriding the "
            "campaign's default. Write the words only; the campaign adds the link. "
            "An empty string clears the override back to the campaign's."
        ),
    )
    def write_bio_hint(item_id: str, bio_hint: str) -> dict[str, Any]:
        return _call(
            "write_bio_hint",
            lambda s: writes.write_post_copy(
                s, workspace_id, item_id, bio_hint=bio_hint
            ),
        )

    # --- media in, and a post proposed -------------------------------------
    # See the note in `policy`: the upload is the operator's own ingest
    # pipeline, and a created post is a draft only a person promotes.

    @server.tool(
        name="list_library_assets",
        description=(
            "Find media already in the media library, newest first, to build a "
            "post from without uploading anything. `query` searches titles and "
            "captions, `kind` narrows to 'image', 'video' or 'audio', and "
            "`collected_within_days` limits it to what arrived recently. Page "
            "with `offset`; `more` says whether any are left. Returns asset "
            "ids, which is what `create_campaign_post` takes - never file "
            "paths."
        ),
    )
    def list_library_assets(
        query: str | None = None,
        kind: str | None = None,
        collected_within_days: int | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        return _call(
            "list_library_assets",
            lambda s: intake.list_library_assets(
                s, workspace_id, query=query, kind=kind,
                collected_within_days=collected_within_days,
                limit=limit, offset=offset,
            ),
        )

    @server.tool(
        name="upload_image",
        title="Upload image to TrendRelay",
        description=(
            "Bring one image into the media library, to post later. Attach the "
            "image in chat (it arrives as the `image` file parameter), pass "
            "a direct public https `image_url`, or - for an image you "
            "generated yourself and cannot give a public address - send the "
            "bytes as `image_base64` (standard base64, or a "
            "data:<type>;base64,<data> URL). Give it a `title` a person "
            "will recognise; `source_url`, `creator`, `caption` and `platform` "
            "(the network it genuinely came from, if any) record where it came "
            "from. The original bytes and full resolution are preserved; a "
            "separate thumbnail never replaces them. Images normally return "
            "asset_id in this call, so do not upload a smaller retry. It lands "
            "in the media library like any import, under the "
            "'mcp-upload' source. Returns an asset_id at once for an image the "
            "library already holds, otherwise a job_id to poll with "
            "get_import_status."
        ),
        # The ChatGPT connector convention: declaring the parameter here makes
        # a chat attachment arrive as {"file_id", "download_url"} in `image`.
        # Other MCP clients ignore this meta and pass image_url instead.
        meta={"openai/fileParams": ["image"]},
    )
    def upload_image(
        image: OpenAIFile = None,  # type: ignore[assignment]
        image_url: str | None = None,
        title: str = "",
        caption: str | None = None,
        creator: str | None = None,
        source_url: str | None = None,
        platform: str | None = None,
        image_base64: str | None = None,
    ) -> dict[str, Any]:
        _guard("upload_image")
        return intake.upload_image(
            workspace_id,
            image=_file_value(image),
            image_url=image_url,
            title=title,
            caption=caption,
            creator=creator,
            source_url=source_url,
            platform=platform,
            image_base64=image_base64,
        )

    @server.tool(
        name="upload_media",
        title="Upload media to TrendRelay",
        description=(
            "Bring one video or image into the media library, to post later. "
            "Three ways in, in order of preference: attach the file in chat "
            "(it arrives as the `media` file parameter); pass a direct public "
            "https `media_url`; or, for a file you generated yourself and "
            "cannot give a public address, send the bytes as `media_base64` "
            "(standard base64, or a data:<type>;base64,<data> URL). The "
            "base64 route needs no URL and no file registry, so use it when "
            "the client cannot turn its own file reference into a link. "
            "Accepts "
            "mp4, mov, webm and mkv video up to 512 MB, and jpeg, png and webp "
            "images up to 25 MB. Give it a `title` a person will recognise; `source_url`, "
            "`creator`, `caption` and `platform` record where it came from. "
            "Images are kept at their original resolution and normally return "
            "an asset_id in this call; do not downscale or recompress one just "
            "to make the import faster. "
            "It lands in the media library under the 'mcp-upload' source. "
            "Returns an asset_id at once for a file the library already "
            "holds, otherwise a job_id to poll with get_import_status."
        ),
        meta={"openai/fileParams": ["media"]},
    )
    def upload_media(
        media: OpenAIFile = None,  # type: ignore[assignment]
        media_url: str | None = None,
        title: str = "",
        caption: str | None = None,
        creator: str | None = None,
        source_url: str | None = None,
        platform: str | None = None,
        media_base64: str | None = None,
    ) -> dict[str, Any]:
        _guard("upload_media")
        return intake.upload_media(
            workspace_id,
            media=_file_value(media),
            media_url=media_url,
            title=title,
            caption=caption,
            creator=creator,
            source_url=source_url,
            platform=platform,
            media_base64=media_base64,
        )

    @server.tool(
        name="get_import_status",
        description=(
            "How upload_media and upload_image imports are going. Pass every "
            "`job_ids` for the "
            "post at once - a carousel is several uploads and polling is a "
            "loop, so one call a round beats one per picture. Poll until "
            "`all_done`, then take `ready` (the asset ids, in the order asked "
            "for); anything in `failed` carries the error to read back."
        ),
    )
    def get_import_status(
        job_id: str | None = None, job_ids: list[str] | None = None
    ) -> dict[str, Any]:
        _guard("get_import_status")
        return intake.get_import_status(job_id, job_ids)

    @server.tool(
        name="create_campaign_post",
        description=(
            "Propose a post into a campaign from Library assets: one video "
            "asset, or several image assets as a carousel - or none, to "
            "draft the words first and attach media with set_post_media "
            "once it is uploaded; the campaign skips the post with a note "
            "until it has media. How many a "
            "carousel may hold is the network's own figure - X swipes "
            "through 4, Instagram and Facebook 10, LinkedIn 20, TikTok 35 - "
            "and `carousel_warnings` names any destination this post "
            "exceeds. Copy fields "
            "are optional and follow the same rules as the write_* tools - no "
            "links; the campaign adds its own. The post is created as a DRAFT "
            "outside the rotation, and only the operator can promote it in "
            "the app - tell them it is waiting. If the campaign's accounts "
            "`post_types` may map destination ids to per-account formats; "
            "omitted accounts inherit the default chosen when they were added. "
            "If the campaign's accounts cannot all carry a gallery, it is still created and "
            "`carousel_warnings` says which ones will not - pass that on."
        ),
    )
    def create_campaign_post(
        campaign_id: str,
        asset_ids: list[str],
        caption: str | None = None,
        title: str | None = None,
        hashtags: list[str] | None = None,
        first_comment: str | None = None,
        thread: list[str] | None = None,
        topic: str | None = None,
        post_types: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return _call(
            "create_campaign_post",
            lambda s: intake.create_campaign_post(
                s, workspace_id, campaign_id, asset_ids,
                caption=caption, title=title, hashtags=hashtags,
                first_comment=first_comment, thread=thread, topic=topic,
                post_types=post_types,
            ),
        )

    @server.tool(
        name="set_post_media",
        description=(
            "Attach or replace a DRAFT post's media from Library assets: one "
            "video asset id, or several image asset ids as a carousel. The "
            "other half of drafting a post before its media exists - upload "
            "with upload_media, wait for get_import_status, then attach "
            "here. Pass append=true to add pictures onto the post's existing "
            "carousel one upload at a time instead of replacing the whole "
            "package (a video always stands alone). Refused on a post "
            "already in rotation; changing what a promoted post publishes is "
            "the operator's act in the app."
        ),
    )
    def set_post_media(
        item_id: str, asset_ids: list[str], append: bool = False
    ) -> dict[str, Any]:
        return _call(
            "set_post_media",
            lambda s: writes.set_post_media(
                s, workspace_id, item_id, asset_ids, append=append
            ),
        )

    # --- when the workspace posts ------------------------------------------
    # Reading and setting the schedule, not the sending. See the note in
    # `policy` for why setting it sits on the allowed side of the boundary.

    @server.tool(
        name="list_posting_times",
        description=(
            "Every posting schedule in the workspace: its own recurring times, "
            "the named presets available, which pages are assigned one, and the "
            "timezone all of them are wall-clock in."
        ),
    )
    def list_posting_times() -> dict[str, Any]:
        return _call(
            "list_posting_times",
            lambda s: schedules.list_posting_times(s, workspace_id),
        )

    @server.tool(
        name="get_campaign_posting_times",
        description=(
            "What each of a campaign's accounts actually posts at, and which "
            "level decided it - the account itself, the campaign, the page, or "
            "the workspace."
        ),
    )
    def get_campaign_posting_times(campaign_id: str) -> dict[str, Any]:
        return _call(
            "get_campaign_posting_times",
            lambda s: schedules.get_campaign_posting_times(s, workspace_id, campaign_id),
        )

    @server.tool(
        name="create_posting_preset",
        description=(
            "Save a named set of posting times, as HH:MM in the workspace's "
            "timezone. This only defines the preset - it changes nothing about "
            "when anything posts until you assign it. Pass weekday 0-6 (Monday "
            "first) to pin it to one day; leave it out for every day."
        ),
    )
    def create_posting_preset(
        label: str,
        times: list[str],
        summary: str = "",
        weekday: int | None = None,
    ) -> dict[str, Any]:
        return _call(
            "create_posting_preset",
            lambda s: schedules.create_posting_preset(
                s, workspace_id, label, times, summary, weekday
            ),
        )

    @server.tool(
        name="set_campaign_posting_times",
        description=(
            "Give one campaign its own posting times, by preset id. Applies to "
            "every account it feeds that has not been given its own. Pass null "
            "to clear it and let each account inherit again."
        ),
    )
    def set_campaign_posting_times(
        campaign_id: str, preset_id: str | None = None
    ) -> dict[str, Any]:
        return _call(
            "set_campaign_posting_times",
            lambda s: schedules.set_campaign_posting_times(
                s, workspace_id, campaign_id, preset_id
            ),
        )

    @server.tool(
        name="set_page_posting_times",
        description=(
            "Assign a preset to one page, so every campaign posting to that "
            "account uses it unless the campaign or the account says otherwise. "
            "Pass null to clear the assignment."
        ),
    )
    def set_page_posting_times(
        page_key: str, preset_id: str | None = None
    ) -> dict[str, Any]:
        return _call(
            "set_page_posting_times",
            lambda s: schedules.set_page_posting_times(
                s, workspace_id, page_key, preset_id
            ),
        )

    @server.tool(
        name="set_workspace_posting_times",
        description=(
            "Replace the workspace's own posting times with exactly these, as "
            "HH:MM in its timezone. This is the fallback every campaign and page "
            "lands on, so a time left out is a time removed. Pass weekday 0-6 "
            "to pin them to one day; leave it out for every day."
        ),
    )
    def set_workspace_posting_times(
        times: list[str], weekday: int | None = None
    ) -> dict[str, Any]:
        return _call(
            "set_workspace_posting_times",
            lambda s: schedules.set_workspace_posting_times(
                s, workspace_id, times, weekday
            ),
        )

    return server


def exposed_tool_names() -> list[str]:
    """The tool names a caller will be offered - the allowed operations."""
    return policy.allowed_operations()


#: The surface, grouped the way an operator reads it rather than the flat
#: alphabet the wire listing is. Every exposed tool must be named in exactly
#: one group - `tool_catalog` refuses an uncategorised tool, so adding an
#: operation forces the decision of where it belongs instead of letting the
#: list silt up. Order here is display order.
TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Guidance": ("list_sops", "get_sop"),
    "Campaign context": (
        "list_campaigns", "list_posts_needing_copy",
        "get_post_context", "get_campaign_config",
        # What already went out belongs here rather than under Copy: it is
        # read to decide what to write, not a way of writing it.
        "list_published_posts",
    ),
    "Copy": (
        "write_caption", "write_first_comment", "write_thread",
        "write_post_copy", "write_disclosure", "write_bio_hint",
    ),
    "Media & posts": (
        "list_library_assets", "upload_image", "upload_media",
        "get_import_status", "create_campaign_post", "set_post_media",
    ),
    "Posting schedule": (
        "list_posting_times", "get_campaign_posting_times",
        "create_posting_preset", "set_campaign_posting_times",
        "set_page_posting_times", "set_workspace_posting_times",
    ),
}

#: The tab where each group's work shows up in the app, so an operator can
#: relate a tool to a screen they know. None where the work has no tab of its
#: own (guidance is read, not shown anywhere). Per-tool overrides carry the
#: exceptions - an upload lands in the Library even though the post it feeds
#: is a Campaigns matter.
CATEGORY_TABS: dict[str, str | None] = {
    "Guidance": None,
    "Campaign context": "Campaigns",
    "Copy": "Campaigns",
    "Media & posts": "Campaigns",
    "Posting schedule": "Campaigns",
}
TOOL_TAB_OVERRIDES: dict[str, str] = {
    "upload_image": "Library",
    "upload_media": "Library",
    "get_import_status": "Library",
}


@lru_cache(maxsize=1)
def tool_catalog() -> tuple[dict[str, Any], ...]:
    """Every exposed tool with what it does, read off the server itself.

    For the Tools tab to show a tool list the way an MCP client would - name,
    what it does, what it takes - rather than a comma-joined line of names.
    Read from a built server so the descriptions shown are the ones served and
    the two cannot drift; cached because the answer only changes with the code.
    Requires the mcp extra, like everything that builds a server - callers
    guard on availability. Ordered by category, then by each category's own
    order, which is the order the surface is explained in.
    """
    import asyncio

    listed = {tool.name: tool for tool in asyncio.run(build_server("catalog").list_tools())}
    category_of = {
        name: category
        for category, names in TOOL_CATEGORIES.items()
        for name in names
    }
    uncategorised = sorted(set(listed) - set(category_of))
    if uncategorised:
        raise RuntimeError(
            "Every MCP tool belongs to one TOOL_CATEGORIES group; missing: "
            + ", ".join(uncategorised)
        )
    catalog = []
    for category, names in TOOL_CATEGORIES.items():
        for name in names:
            tool = listed.get(name)
            if tool is None:
                continue
            schema = tool.inputSchema or {}
            required = set(schema.get("required") or [])
            access = policy.classify(tool.name)
            catalog.append({
                "name": tool.name,
                "category": category,
                "tab": TOOL_TAB_OVERRIDES.get(name, CATEGORY_TABS.get(category)),
                "description": " ".join((tool.description or "").split()),
                # Read or write, so the list can say which tools only look.
                "access": access.value if access else None,
                "params": [
                    {
                        "name": param,
                        "required": param in required,
                        "type": (detail or {}).get("type"),
                    }
                    for param, detail in (schema.get("properties") or {}).items()
                ],
                # The ChatGPT file-parameter declaration, and anything like it:
                # part of what the tool is, so the inspector shows it.
                "meta": tool.meta or None,
            })
    return tuple(catalog)
