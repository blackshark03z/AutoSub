# Field-learning runtime data

`events.jsonl` is generated operational telemetry for this checkout. It is intentionally ignored by Git so ordinary product commits do not accumulate governance-noise or local project telemetry.

Use `python scripts/ai_os.py field report` for the project-local view. Optional privacy-redacted cross-project learning is stored outside the repository at `~/.ai-build-os/field/events.jsonl` only when explicitly enabled in `config/assurance.json`.

Field data may recommend an upgrade candidate, but it must never edit stable kernel or policy automatically.
