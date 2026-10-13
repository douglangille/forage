"""Settings, all from environment variables so the systemd unit is the only config file."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    inbox_dir: Path
    host: str
    port: int
    refresh_minutes: int
    wikilink_author: bool
    user_agent: str
    git_root: Path | None = None
    sync_script: str | None = None
    summary_key: str = ""
    summary_model: str = "openrouter/free"
    summary_daily_cap: int = 100
    summary_allow_training: bool = False
    summary_fallback_model: str = "anthropic/claude-haiku-5.5"  # paid ($0.10 in / $0.50 out per M tokens); used from the third try. Empty turns it off.

    @property
    def db_path(self) -> Path:
        return self.data_dir / "forage.db"


def _summary_key() -> str:
    """The OpenRouter key for summaries, from a file (preferred, so it never sits in an environment dump) or the environment."""
    path = os.environ.get("FORAGE_OPENROUTER_KEY_FILE")
    if path:
        try:
            return Path(path).expanduser().read_text().strip()
        except OSError:
            return ""
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def load() -> Config:
    data_dir = Path(os.environ.get("FORAGE_DATA_DIR", "data")).expanduser()
    inbox = Path(os.environ.get("FORAGE_INBOX_DIR", data_dir / "inbox")).expanduser()
    git_root = os.environ.get("FORAGE_GIT_ROOT")
    return Config(
        data_dir=data_dir,
        inbox_dir=inbox,
        host=os.environ.get("FORAGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("FORAGE_PORT", "8787")),
        refresh_minutes=int(os.environ.get("FORAGE_REFRESH_MINUTES", "30")),
        # Web Clipper's default template writes author as a [[wikilink]] property.
        wikilink_author=os.environ.get("FORAGE_WIKILINK_AUTHOR", "1") != "0",
        user_agent=os.environ.get(
            "FORAGE_USER_AGENT", "Mozilla/5.0 (compatible; forage/0.1; +self-hosted RSS reader)"
        ),
        # When set, a save also commits the clip and pushes (via sync_script if given).
        git_root=Path(git_root).expanduser() if git_root else None,
        sync_script=os.environ.get("FORAGE_SYNC_SCRIPT") or None,
        summary_key=_summary_key(),
        summary_model=os.environ.get("FORAGE_SUMMARY_MODEL", "openrouter/free"),
        summary_daily_cap=int(os.environ.get("FORAGE_SUMMARY_DAILY_CAP", "100")),
        summary_allow_training=os.environ.get("FORAGE_SUMMARY_ALLOW_TRAINING", "0") == "1",
        summary_fallback_model=os.environ.get("FORAGE_SUMMARY_FALLBACK_MODEL", "anthropic/claude-haiku-5.5"),
    )
