from datetime import datetime, timezone


def heartbeat() -> dict:
    return {"worker": "local", "status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}
