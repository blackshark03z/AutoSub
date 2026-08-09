# Real offline translation runtime smoke

Date: 2026-08-09 (Asia/Saigon)
Goal: `PROVISION_SUPPORTED_OFFLINE_TRANSLATION_RUNTIME`
Task: `PROVISION_LOCAL_ARGOS_RUNTIME`

## Provisioned machine-local runtime

The runtime is deliberately outside the repository and is selected only through
`TOOL_AUTO_SUB_TRANSLATION_RUNTIME_CONFIG`.

| Item | Value |
| --- | --- |
| Runtime root | `D:\\ToolAutoSubRuntime\\translation` |
| Python | `D:\\ToolAutoSubRuntime\\translation\\venv\\Scripts\\python.exe` (Python 3.11.4) |
| Translation runtime | `argostranslate==1.9.6` |
| Supporting runtime | `ctranslate2==4.8.1`, `sentencepiece==0.2.0`, `torch==2.13.0` |
| Package root | `D:\\ToolAutoSubRuntime\\translation\\packages` |
| Model ID | `translate-zh_en-1_9` |
| Model binary | `D:\\ToolAutoSubRuntime\\translation\\packages\\translate-zh_en-1_9\\model\\model.bin` |
| Model binary bytes / SHA-256 | `82,713,318` / `EDD8C8A6863D36959613FF291074627A1635FAB2F51B872EF437E924D238921A` |
| Installed model tree bytes | `86,137,496` |

The downloaded official Argos package was cached at
`D:\\ToolAutoSubRuntime\\translation\\downloads\\translate-zh_en-1_9.argosmodel`:

| Item | Value |
| --- | --- |
| Download size / SHA-256 | `74,481,402` / `62E7AF5A3A48B530E47B7B3E5C78C2DE79073ECD815750D2BF3AB35B4A67DA2D` |
| Provenance | `https://argos-net.com/v1/translate-zh_en-1_9.argosmodel` |
| License | OPUS-MT Chinese-English, CC-BY 4.0; attribution and source recorded in `licenses/ARGOS_ZH_EN_NOTICE.txt` |

The external configuration was:

```json
{
  "schema_version": 1,
  "python_path": "D:\\ToolAutoSubRuntime\\translation\\venv\\Scripts\\python.exe",
  "packages_root": "D:\\ToolAutoSubRuntime\\translation\\packages",
  "model_id": "translate-zh_en-1_9",
  "timeout_seconds": 60,
  "network_during_processing": "disabled"
}
```

`argostranslate.package.install_from_path` completed successfully. No optional
alignment model or non-commercial model asset was installed. The model and
runtime remain machine-local and are not Git artifacts.

## Real service smoke

With the environment variable set to the configuration above,
`app.services.offline_translation.translate_source_captions` was invoked with
three Chinese source cues retained from the canonical AutoSubs real-media
evidence. It launched the configured external worker (not a fake/mocked
worker) and returned:

| Source cue | English translation |
| --- | --- |
| `怎么呀来我再学习呢` | `I'll study later.` |
| `我在学回去看电视吧` | `I'm learning to watch TV.` |
| `玩得挺好哈哎呀,幸亏我在学` | `I'm glad I'm here.` |

Assertions passed: returned value was a list, every `source_text` exactly
equalled its input, every `translated_text` was non-empty English, every
`runtime` was `argostranslate`, and every `model` was
`translate-zh_en-1_9`. This validates the source/translation separation:
source text is retained verbatim while English appears only in the translated
track.

A direct configured worker invocation with source `我来了` also completed with
exit code `0`, empty stderr, `ok=true`, `runtime=argostranslate`,
`model=translate-zh_en-1_9`, `external_calls=0`, and translation `I'm here.`
This confirms processing is local and makes no provider call.

The temporary direct-worker request file was removed after the worker released
it. No media or model artifact was written inside the repository.

## Limits and operational evidence

This is a product smoke only. The retained clip has no authoritative
translation reference, so no CER/WER or translation-quality metric is claimed.
The English phrasing is model output and can be imperfect; the accepted
contract is executable local translation and invariant preservation, not a
semantic-quality threshold. The source-only ASR path, AutoSubs runtime and
external transcription implementation were not changed by this Goal.

Before installation D: had `16,599,044,096` free bytes; after installation it
had `15,599,267,840` free bytes (about `0.93 GiB` consumed, including runtime
and cached package). Processing is configured with network disabled.
