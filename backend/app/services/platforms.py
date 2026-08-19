"""Platform catalogue and resolution of the names returned by tools."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import PlatformCategory
from app.models.identity import Platform

#: (name, category, base_url, profile URL template, icon)
PLATFORM_SEED: list[tuple[str, str, str, str, str]] = [
    ("Instagram", PlatformCategory.SOCIAL_NETWORK.value, "https://instagram.com",
     "https://instagram.com/{username}", "instagram"),
    ("Facebook", PlatformCategory.SOCIAL_NETWORK.value, "https://facebook.com",
     "https://facebook.com/{username}", "facebook"),
    ("X", PlatformCategory.SOCIAL_NETWORK.value, "https://x.com",
     "https://x.com/{username}", "x"),
    ("TikTok", PlatformCategory.VIDEO.value, "https://tiktok.com",
     "https://tiktok.com/@{username}", "tiktok"),
    ("Snapchat", PlatformCategory.SOCIAL_NETWORK.value, "https://snapchat.com",
     "https://snapchat.com/add/{username}", "snapchat"),
    ("LinkedIn", PlatformCategory.PROFESSIONAL.value, "https://linkedin.com",
     "https://linkedin.com/in/{username}", "linkedin"),
    ("GitHub", PlatformCategory.DEVELOPER.value, "https://github.com",
     "https://github.com/{username}", "github"),
    ("GitLab", PlatformCategory.DEVELOPER.value, "https://gitlab.com",
     "https://gitlab.com/{username}", "gitlab"),
    ("Reddit", PlatformCategory.FORUM.value, "https://reddit.com",
     "https://reddit.com/user/{username}", "reddit"),
    ("YouTube", PlatformCategory.VIDEO.value, "https://youtube.com",
     "https://youtube.com/@{username}", "youtube"),
    ("Twitch", PlatformCategory.GAMING.value, "https://twitch.tv",
     "https://twitch.tv/{username}", "twitch"),
    ("Steam", PlatformCategory.GAMING.value, "https://steamcommunity.com",
     "https://steamcommunity.com/id/{username}", "steam"),
    ("Discord", PlatformCategory.GAMING.value, "https://discord.com", "", "discord"),
    ("Telegram", PlatformCategory.SOCIAL_NETWORK.value, "https://t.me",
     "https://t.me/{username}", "telegram"),
    ("Spotify", PlatformCategory.MUSIC.value, "https://open.spotify.com",
     "https://open.spotify.com/user/{username}", "spotify"),
    ("SoundCloud", PlatformCategory.MUSIC.value, "https://soundcloud.com",
     "https://soundcloud.com/{username}", "soundcloud"),
    ("Pinterest", PlatformCategory.SOCIAL_NETWORK.value, "https://pinterest.com",
     "https://pinterest.com/{username}", "pinterest"),
    ("Medium", PlatformCategory.BLOG.value, "https://medium.com",
     "https://medium.com/@{username}", "medium"),
    ("Vinted", PlatformCategory.MARKETPLACE.value, "https://vinted.fr",
     "https://vinted.fr/member/{username}", "vinted"),
    ("Leboncoin", PlatformCategory.MARKETPLACE.value, "https://leboncoin.fr", "", "leboncoin"),
    ("Mastodon", PlatformCategory.SOCIAL_NETWORK.value, "https://mastodon.social",
     "https://mastodon.social/@{username}", "mastodon"),
    ("Keybase", PlatformCategory.DEVELOPER.value, "https://keybase.io",
     "https://keybase.io/{username}", "keybase"),
    ("Twitter", PlatformCategory.SOCIAL_NETWORK.value, "https://x.com",
     "https://x.com/{username}", "x"),
]

#: Names returned by tools mapped to their canonical slug.
ALIASES = {
    "twitter": "x",
    "x (twitter)": "x",
    "instagram.com": "instagram",
    "github.com": "github",
    "youtube user": "youtube",
    "youtube channel": "youtube",
    "reddit user": "reddit",
    "telegram (t.me)": "telegram",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


def seed_platforms(db: Session) -> int:
    """Insert missing platforms. Idempotent."""
    # `select(Platform.slug)` yields plain strings, not Platform rows.
    existing = set(db.execute(select(Platform.slug)).scalars().all())
    created = 0
    for name, category, base_url, template, icon in PLATFORM_SEED:
        slug = slugify(name)
        if slug in existing:
            continue
        db.add(
            Platform(
                name=name,
                slug=slug,
                category=category,
                base_url=base_url or None,
                profile_url_template=template or None,
                icon=icon,
                enabled=True,
            )
        )
        existing.add(slug)
        created += 1
    db.flush()
    return created


def resolve(db: Session, name: str | None, *, create: bool = True) -> Platform | None:
    """Find (or create) the platform matching a tool-provided name."""
    if not name:
        return None
    slug = ALIASES.get(name.strip().lower(), slugify(name))
    if not slug:
        return None
    platform = db.execute(
        select(Platform).where(Platform.slug == slug)
    ).scalar_one_or_none()
    if platform or not create:
        return platform
    platform = Platform(
        name=name.strip()[:120],
        slug=slug[:120],
        category=PlatformCategory.OTHER.value,
        enabled=True,
    )
    db.add(platform)
    db.flush()
    return platform


def by_slug(db: Session, slug: str) -> Platform | None:
    return db.execute(select(Platform).where(Platform.slug == slug)).scalar_one_or_none()
