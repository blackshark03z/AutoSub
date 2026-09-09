from pathlib import Path


HTML = Path("app/static/simple/index.html")
JS = Path("app/static/simple/app.js")
CSS = Path("app/static/simple/styles.css")


def _sources() -> tuple[str, str, str]:
    return (
        HTML.read_text(encoding="utf-8"),
        JS.read_text(encoding="utf-8"),
        CSS.read_text(encoding="utf-8"),
    )


def test_settings_is_a_separate_product_view_and_credentials_are_not_in_primary_flow():
    html, js, _ = _sources()
    setup = html.split('data-flow-view="setup"', 1)[1].split('data-flow-view="settings"', 1)[0]
    settings = html.split('data-flow-view="settings"', 1)[1].split('data-flow-view="processing"', 1)[0]

    assert 'id="settingsNavBtn"' in html
    assert 'id="settingsBackBtn"' in settings
    assert 'id="geminiKeysInput"' not in setup
    assert 'id="geminiRequirement"' in setup
    assert 'id="openGeminiSettingsBtn"' in setup
    assert 'id="geminiKeysInput"' in settings
    assert 'id="saveGeminiKeysBtn"' in settings
    assert 'id="geminiStoredCount"' in settings
    assert 'id="geminiLastSaveSummary"' in settings
    assert "function openSettings()" in js
    assert "function closeSettings()" in js


def test_gemini_key_form_has_persistent_label_help_and_live_feedback():
    html, js, _ = _sources()

    assert '<label class="field" for="geminiKeysInput">' in html
    assert 'aria-describedby="geminiKeysHelp"' in html
    assert 'id="geminiKeysHelp"' in html
    assert 'role="status" aria-live="polite"' in html
    assert "Mỗi key một dòng" in html
    assert "key trùng được bỏ qua" in html
    assert "danh sách cũ không bị ghi đè" in html
    assert "Tổng" in js
    assert ".split(/\\r?\\n/)" in js
    assert "api_keys: keys" in js
    assert 'input.value = ""' in js
    assert 'input.setAttribute("aria-invalid", "true")' in js
    assert 'element.setAttribute("role", kind === "error" ? "alert" : "status")' in js


def test_view_transitions_manage_keyboard_focus_without_focusing_hidden_controls():
    html, js, _ = _sources()

    for heading_id in ("setupTitle", "appSettingsTitle", "processingTitle", "completedTitle", "errorTitle"):
        assert f'id="{heading_id}" tabindex="-1"' in html
    assert "function focusViewHeading(view)" in js
    assert 'focusViewHeading("settings")' in js
    assert '$("settingsNavBtn")?.focus({ preventScroll: true })' in js
    assert "if (previousView !== view) focusViewHeading(view);" in js


def test_visual_system_has_accessible_targets_focus_reflow_and_reduced_motion():
    _, _, css = _sources()

    assert "min-height: 44px;" in css
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--focus);" in css
    assert "@media (max-width: 720px)" in css
    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 340px)" in css
    assert "width: min(960px, calc(100vw - 32px));" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (forced-colors: active)" in css


def test_settings_layout_uses_scannable_single_column_sections_and_cards():
    html, _, css = _sources()

    for heading in ("Gemini", "Khả năng xử lý", "Ứng dụng"):
        assert heading in html
    assert "width: min(100%, 920px);" in css
    assert ".settings-section" in css
    assert ".settings-card" in css
    assert ".settings-card-header" in css
    assert ".settings-meta" in css
    assert "Segoe UI Variable" in css
