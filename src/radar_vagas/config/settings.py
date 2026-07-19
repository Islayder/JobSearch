import os
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseModel):
    database_url: str = Field(default="sqlite:///data/database/radar.sqlite3")
    config_dir: Path = Field(default=PROJECT_ROOT / "config")
    profile_path: Path | None = None
    debug: bool = False

    @classmethod
    def from_env(cls, *, debug: bool = False) -> "Settings":
        database_url = os.environ.get("RADAR_DATABASE_URL", "sqlite:///data/database/radar.sqlite3")
        config_dir = Path(os.environ.get("RADAR_CONFIG_DIR", str(PROJECT_ROOT / "config")))
        if not config_dir.is_absolute():
            config_dir = PROJECT_ROOT / config_dir
        profile_env = os.environ.get("RADAR_PROFILE_PATH")
        profile_path = Path(profile_env) if profile_env else None
        if profile_path is not None and not profile_path.is_absolute():
            profile_path = PROJECT_ROOT / profile_path
        return cls(
            database_url=database_url,
            config_dir=config_dir,
            profile_path=profile_path,
            debug=debug,
        )

    @property
    def database_path(self) -> Path | None:
        if self.database_url == "sqlite:///:memory:":
            return None
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw_path = self.database_url[len(prefix) :]
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def ensure_directories(self) -> None:
        database_path = self.database_path
        if database_path is not None:
            database_path.parent.mkdir(parents=True, exist_ok=True)
        (PROJECT_ROOT / "data" / "exports").mkdir(parents=True, exist_ok=True)
