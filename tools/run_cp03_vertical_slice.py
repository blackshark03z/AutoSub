import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import init_db
from app.providers.translation.gemini import GeminiOpenAICompatibleProvider, load_gemini_translation_config
from app.services.content_transform import transform_latest_timeline


def main() -> None:
    init_db()
    provider = GeminiOpenAICompatibleProvider(load_gemini_translation_config())
    result = transform_latest_timeline("vertical_slice_cp02", provider)
    print(f"project_id={result['project_id']}")
    print(f"provider={result['provider']}")
    print(f"model={result['model']}")
    print(f"request_hashes={','.join(result['request_hashes'])}")
    print(f"cache_status={result['cache_status']}")
    print(f"segments_transformed={result['segments_transformed']}")
    print(f"timeline_path={result['timeline']['path']}")
    print(f"timeline_sha256={result['timeline']['sha256']}")
    print(f"content_revision_path={result['content_revision']['path']}")
    print(f"content_revision_sha256={result['content_revision']['sha256']}")


if __name__ == "__main__":
    main()
