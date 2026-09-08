"""The MCP server: the allowed operations, and nothing else, over Streamable HTTP.

Built for one workspace and served on loopback. Every tool re-checks the policy
at the moment it runs rather than trusting that it was only registered because
it is allowed - a caller may name any tool it likes, and the boundary is the
check, not the listing. The refused operations in the policy are never
registered here, so they are absent from the listing and refused by name.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from trendrelay_api.integrations.mcp import (
    context,
    intake,
    policy,
    products,
    schedules,
    sops,
    writes,
)

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


CopyPageLimit = Annotated[
    int,
    Field(
        ge=1,
        le=context.MAX_COPY_PAGE_SIZE,
        description="Posts to return. Use a smaller page to conserve model context.",
    ),
]
CopyPageOffset = Annotated[
    int,
    Field(ge=0, description="Zero-based position of the first post to return."),
]
ProductPageLimit = Annotated[
    int,
    Field(
        ge=1,
        le=products.MAX_PAGE,
        description="Products to return. Read full listings one product at a time.",
    ),
]
ProductPageOffset = Annotated[
    int,
    Field(ge=0, description="Zero-based position of the first product to return."),
]


def _file_value(value: OpenAIFile | None) -> dict[str, Any] | None:
    return value.model_dump(exclude_none=True) if value is not None else None

INSTRUCTIONS = (
    "This is a TrendRelay workspace. Help write copy for campaign posts that are "
    "queued without a caption yet.\n\n"
    "Before an action, use `list_sops` and `get_sop` to load the reviewed SOP "
    "that matches it. For campaign copy, start with action "
    "`campaigns.fill-needs-copy`, then page through `list_posts_needing_copy` "
    "using `limit`, `offset`, `more` and `next_offset` to see what needs writing without "
    "loading the whole queue at once, and "
    "`get_post_context` for one post: it gives the video, the attached product and "
    "what it pays, every destination the post reaches and where a first comment or "
    "thread reply lands there, and the campaign's brief. Write with "
    "`write_caption`, `write_first_comment` and `write_thread` (or `write_post_copy` "
    "for several at once).\n\n"
    "For product-aware copy or media prompts, use `list_products` (or "
    "`list_campaign_products` inside a campaign), then call `get_product_details` "
    "for the chosen product's complete listing and image gallery. "
    "`get_product_attribution` reads its campaign performance separately. Avoid "
    "loading every full listing at once, and make only claims supported by the "
    "live record.\n\n"
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
    "them. Nothing is lost between the halves: `list_campaign_posts` finds "
    "any post again by state, media shape or words in its caption, so a "
    "draft written in an earlier conversation can still be given its media.\n\n"
    "For when things post, `list_posting_times` gives the workspace's times, the "
    "presets available and which pages are assigned one; "
    "`get_campaign_posting_times` says what each of a campaign's accounts posts at "
    "and which level decided it. To change a rhythm, `create_posting_preset` names "
    "a set of times without putting it in front of anything, then "
    "`set_campaign_posting_times`, `set_page_posting_times` or "
    "`set_workspace_posting_times` puts it into effect. Times are HH:MM on the "
    "workspace's own clock, not UTC. One post can also be locked to one "
    "concrete slot: `get_day_slots` reads a day's openings - free, taken, "
    "locked or past - and `pin_post_slot` claims the most fitting free one "
    "(or the exact time you name) and holds the post there; other posts "
    "reflow around a lock, never through it, and release=true hands the post "
    "back to the rotation. Locks work on drafts too, and each lock reserves "
    "its slot against the next, so pinning a batch one by one - say ten "
    "drafts to the next ten days - spreads them without a collision.\n\n"
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
    from mcp.server.fastmcp import FastMCP, Image

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
            "procedure and metadata; use it before the related workspace tools. "
            "Long procedures come back in pages: when the result says "
            "`more: true`, call again with the `next_offset` it gives and read "
            "the rest before acting. A partial procedure is not a procedure. "
            "Pass a smaller `limit` if your client truncates large results."
        ),
    )
    def get_sop(
        action: str, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        _guard("get_sop")
        return sops.get_sop_page(action, offset=offset, limit=limit)

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
            "linked and a permalink. Every post also names the clip behind it "
            "(video_title, asset_id, duration_seconds) so a winner can be "
            "found again and used, and carries every figure at once in "
            "`metrics` - views, likes, comments, shares, saves, watch_seconds "
            "- so one call answers what did well without asking again per "
            "measure. `sort_by` only decides the order: interactions "
            "(default), views, likes, comments, shares, saves or "
            "watch_seconds. Narrow with campaign_id or platform. A post "
            "nobody has read back yet says measured: false and carries no "
            "figures - that is not the same as a post that got nothing, and "
            "it never outranks one. To see posts rather than read about "
            "them, pass their asset_ids to get_asset_thumbnails - up to "
            "eight per call."
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
        name="get_asset_thumbnails",
        description=(
            "Library thumbnail stills for up to eight assets, returned as "
            "images, each preceded by a text line naming its asset so the "
            "pictures cannot be mistaken for one another. The companion to "
            "the listings that name an asset_id - the top posts of "
            "list_published_posts, list_library_assets, the needs-copy queue "
            "- which stay compact text; fetch stills only for the posts "
            "actually being studied. An id with nothing to show is answered "
            "in words in its place, and the rest still arrive. For a video "
            "the still is a representative frame; for a picture, a small "
            "copy."
        ),
    )
    # `-> Any` rather than the content types: the module defers postponed
    # annotations, which are evaluated against module globals where the
    # lazily imported Image does not exist. The conversion to text and image
    # content blocks is done by the returned values' types, in order.
    def get_asset_thumbnails(asset_ids: list[str]) -> Any:
        entries = _call(
            "get_asset_thumbnails",
            lambda s: context.get_asset_thumbnails(s, workspace_id, asset_ids),
        )
        blocks: list[Any] = []
        for entry in entries:
            name = entry["title"] or entry["asset_id"]
            if entry["data"] is None:
                blocks.append(f"{name}: {entry['note']}")
                continue
            blocks.append(f"{name} ({entry['asset_id']}):")
            mime = entry["mime"]
            blocks.append(Image(
                data=entry["data"],
                format=mime.split("/", 1)[1] if "/" in mime else "jpeg",
            ))
        return blocks

    @server.tool(
        name="list_posts_needing_copy",
        description=(
            "A page of posts that are queued but have no caption written yet. "
            "Pass campaign_id to narrow it; adjust limit and offset as needed. "
            "The response includes total, more and next_offset. Each compact "
            "entry says what the clip is, how long it runs, and what it sells."
        ),
    )
    def list_posts_needing_copy(
        campaign_id: str | None = None,
        limit: CopyPageLimit = context.DEFAULT_COPY_PAGE_SIZE,
        offset: CopyPageOffset = 0,
    ) -> dict[str, Any]:
        return _call(
            "list_posts_needing_copy",
            lambda s: context.list_posts_needing_copy(
                s, workspace_id, campaign_id, limit=limit, offset=offset,
            ),
        )

    @server.tool(
        name="list_campaign_posts",
        description=(
            "A page of the queue's posts whatever their state, so a post's "
            "item_id can always be found again - a draft captioned in an "
            "earlier conversation included. Narrow with campaign_id, state "
            "(draft, approved, paused, retired), media (video, carousel, "
            "'text only', or 'none yet' for drafts still waiting for theirs) "
            "or search, which matches captions and titles. Each entry carries "
            "its state, a caption excerpt to recognise it by, and the slot it "
            "is locked to if any; the response paginates like "
            "list_posts_needing_copy."
        ),
    )
    def list_campaign_posts(
        campaign_id: str | None = None,
        state: str | None = None,
        media: str | None = None,
        search: str | None = None,
        limit: CopyPageLimit = context.DEFAULT_COPY_PAGE_SIZE,
        offset: CopyPageOffset = 0,
    ) -> dict[str, Any]:
        return _call(
            "list_campaign_posts",
            lambda s: context.list_campaign_posts(
                s, workspace_id, campaign_id,
                state=state, media=media, search=search,
                limit=limit, offset=offset,
            ),
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
            "An empty string clears the override back to the campaign's. TikTok "
            "captions never carry this line - TikTok treats 'link in bio' "
            "call-outs as spam and they risk violations - so it reaches "
            "Instagram-style bio networks only, and TikTok copy should not "
            "point at the profile either."
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
        name="list_products",
        description=(
            "Search the workspace product catalog, optionally limited to one "
            "campaign or to products with/without a fetched listing. Returns "
            "identity, primary image, a bounded listing preview and every offer; "
            "pages with limit/offset. Use get_product_details only for products "
            "you need in full, rather than pulling every long description and "
            "gallery into context at once."
        ),
    )
    def list_products(
        query: str | None = None,
        campaign_id: str | None = None,
        has_listing: bool | None = None,
        limit: ProductPageLimit = products.DEFAULT_PAGE,
        offset: ProductPageOffset = 0,
    ) -> dict[str, Any]:
        return _call(
            "list_products",
            lambda s: products.list_products(
                s, workspace_id, query=query, campaign_id=campaign_id,
                has_listing=has_listing, limit=limit, offset=offset,
            ),
        )

    @server.tool(
        name="get_product_details",
        description=(
            "Read one product's complete stored listing and commercial context: "
            "full description, every stored image, categories, attributes, "
            "variations, vouchers, listing freshness, offers, campaign membership, "
            "tracking links, clicks and conversions. Use the product_id returned by "
            "list_products, list_campaign_products or get_post_context. This is the "
            "authoritative context for product-aware copy and media prompts."
        ),
    )
    def get_product_details(product_id: str) -> dict[str, Any]:
        return _call(
            "get_product_details",
            lambda s: products.get_product_details(s, workspace_id, product_id),
        )

    @server.tool(
        name="get_product_attribution",
        description=(
            "Read one product's attribution independently of its listing: tracking "
            "links, click count and conversion statuses, split by campaign and with "
            "commission/order value kept in integer cents per currency. Use this "
            "when performance should inform product selection; it changes nothing."
        ),
    )
    def get_product_attribution(product_id: str) -> dict[str, Any]:
        return _call(
            "get_product_attribution",
            lambda s: products.get_product_attribution(s, workspace_id, product_id),
        )

    @server.tool(
        name="list_campaign_products",
        description=(
            "The products a campaign may promote, and which are still free to "
            "attach. Use it when the media makes a different product the "
            "obvious fit than the one smart matching chose. Products already "
            "pinned to another post are hidden - each product goes to one post "
            "in a campaign - so what comes back is what you may actually pick; "
            "pass include_taken=true to see the rest and which post holds each. "
            "Pass post_id so this post's own products come back as `current` "
            "rather than as taken. Pages with limit/offset; follow `more` and "
            "`next_offset`."
        ),
    )
    def list_campaign_products(
        campaign_id: str,
        post_id: str | None = None,
        include_taken: bool = False,
        limit: ProductPageLimit = products.DEFAULT_PAGE,
        offset: ProductPageOffset = 0,
    ) -> dict[str, Any]:
        return _call(
            "list_campaign_products",
            lambda s: products.list_campaign_products(
                s, workspace_id, campaign_id,
                post_id=post_id, include_taken=include_taken,
                limit=limit, offset=offset,
            ),
        )

    @server.tool(
        name="set_post_products",
        description=(
            "Choose which of the campaign's products this post carries, "
            "overriding smart matching for it. Give the offer_ids from "
            "list_campaign_products; the list replaces the post's current "
            "choice, and an empty list hands it back to smart matching. "
            "Refused if a product is not tagged to the campaign, is "
            "unavailable at the merchant, exceeds the campaign's products-per-"
            "post ceiling, or is already pinned to another post - each product "
            "goes to one post. Do not use this to make a caption match a "
            "product; use it when the product should match the media."
        ),
    )
    def set_post_products(post_id: str, offer_ids: list[str]) -> dict[str, Any]:
        return _call(
            "set_post_products",
            lambda s: products.set_post_products(s, workspace_id, post_id, offer_ids),
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
            "until it has media. Pass text_only=true (with a caption and no "
            "assets) for a deliberate copy-only post that publishes as words "
            "alone and is never held for media. How many a "
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
        text_only: bool = False,
    ) -> dict[str, Any]:
        return _call(
            "create_campaign_post",
            lambda s: intake.create_campaign_post(
                s, workspace_id, campaign_id, asset_ids,
                caption=caption, title=title, hashtags=hashtags,
                first_comment=first_comment, thread=thread, topic=topic,
                post_types=post_types, text_only=text_only,
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
            "package (a video always stands alone). Pass text_only=true "
            "with no assets to make the draft a deliberate copy-only post "
            "that publishes as words alone. Refused on a post "
            "already in rotation; changing what a promoted post publishes is "
            "the operator's act in the app."
        ),
    )
    def set_post_media(
        item_id: str, asset_ids: list[str],
        append: bool = False, text_only: bool = False,
    ) -> dict[str, Any]:
        return _call(
            "set_post_media",
            lambda s: writes.set_post_media(
                s, workspace_id, item_id, asset_ids,
                append=append, text_only=text_only,
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
        name="get_day_slots",
        description=(
            "One day's concrete posting slots for a campaign and what claims "
            "each of them: free, taken by a committed post, locked by another "
            "post's pin, or already past. Read this before pin_post_slot to "
            "see what is available. Day is YYYY-MM-DD in the workspace "
            "timezone; pass item_id for the post being placed so its own "
            "current lock reads as free to it."
        ),
    )
    def get_day_slots(
        campaign_id: str, day: str, item_id: str | None = None
    ) -> dict[str, Any]:
        return _call(
            "get_day_slots",
            lambda s: schedules.get_day_slots(
                s, workspace_id, campaign_id, day, item_id=item_id
            ),
        )

    @server.tool(
        name="pin_post_slot",
        description=(
            "Lock a campaign post to one of that day's posting slots, or "
            "release the lock. With only a day (YYYY-MM-DD) the most fitting "
            "free slot is chosen - the earliest still ahead, preferring an "
            "account the post has never been on - and with time (HH:MM, "
            "workspace timezone) exactly that slot is claimed. A locked post "
            "is spent nowhere else, and other posts reflow around it when "
            "something publishes early. Pass release=true to hand it back to "
            "the rotation. A scheduling write, not an approval: a draft still "
            "waits for the operator to promote it. Each lock reserves its "
            "slot against the next call, so locking a batch of posts one by "
            "one - each to its own day - spreads them without a collision."
        ),
    )
    def pin_post_slot(
        item_id: str,
        day: str | None = None,
        time: str | None = None,
        release: bool = False,
    ) -> dict[str, Any]:
        return _call(
            "pin_post_slot",
            lambda s: writes.pin_post_slot(
                s, workspace_id, item_id, day=day, time=time, release=release
            ),
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
        "list_campaigns", "list_posts_needing_copy", "list_campaign_posts",
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
        "list_library_assets", "get_asset_thumbnails", "upload_image", "upload_media",
        "get_import_status", "create_campaign_post", "set_post_media",
    ),
    # Which product a post carries: read what the campaign may promote, and
    # choose among those. Its own group rather than filed under Copy, because
    # it decides what the post earns on rather than what it says - and the
    # decision is bounded by the campaign's own tags either way.
    "Products": (
        "list_products", "get_product_details", "get_product_attribution",
        "list_campaign_products", "set_post_products",
    ),
    "Posting schedule": (
        "list_posting_times", "get_campaign_posting_times",
        "get_day_slots", "pin_post_slot",
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
    "Products": "Campaigns",
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
