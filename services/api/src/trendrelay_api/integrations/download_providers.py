"""Which service a pasted link belongs to, and the rule that a batch is one service.

The Downloads tab began as a Douyin tab, so "the provider" was a fact nobody had
to establish. It is now a question, and it is answered from the links rather
than asked as a dropdown: somebody pasting forty links already knows where they
came from, and making them say so is asking them to repeat themselves.

Why a batch may not mix services
--------------------------------
One submission becomes one job, with one output directory, one set of
credentials, one rate limit and one downloader binary. A batch spanning two
services has no honest answer for any of those: it would either run twice under
one job's status - so "failed" could not say which half - or silently drop the
links belonging to the service that lost the coin toss.

So a mixed paste is refused at the door, and the refusal names both services
found and how many links each holds. That is a sentence somebody can act on;
"invalid input" is not.

Adding a service
----------------
Declare it here. `detect` and the refusals follow from the table, and the
interface reads the same table through the API rather than keeping its own copy
of which host belongs to whom - a second copy is how the two drift into
disagreeing about what a link is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceKind:
    """One shape of link a service accepts, named for what it is to a person."""

    id: str
    label: str
    #: Path fragments that identify this kind. Matched case-insensitively.
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadProvider:
    id: str
    label: str
    #: Hosts owned by this service. A host matches itself and its subdomains.
    hosts: tuple[str, ...]
    #: Short-link hosts, where any path at all is a real source and the path
    #: says nothing about what kind. `v.douyin.com/abc` is a whole video.
    short_hosts: tuple[str, ...] = ()
    kinds: tuple[SourceKind, ...] = ()
    #: What the paste box shows when this service is the one detected.
    example: str = ""
    #: The tool that does the fetching, as declared in the tool catalogue.
    tool_id: str = ""
    #: The fetch modes this service can actually deliver, as ids the request
    #: model already accepts. Declared per service rather than shared, because
    #: they are not the same everywhere: TikTok keeps likes private and its
    #: sound and tag extractors are marked broken upstream, so offering
    #: "liked videos" there would be a control that always fails.
    modes: tuple[str, ...] = ("post",)
    #: Extra file kinds this service can fetch beside the video itself.
    media_kinds: tuple[str, ...] = ("video",)
    extras: dict[str, Any] = field(default_factory=dict)

    def owns_host(self, host: str) -> bool:
        return any(host == name or host.endswith(f".{name}") for name in self.hosts)

    def owns_short_host(self, host: str) -> bool:
        return any(
            host == name or host.endswith(f".{name}") for name in self.short_hosts
        )

    def kind_of(self, url: str) -> str | None:
        """Which shape of source this is, or None when the link is not one.

        A host on its own is not a source. `douyin.com` is the front page and
        `tiktok.com/@someone/` with nothing after it is a profile - the second
        is a source and the first is not, which is why the kinds are declared
        per service rather than guessed from the path's depth.
        """
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        if self.owns_short_host(host):
            # Any path is the whole address here; there is nothing else it
            # could be, and refusing it for not looking like a video would
            # refuse the most commonly shared link there is.
            return "share" if path.strip("/") else None
        if not self.owns_host(host):
            return None
        for kind in self.kinds:
            if any(part in path for part in kind.paths):
                return kind.id
        return None

    def accepts(self, url: str) -> bool:
        return self.kind_of(url) is not None


DOUYIN = DownloadProvider(
    id="douyin",
    label="Douyin",
    hosts=("douyin.com", "iesdouyin.com"),
    short_hosts=("v.douyin.com",),
    kinds=(
        SourceKind("video", "Video", ("/video/", "/note/")),
        SourceKind("profile", "Profile", ("/user/",)),
        SourceKind("collection", "Collection", ("/mix/",)),
        SourceKind("music", "Music", ("/music/",)),
    ),
    example="https://www.douyin.com/video/…",
    tool_id="douyin-downloader",
    modes=("post", "like", "mix", "music"),
    media_kinds=("video", "image", "audio"),
)

TIKTOK = DownloadProvider(
    id="tiktok",
    label="TikTok",
    hosts=("tiktok.com",),
    # Every one of TikTok's own shorteners. They resolve to a video, a profile
    # or a collection, and which one is not knowable until it is followed.
    short_hosts=("vm.tiktok.com", "vt.tiktok.com"),
    kinds=(
        SourceKind("video", "Video", ("/video/", "/photo/")),
        SourceKind("collection", "Collection", ("/collection/",)),
        # Last, because "/@handle/video/123" is a video and contains "/@". A
        # profile is the handle with nothing after it, which `kind_of` reaches
        # only once the more specific paths have failed to match.
        SourceKind("profile", "Channel", ("/@",)),
    ),
    example="https://www.tiktok.com/@handle/video/…",
    # Only what the extractors actually deliver. Likes are private on TikTok,
    # and yt-dlp marks its sound and tag extractors broken - so "post" and
    # collections are the honest list.
    tool_id="yt-dlp",
    modes=("post", "mix"),
    media_kinds=("video", "image", "audio"),
)

#: Order is the order the interface offers them in.
PROVIDERS: tuple[DownloadProvider, ...] = (DOUYIN, TIKTOK)

BY_ID: dict[str, DownloadProvider] = {provider.id: provider for provider in PROVIDERS}


class MixedProviders(ValueError):
    """One batch, two services. Raised with a sentence naming both."""

    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        named = ", ".join(
            f"{BY_ID[provider].label} ({count} "
            f"{'link' if count == 1 else 'links'})"
            for provider, count in sorted(counts.items(), key=lambda row: -row[1])
        )
        super().__init__(
            f"These links are from more than one service: {named}. "
            "A download runs against one service at a time - each has its own "
            "sign-in, its own limits and its own downloader. Keep the links "
            "from one and start a second download for the rest."
        )


def provider_for(url: str) -> DownloadProvider | None:
    """The service a single link belongs to, or None when no service claims it."""
    for provider in PROVIDERS:
        if provider.accepts(url):
            return provider
    return None


def classify(urls: list[str]) -> dict[str, list[str]]:
    """Every link sorted under the service that claims it.

    Links nothing claims land under `""`. They are not an error on their own -
    a pasted share message is mostly prose - but they are worth counting, so
    the interface can say how many it ignored rather than quietly dropping
    them.
    """
    grouped: dict[str, list[str]] = {}
    for url in urls:
        provider = provider_for(url)
        grouped.setdefault(provider.id if provider else "", []).append(url)
    return grouped


def detect(urls: list[str]) -> tuple[DownloadProvider, list[str], list[str]]:
    """The one service this batch is for, its links, and the ones ignored.

    Raises `MixedProviders` when the paste spans two services, and `ValueError`
    when no link is a source at all - two different problems needing two
    different things done about them.
    """
    grouped = classify(urls)
    ignored = grouped.pop("", [])
    if not grouped:
        raise ValueError(
            "No downloadable link found. Paste a link to a video, a profile or "
            "a collection from "
            + " or ".join(provider.label for provider in PROVIDERS)
            + "."
        )
    if len(grouped) > 1:
        raise MixedProviders({key: len(value) for key, value in grouped.items()})
    provider_id, matched = next(iter(grouped.items()))
    return BY_ID[provider_id], matched, ignored


def catalogue() -> list[dict[str, Any]]:
    """The table the interface reads, so it keeps no second copy of these rules."""
    return [
        {
            "id": provider.id,
            "label": provider.label,
            "hosts": list(provider.hosts),
            "short_hosts": list(provider.short_hosts),
            "kinds": [
                {"id": kind.id, "label": kind.label} for kind in provider.kinds
            ],
            "example": provider.example,
            "tool_id": provider.tool_id,
            "modes": list(provider.modes),
            "media_kinds": list(provider.media_kinds),
        }
        for provider in PROVIDERS
    ]
