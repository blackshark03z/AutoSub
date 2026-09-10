from pathlib import Path


def test_production_monitor_surface_and_truthful_evidence_contract():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    css = Path("app/static/simple/styles.css").read_text(encoding="utf-8")
    workflow = Path("app/services/simple_workflow.py").read_text(encoding="utf-8")

    for required in (
        'id="monitorCaptionList"',
        'id="monitorMetrics"',
        'id="monitorEvidenceGrid"',
        'id="monitorWarning"',
        'id="resultQcSummary"',
        'role="log"',
        'aria-live="polite"',
    ):
        assert required in html

    for required in (
        "renderCaptionMonitor(run)",
        "renderMonitorEvidence(run)",
        "renderResultQc(run)",
        "provider.prevalidated_evidence_reused",
        "analysis.ocr_batches_completed",
        "analysis.translations_completed",
        "render.processed_seconds",
    ):
        assert required in js

    assert ".production-monitor-grid" in css
    assert "grid-template-columns: 1fr" in css
    assert ".activity-bar.determinate span" in css
    assert '"monitor": _monitor_summary(row, validation)' in workflow
    assert '"render_progress": render' in workflow
    assert "_run_ffmpeg_with_progress(" in workflow
    assert '"-progress"' in workflow
    assert '"pipe:1"' in workflow


def test_monitor_does_not_fake_total_percentage():
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    assert 'if (max > 0)' in js
    assert '"Chưa có counter định lượng"' in js
    assert 'Number.isFinite(renderPercent)' in js
