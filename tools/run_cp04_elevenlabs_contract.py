import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import init_db, session_scope
from app.domain.models import Project
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from app.services.tts_generation import generate_tts_for_segment, resolve_voice_id


def main() -> None:
    init_db()
    project_id = "cp04_elevenlabs_contract"
    with session_scope() as session:
        if session.query(Project).filter(Project.project_id == project_id).one_or_none() is None:
            session.add(Project(project_id=project_id, title="CP04 ElevenLabs contract"))
    provider = ElevenLabsTTSProvider(load_elevenlabs_config())
    voice_id = resolve_voice_id(None, provider)
    segment = {
        "id": "contract_0001",
        "spoken_text": "Contract test.",
        "subtitle_text": "Contract test.",
        "translated_text": "Contract test.",
    }
    result = generate_tts_for_segment(project_id, segment, provider, voice_id)
    print(f"project_id={project_id}")
    print(f"provider=elevenlabs")
    print(f"model={provider.model}")
    print(f"voice_configured=True")
    print(f"request_hash={result['request_hash']}")
    print(f"cache_status={result['cache_status']}")
    print(f"request_id_present={bool(result['request_id'])}")
    print(f"artifact_path={result['artifact_path']}")
    print(f"artifact_sha256={result['sha256']}")
    print(f"character_count={result['character_count']}")


if __name__ == "__main__":
    main()
