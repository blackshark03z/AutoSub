import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.session import init_db
from app.services.preview_render import render_preview


def main() -> None:
    init_db()
    result = render_preview("vertical_slice_cp02", get_settings().source_path)
    print(f"project_id={result['project_id']}")
    print(f"audio_policy={result['audio_policy']}")
    print(f"source_audio_mapped={result['source_audio_mapped']}")
    print(f"srt_path={result['srt_path']}")
    print(f"ass_path={result['ass_path']}")
    print(f"tts_mix_path={result['tts_mix_path']}")
    print(f"preview_path={result['preview_path']}")
    print(f"preview_sha256={result['preview_sha256']}")
    print(f"preview_width={result['preview_media']['video']['width']}")
    print(f"preview_height={result['preview_media']['video']['height']}")
    if result["preview_media"]["video"]["width"] != 1280 or result["preview_media"]["video"]["height"] != 720:
        raise SystemExit("Preview is not 1280x720")
    if not result["preview_media"]["audio"]["codec"]:
        raise SystemExit("Preview audio stream missing")
    print(f"fake_tts_generations={result['generations']}")


if __name__ == "__main__":
    main()
