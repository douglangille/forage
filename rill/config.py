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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rill.db"


def load() -> Config:
    data_dir = Path(os.environ.get("RILL_DATA_DIR", "data")).expanduser()
    inbox = Path(os.environ.get("RILL_INBOX_DIR", data_dir / "inbox")).expanduser()
    return Config(
        data_dir=data_dir,
        inbox_dir=inbox,
        host=os.environ.get("RILL_HOST", "127.0.0.1"),
        port=int(os.environ.get("RILL_PORT", "8787")),
        refresh_minutes=int(os.environ.get("RILL_REFRESH_MINUTES", "30")),
        # Web Clipper's default template writes author as a [[wikilink]] property.
        wikilink_author=os.environ.get("RILL_WIKILINK_AUTHOR", "1") != "0",
        user_agent=os.environ.get(
            "RILL_USER_AGENT", "Mozilla/5.0 (compatible; rill/0.1; +self-hosted RSS reader)"
        ),
    )
