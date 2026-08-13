import re
from pathlib import Path


HTML_PATH = Path("app/static/simple/index.html")
JS_PATH = Path("app/static/simple/app.js")
CSS_PATH = Path("app/static/simple/styles.css")


def _sources() -> tuple[str, str, str]:
    return (
        HTML_PATH.read_text(encoding="utf-8"),
        JS_PATH.read_text(encoding="utf-8"),
        CSS_PATH.read_text(encoding="utf-8"),
    )


def test_task36_has_four_mutually_exclusive_views_without_tabs_or_rail():
    html, js, css = _sources()

    assert 'role="tablist"' not in html
    assert 'role="tabpanel"' not in html
    assert "status-rail" not in html
    assert "tab-strip" not in html
    assert "progress sidebar" not in html.lower()
    assert "const FLOW_VIEWS = [\"setup\", \"processing\", \"completed\", \"error\"]" in js
    assert 'document.body.dataset.flowState = next' in js
    assert 'element.hidden = view !== next' in js
    assert ".flow-view" in css
    assert ".status-rail" not in css

    markers = re.findall(r'data-flow-view="(setup|processing|completed|error)"', html)
    assert markers == ["setup", "processing", "completed", "error"]
    assert html.count('class="flow-view') == 4
    assert 'data-flow-view="setup" aria-labelledby="setupTitle">' in html
    assert 'data-flow-view="processing" aria-labelledby="processingTitle" hidden>' in html
    assert 'data-flow-view="completed" aria-labelledby="completedTitle" hidden>' in html
    assert 'data-flow-view="error" aria-labelledby="errorTitle" hidden>' in html


def test_task36_setup_is_one_button_and_advanced_features_are_collapsed():
    html, js, _ = _sources()
    setup = html.split('data-flow-view="setup"', 1)[1].split('data-flow-view="processing"', 1)[0]

    assert "Chọn video, kiểm tra thiết lập và bấm tạo. Mọi xử lý diễn ra trên máy." in setup
    assert 'id="videoPicker"' in setup
    assert 'id="sourceSummary"' in setup
    assert 'id="subtitleStyle"' in setup
    assert 'id="cleanupMode"' in setup
    assert "Chế độ phụ đề" in setup
    assert "Che phụ đề gốc phía dưới" not in setup
    # V1: Gemini mode is not exposed
    assert 'value="source_caption_gemini_translation"' not in setup
    assert "Gemini free tier" not in setup.lower()
    # V1: OCR mode is the default
    assert 'value="source_caption_ocr_translation"' in setup
    assert "Lưu thêm tệp phụ đề ASS" in setup
    assert 'id="startBtn" class="primary action-primary" type="button" disabled' in setup
    assert setup.count('class="primary action-primary"') == 1
    assert "Hãy chọn một video để tiếp tục." in setup
    assert '<details id="advancedOptions" class="advanced-disclosure">' in setup
    assert '<details id="advancedOptions" class="advanced-disclosure" open' not in setup
    assert setup.index('id="advancedOptions"') < setup.index('id="creativeImportText"')
    assert setup.index('id="advancedOptions"') < setup.index('id="recentRuns"')

    validation_block = js.split("async function validateSourcePath(sourcePath)", 1)[1].split(
        "async function uploadAndValidate",
        1,
    )[0]
    assert '"/api/simple/runs"' not in validation_block
    assert "/start" not in validation_block


def test_task36_processing_uses_backend_stage_polling_without_fake_percentage():
    html, js, _ = _sources()
    processing = html.split('data-flow-view="processing"', 1)[1].split('data-flow-view="completed"', 1)[0]

    for label in (
        "Đang tạo video có phụ đề",
        "Chuẩn bị âm thanh",
        "Nhận dạng lời nói",
        "Tạo phụ đề",
        "Xuất video",
        "Kiểm tra kết quả",
    ):
        assert label in f"{processing}\n{js}"
    assert 'id="processingStages"' in processing
    assert 'id="processingDetails"' in processing
    assert "Kiểm tra khả năng cục bộ" in processing + js
    for stage in ("checking_runtime", "downloading_autosubs", "preparing_autosubs_model", "preparing_translation", "runtime_ready"):
        assert stage in js
    assert "%" not in processing
    assert "Math.random" not in js
    assert "setTimeout(() => render" not in js
    assert "refreshActiveRun" in js
    assert 'api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}`)' in js
    assert 'state.startApiCalls += 1' in js
    assert js.count('/start`, {') == 1


def test_task36_completed_and_error_views_fail_closed():
    html, js, _ = _sources()
    completed = html.split('data-flow-view="completed"', 1)[1].split('data-flow-view="error"', 1)[0]
    error = html.split('data-flow-view="error"', 1)[1]

    assert "Video đã hoàn tất" in completed
    assert 'id="previewVideo"' in completed
    assert "Mở thư mục kết quả" in completed
    assert "Tạo video mới" in completed
    assert "Phụ đề được tạo tự động" in completed
    assert "Chưa thể tạo video" in error
    assert 'id="retryRuntimeBtn"' in error
    assert "Quay lại thiết lập" in error
    assert '<details id="errorDetails"' in error
    assert 'run.result_eligible === true' in js
    assert 'run.result_validation?.status !== "FAIL"' in js
    assert '&& run.output?.url' in js
    assert "clearPreview();" in js
    assert 'run?.failure_category !== "runtime_readiness_failed"' in js
    assert 'startProcessing({ retryRuntime: true })' in js


def test_task36_non_processing_transitions_do_not_start_or_create_runs():
    _, js, _ = _sources()
    return_block = js.split("function returnToSetup({ clearSelection = false } = {})", 1)[1].split(
        "async function openRunReadOnly",
        1,
    )[0]
    latest_handler = js.split('$("latestResultBtn").addEventListener', 1)[1].split(
        '$("recentRuns").addEventListener',
        1,
    )[0]

    assert "api(" not in return_block
    assert "fetch(" not in return_block
    assert "/start" not in return_block
    assert "/api/simple/runs/retry" not in return_block
    assert "/start" not in latest_handler
    assert "location.reload" not in js
    assert 'returnToSetup({ clearSelection: true })' in js
    assert 'returnToSetup({ clearSelection: false })' in js


def test_task36_preserves_local_asr_import_and_invalid_history_contracts():
    html, js, _ = _sources()
    combined = f"{html}\n{js}"

    assert "/api/simple/source/upload" in js
    assert "/api/simple/runs/retry" in js
    assert "/creative/import/preview" in js
    assert "/creative/import/apply" in js
    assert "/tracks/active" in js
    assert "Kết quả không hợp lệ - Không có phụ đề" in js
    assert "isEligibleCompletedRun" in js
    assert "Phiên âm cục bộ" in combined
    assert "Translation line 1" not in combined
    assert "Translation line 2" not in combined
    assert "Translation line 3" not in combined
    assert "canonical_cues" not in combined


def test_task36_has_responsive_no_overflow_contract_and_clean_vietnamese():
    html, js, css = _sources()
    combined = f"{html}\n{js}"

    assert "width: min(860px, calc(100vw - 36px));" in css
    assert "@media (max-width: 720px)" in css
    assert "@media (max-width: 430px)" in css
    assert "width: 100%;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "/app.js?v=first-run-runtime" in html
    assert "/styles.css?v=task36b" in html
    for bad in ("\ufffd", "Ãƒ", "Ã‚", "Táº", "Ä‘"):
        assert bad not in combined
    for unexpected in (
        "Choose video",
        "Video is ready",
        "Output filename",
        "Final validation",
        "Progress stays visible",
    ):
        assert unexpected not in combined
