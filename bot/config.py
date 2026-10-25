"""Settings (from environment / .env) and the site list (sites.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


@dataclass
class Settings:
    bot_token: str
    chat_id: str            # where announcements go; comma-separated for several chats
    admin_id: str           # who may use commands and gets reports; comma-separated allowed
    interval_minutes: int
    max_workers: int
    request_timeout: int
    report_hour: int        # local hour for the daily health report (-1 = off)
    timezone: str
    max_new_per_site: int   # above this, a site is treated as "redesigned" -> one summary msg
    link_preview: bool
    healthcheck_url: str
    dry_run: bool
    data_dir: Path
    sites_file: Path
    log_level: str = "INFO"
    extra_keywords: list[str] = field(default_factory=list)

    @property
    def chat_ids(self) -> list[str]:
        """CHAT_ID may list several recipients separated by commas (you, your friend, a channel)."""
        return [c.strip() for c in self.chat_id.split(",") if c.strip()]

    @property
    def admin_ids(self) -> list[str]:
        return [c.strip() for c in self.admin_id.split(",") if c.strip()]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    chat_id = os.environ.get("CHAT_ID", "").strip()
    s = Settings(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        chat_id=chat_id,
        admin_id=os.environ.get("ADMIN_ID", "").strip() or chat_id,
        interval_minutes=_int("INTERVAL_MINUTES", 30),
        max_workers=_int("MAX_WORKERS", 8),
        request_timeout=_int("REQUEST_TIMEOUT", 25),
        report_hour=_int("REPORT_HOUR", 9),
        timezone=os.environ.get("TIMEZONE", "Africa/Casablanca"),
        max_new_per_site=_int("MAX_NEW_PER_SITE", 15),
        link_preview=_bool("LINK_PREVIEW", True),
        healthcheck_url=os.environ.get("HEALTHCHECK_URL", "").strip(),
        dry_run=_bool("DRY_RUN", False),
        data_dir=data_dir,
        sites_file=Path(os.environ.get("SITES_FILE", ROOT / "sites.yaml")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        extra_keywords=[k.strip() for k in os.environ.get("EXTRA_KEYWORDS", "").split(",") if k.strip()],
    )
    if not s.dry_run and not s.bot_token:
        raise SystemExit("BOT_TOKEN must be set (see .env.example), or set DRY_RUN=1")
    # An empty CHAT_ID (and no DRY_RUN) starts the bot in setup mode: it only replies to
    # /start with the sender's ids, so the owner can find the value to put in CHAT_ID.
    return s


@dataclass
class Site:
    id: str
    name: str
    url: str
    category: str = "other"
    enabled: bool = True
    rss: str | None = None
    selector: str | None = None
    keywords: bool = True
    also: list[str] = field(default_factory=list)


def load_sites(path: Path) -> list[Site]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sites: list[Site] = []
    seen: set[str] = set()
    for raw in data.get("sites", []):
        site = Site(
            id=str(raw["id"]),
            name=str(raw.get("name", raw["id"])),
            url=str(raw["url"]),
            category=str(raw.get("category", "other")),
            enabled=bool(raw.get("enabled", True)),
            rss=raw.get("rss"),
            selector=raw.get("selector"),
            keywords=bool(raw.get("keywords", True)),
            also=list(raw.get("also", []) or []),
        )
        if site.id in seen:
            raise SystemExit(f"Duplicate site id in {path.name}: {site.id}")
        seen.add(site.id)
        sites.append(site)
    return sites
