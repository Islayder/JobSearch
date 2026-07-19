from pathlib import Path

from alembic import command
from alembic.config import Config

from radar_vagas.config.settings import PROJECT_ROOT, Settings


def alembic_config(settings: Settings) -> Config:
    config_path = PROJECT_ROOT / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def run_migrations(settings: Settings) -> None:
    settings.ensure_directories()
    command.upgrade(alembic_config(settings), "head")


def database_display_path(settings: Settings) -> str:
    database_path = settings.database_path
    if database_path is None:
        return settings.database_url
    return str(Path(database_path).resolve())
