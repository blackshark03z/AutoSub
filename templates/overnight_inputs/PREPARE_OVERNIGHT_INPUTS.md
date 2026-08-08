# PREPARE OVERNIGHT INPUTS

Dat cac file duoi day vao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\
```

Cau truc cuoi cung:

```text
D:\tool_auto_sub_worker_handoff_v0.2\
|-- input\
|   `-- source.mp4
|-- secrets\
|   |-- elevenlabs_api.txt
|   `-- gemini_api.txt
|-- operator\
|   |-- run_config.json
|   |-- translation_config.env
|   `-- source_provenance.json
`-- evidence\
```

## 1. Bat buoc de chay CP01-CP07 khong gian doan

### A. Video nguon

Copy video mau vao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\input\source.mp4
```

Expected canonical sample:

```text
SHA-256: 34a304fb44f5e4c27d1a34989a69f939888ef90c89bbae0142434f43cf4db068
Duration: 666.435918 seconds
Resolution: 1920x1080
FPS: 30
```

### B. ElevenLabs key list

Tao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\secrets\elevenlabs_api.txt
```

Moi dong khong rong va khong bat dau bang `#` la mot API key. Thu tu dong la thu tu uu tien ban dau.

### C. Gemini key list

Tao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\secrets\gemini_api.txt
```

Moi dong khong rong va khong bat dau bang `#` la mot API key.

### D. Gemini provider config khong chua secret

Tao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\operator\translation_config.env
```

Bat buoc:

```text
TRANSLATION_PROVIDER=gemini_openai_compatible
TRANSLATION_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
TRANSLATION_MODEL=PASTE_GEMINI_MODEL_HERE
TRANSLATION_TIMEOUT_SECONDS=180
TRANSLATION_KEY_FILE=secrets\gemini_api.txt
```

### E. Run configuration

Tao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\operator\run_config.json
```

Default de xuat:

```text
source_language: zh
target_language: en
target_locale: en-US
market_profile_id: roblox_en_us
content_mode: transformative_edit
TTS preset: quality_stable
audio_policy: replace_all_audio
preview: 720p
final: 1080p_or_source
```

### F. Provenance

Tao:

```text
D:\tool_auto_sub_worker_handoff_v0.2\operator\source_provenance.json
```

Co the de URL/author trong neu chua biet, nhung phai ghi `rights_status` va `operator_note`.

## 2. Rat nen cung cap

### Preferred ElevenLabs voice

Trong `run_config.json`:

```json
"preferred_voice_id": null
```

### Quota/budget

Dat hard cap:

```json
"elevenlabs_hard_character_cap": 100000
```

### Hardware/runtime

```json
"hardware": {
  "gpu": "auto",
  "whisper_device": "auto",
  "whisper_compute_type": "auto",
  "free_disk_required_gb": 20
}
```

### FFmpeg

Worker uu tien:

1. `ffmpeg` va `ffprobe` trong PATH.
2. `D:\tool_auto_sub_worker_handoff_v0.2\tools\ffmpeg\bin\`.
3. Duong dan trong `run_config.json`.

### Font

Default Windows:

```text
C:\Windows\Fonts\arial.ttf
```

## 3. Khong nen cung cap

- Mat khau tai khoan ElevenLabs.
- Cookie trinh duyet neu chi import local file.
- YouTube credential/OAuth.
- Google account password.
- Douyin login credential.
- Voice cloning samples.
- API key trong Markdown hoac source code.
- Key gui qua commit/Git.

## 4. Quy uoc parser tuong lai

- UTF-8 text
- Mot key moi dong
- Trim whitespace
- Bo qua dong rong
- Bo qua dong bat dau bang `#`
- Loai duplicate chinh xac trong memory
- Giu thu tu xuat hien dau tien
- Khong in full key
- Khong luu full key vao SQLite, log, metadata hay exception

## 5. Lenh kiem tra truoc khi giao worker

```text
powershell -ExecutionPolicy Bypass -File .\validate_overnight_inputs.ps1 -Mode Prepare
```

```text
powershell -ExecutionPolicy Bypass -File .\validate_overnight_inputs.ps1 -Mode Ready
```
