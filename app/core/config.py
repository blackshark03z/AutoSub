import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    db_path: Path
    run_config_path: Path
    source_path: Path
    provenance_path: Path
    font_path: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = Path(os.environ.get("TOOL_AUTO_SUB_ROOT", Path.cwd())).resolve()
    data_dir = Path(os.environ.get("TOOL_AUTO_SUB_DATA_DIR", root / "data")).resolve()
    run_config_path = root / "operator" / "run_config.json"
    config = json.loads(run_config_path.read_text(encoding="utf-8"))
    source_path = root / config["source"]["path"]
    font_path = Path(config.get("subtitle", {}).get("font_path", r"C:\Windows\Fonts\arial.ttf"))
    return Settings(
        root=root,
        data_dir=data_dir,
        db_path=Path(os.environ.get("TOOL_AUTO_SUB_DB_PATH", data_dir / "app.db")).resolve(),
        run_config_path=run_config_path,
        source_path=source_path,
        provenance_path=root / "operator" / "source_provenance.json",
        font_path=font_path,
    )
