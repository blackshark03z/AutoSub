from alembic import command
from alembic.config import Config

from app.core.config import get_settings

CODE_ROOT = __file__


def alembic_config() -> Config:
    settings = get_settings()
    from pathlib import Path

    code_root = Path(CODE_ROOT).resolve().parents[2]
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(code_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(code_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.db_path}")
    return cfg


def upgrade_to_head() -> None:
    command.upgrade(alembic_config(), "head")
