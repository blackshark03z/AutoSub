import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.cp02_pipeline import run_cp02_vertical_slice


if __name__ == "__main__":
    result = run_cp02_vertical_slice()
    print(f"project_id={result['project_id']}")
    print(f"audio_path={result['audio_path']}")
    print(f"timeline_path={result['timeline']['path']}")
    print(f"timeline_sha256={result['timeline']['sha256']}")
    print(f"segments={len(result['timeline']['timeline']['segments'])}")
