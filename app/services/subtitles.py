from pathlib import Path


def format_srt_timestamp(ms: int) -> str:
    hours = ms // 3_600_000
    ms %= 3_600_000
    minutes = ms // 60_000
    ms %= 60_000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def format_ass_timestamp(ms: int) -> str:
    hours = ms // 3_600_000
    ms %= 3_600_000
    minutes = ms // 60_000
    ms %= 60_000
    seconds = ms // 1000
    centis = (ms % 1000) // 10
    return f"{hours}:{minutes:02}:{seconds:02}.{centis:02}"


def write_srt(timeline: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    index = 1
    for segment in timeline["segments"]:
        text = _subtitle_text(segment)
        if not text:
            continue
        lines.extend(
            [
                str(index),
                f"{format_srt_timestamp(segment['start_ms'])} --> {format_srt_timestamp(segment['end_ms'])}",
                text,
                "",
            ]
        )
        index += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_ass(timeline: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    for segment in timeline["segments"]:
        text = _subtitle_text(segment)
        if not text:
            continue
        escaped = _ass_escape(text)
        events.append(
            "Dialogue: 0,"
            f"{format_ass_timestamp(segment['start_ms'])},{format_ass_timestamp(segment['end_ms'])},"
            f"Default,,0,0,0,,{escaped}"
        )
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,40,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,52,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def _subtitle_text(segment: dict) -> str:
    if not segment.get("enabled", True):
        return ""
    return (segment.get("subtitle_text") or segment.get("spoken_text") or segment.get("translated_text") or "").strip()


def _ass_escape(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(ch for ch in normalized if ch == "\n" or ord(ch) >= 32)
    return normalized.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", r"\N")
