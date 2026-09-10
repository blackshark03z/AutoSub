const state = {
  view: "setup",
  returnView: "setup",
  run: null,
  latestValidRun: null,
  sourcePath: "",
  validatedSource: null,
  validationTimer: null,
  pollTimer: null,
  starting: false,
  startApiCalls: 0,
  readiness: null,
};

const FLOW_VIEWS = ["setup", "settings", "processing", "completed", "error"];
const VIEW_IDS = {
  setup: "setupView",
  settings: "settingsView",
  processing: "processingView",
  completed: "completedView",
  error: "errorView",
};

const AUDIO_WORKFLOW_STEPS = [
  { id: "runtime", label: "Kiểm tra khả năng cục bộ", stages: ["checking_runtime", "downloading_autosubs", "preparing_autosubs_model", "preparing_translation", "runtime_ready"] },
  { id: "prepare", label: "Chuẩn bị âm thanh", stages: ["checking_video"] },
  { id: "recognize", label: "Nhận dạng lời nói", stages: ["analysing_dialogue"] },
  { id: "subtitles", label: "Tạo phụ đề", stages: ["preparing_english_subtitles", "cleaning_dialogue_subtitles"] },
  { id: "render", label: "Xuất video", stages: ["rendering_video"] },
  { id: "verify", label: "Kiểm tra kết quả", stages: ["verifying_result"] },
];

const OCR_WORKFLOW_STEPS = [
  { id: "runtime", label: "Kiểm tra OCR và Gemini", stages: ["checking_runtime"] },
  { id: "recognize", label: "Đọc và dịch phụ đề", stages: ["analysing_dialogue"] },
  { id: "subtitles", label: "Chuẩn bị phụ đề", stages: ["preparing_english_subtitles", "cleaning_dialogue_subtitles"] },
  { id: "render", label: "Xuất video", stages: ["rendering_video"] },
  { id: "verify", label: "Kiểm tra kết quả", stages: ["verifying_result"] },
];

const STAGE_LABELS = {
  checking_runtime: "Đang kiểm tra khả năng cục bộ",
  downloading_autosubs: "Đang tải AutoSubs cục bộ",
  preparing_autosubs_model: "Đang chuẩn bị mô hình AutoSubs small",
  preparing_translation: "Đang chuẩn bị dịch Trung → Anh ngoại tuyến",
  runtime_ready: "Khả năng cục bộ đã sẵn sàng",
  checking_video: "Đang chuẩn bị âm thanh",
  analysing_dialogue: "Đang nhận dạng lời nói",
  preparing_english_subtitles: "Đang tạo phụ đề",
  cleaning_dialogue_subtitles: "Đang tạo phụ đề",
  rendering_video: "Đang xuất video",
  verifying_result: "Đang kiểm tra kết quả",
};

const TRACK_TYPE_LABELS = {
  translation: "Bản phiên âm",
  creative: "Nội dung sáng tạo",
  imported: "Nội dung đã nhập",
};

const $ = (id) => document.getElementById(id);

function captionMode(run = null) {
  return run?.settings?.caption_mode || $("cleanupMode")?.value || "external_audio_transcription";
}

function isOcrMode(run = null) {
  return captionMode(run) === "source_caption_ocr_translation";
}

function workflowSteps(run = null) {
  return isOcrMode(run) ? OCR_WORKFLOW_STEPS : AUDIO_WORKFLOW_STEPS;
}

function stageLabel(run, stageId) {
  if (!isOcrMode(run)) return STAGE_LABELS[stageId];
  const ocrLabels = {
    checking_runtime: "Đang kiểm tra OCR và Gemini",
    analysing_dialogue: "Đang đọc phụ đề và dịch bằng Gemini",
    preparing_english_subtitles: "Đang chuẩn bị phụ đề tiếng Anh",
    cleaning_dialogue_subtitles: "Đang chuẩn bị phụ đề để xuất video",
    rendering_video: "Đang xuất video",
    verifying_result: "Đang kiểm tra kết quả",
  };
  return ocrLabels[stageId] || STAGE_LABELS[stageId];
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || {};
    const error = new Error(detail.message || detail.title || "Đã xảy ra lỗi.");
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "Không rõ";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "Không rõ";
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${minutes}:${String(secs).padStart(2, "0")}`;
}

function setMessage(text, error = false) {
  $("userMessage").textContent = text || "";
  $("userMessage").classList.toggle("error", error);
}

function setResultMessage(text, error = false) {
  $("resultMessage").textContent = text || "";
  $("resultMessage").classList.toggle("error", error);
}

function setFlowView(nextView) {
  const next = FLOW_VIEWS.includes(nextView) ? nextView : "setup";
  state.view = next;
  FLOW_VIEWS.forEach((view) => {
    const element = $(VIEW_IDS[view]);
    if (element) element.hidden = view !== next;
  });
  const settingsActive = next === "settings";
  if (settingsActive) {
    $("settingsNavBtn")?.setAttribute("aria-current", "page");
    $("homeNavBtn")?.removeAttribute("aria-current");
  } else {
    $("homeNavBtn")?.setAttribute("aria-current", "page");
    $("settingsNavBtn")?.removeAttribute("aria-current");
  }
  document.body.dataset.flowState = next;
  window.scrollTo({ top: 0, behavior: "auto" });
}

function focusViewHeading(view) {
  const headingId = {
    setup: "setupTitle",
    settings: "appSettingsTitle",
    processing: "processingTitle",
    completed: "completedTitle",
    error: "errorTitle",
  }[view];
  if (!headingId) return;
  requestAnimationFrame(() => $(headingId)?.focus({ preventScroll: true }));
}

function openSettings() {
  if (state.view !== "settings") state.returnView = state.view;
  setFlowView("settings");
  focusViewHeading("settings");
  renderReadiness().catch(handleActionError);
}

function closeSettings() {
  const derived = deriveFlowView(state.run);
  const target = state.returnView === "settings" ? derived : state.returnView;
  const next = FLOW_VIEWS.includes(target) && target !== "settings" ? target : derived;
  setFlowView(next);
  requestAnimationFrame(() => $("settingsNavBtn")?.focus({ preventScroll: true }));
}

function isFailedRun(run) {
  return Boolean(run && (run.internal_state === "blocked" || run.internal_state === "failed" || run.failure_category));
}

function isEligibleCompletedRun(run) {
  return Boolean(
    run
    && ["completed", "approved"].includes(run.internal_state)
    && run.result_eligible === true
    && run.result_validation?.status !== "FAIL"
    && run.output?.url,
  );
}

function deriveFlowView(run, { explicitNew = false } = {}) {
  if (explicitNew || !run) return "setup";
  if (isFailedRun(run)) return "error";
  if (run.internal_state === "processing") return "processing";
  if (isEligibleCompletedRun(run)) return "completed";
  return "setup";
}

function renderSource(source) {
  if (!source) {
    $("sourceSummary").innerHTML = "";
    return;
  }
  const resolution = source.resolution || {};
  $("sourceSummary").innerHTML = [
    ["Tên tệp", source.filename || "Không rõ"],
    ["Thời lượng", formatDuration(Number(source.duration_seconds))],
    ["Độ phân giải", `${resolution.width || "?"} × ${resolution.height || "?"}`],
    ["Dung lượng", formatBytes(Number(source.size_bytes))],
  ].map(([label, value]) => `
    <div class="summary-card">
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
}

function updateStartAction() {
  const missingGemini = isOcrMode() && state.readiness && state.readiness?.gemini_runtime?.configured !== true;
  const ready = Boolean(state.validatedSource && state.sourcePath && !state.starting && !missingGemini);
  $("startBtn").disabled = !ready;
  $("startReason").textContent = state.starting
    ? "Đang bắt đầu lượt xử lý..."
    : missingGemini
      ? "Hãy lưu Gemini API key để dùng chế độ OCR + Gemini."
      : ready
        ? "Video đã sẵn sàng. Một lần bấm sẽ tạo phụ đề và xuất video."
        : "Hãy chọn một video để tiếp tục.";
}

function runtimeComponents(readiness) {
  if (isOcrMode()) {
    const ocr = readiness?.ocr_runtime || {};
    return [
      ["PaddleOCR", {
        state: ocr.available === true ? "ready" : "missing",
        message: ocr.actionable_fix_message || "Bộ đọc phụ đề OCR chưa sẵn sàng.",
      }],
      ["Gemini", readiness?.gemini_runtime],
    ];
  }
  return [
    ["AutoSubs", readiness?.autosubs_runtime],
    ["Mô hình AutoSubs small", readiness?.autosubs_small_model],
    ["Argos Translate", readiness?.argos_runtime],
    ["Mô hình Trung → Anh", readiness?.argos_zh_en_model],
  ];
}

function clearPreview() {
  const video = $("previewVideo");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("outputSummary").innerHTML = "";
  setResultMessage("");
}

function activeTrack(run) {
  const summary = run?.subtitle_tracks || {};
  return (summary.tracks || []).find((track) => track.track_id === summary.active_track_id || track.active);
}

function subtitleSourceLabel(run) {
  const provenance = String(
    run?.result_validation?.subtitle_content_validation?.provenance || "",
  ).toLocaleLowerCase("vi");
  if (provenance === "local_transcription") return "Phiên âm cục bộ";
  if (provenance === "user_import") return "Nội dung đã nhập";
  if (provenance === "user_authored") return "Nội dung sáng tạo";
  const track = activeTrack(run);
  const name = String(track?.display_name || "").toLocaleLowerCase("vi");
  if (name.includes("offline") || name.includes("local") || name.includes("phiên âm")) {
    return "Phiên âm cục bộ";
  }
  if (track?.track_type === "imported") return "Nội dung đã nhập";
  if (track?.track_type === "creative") return "Nội dung sáng tạo";
  return "Phụ đề đã xác minh";
}

function formatMonitorTime(milliseconds) {
  const seconds = Math.max(0, Number(milliseconds || 0) / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}.${String(Math.floor((seconds % 1) * 10))}`;
}

function monitorMetric(label, value, detail = "") {
  return `<div class="monitor-metric"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
}

function progressMetric(label, completed, total) {
  const done = Number(completed || 0);
  const max = Number(total || 0);
  if (max > 0) return monitorMetric(label, `${done}/${max}`, `${Math.min(100, Math.round(done * 100 / max))}%`);
  if (done > 0) return monitorMetric(label, String(done), "Đang cập nhật");
  return "";
}

function renderCaptionMonitor(run) {
  const captions = run?.monitor?.captions || [];
  $("monitorCaptionCount").textContent = `${captions.length} câu`;
  $("monitorCaptionSummary").textContent = captions.length
    ? "Danh sách giới hạn các câu mới nhất mà pipeline đã thực sự nhận diện/giải quyết."
    : "Chưa có phụ đề để hiển thị. Dữ liệu sẽ xuất hiện ngay khi pipeline tạo được interval có thời gian.";
  if (!captions.length) {
    $("monitorCaptionList").innerHTML = `<div class="monitor-empty"><strong>Đang chờ dữ liệu phụ đề</strong><span>Khi OCR hoặc nhận dạng lời nói tạo được câu có mốc thời gian, chúng sẽ xuất hiện tại đây.</span></div>`;
    return;
  }
  $("monitorCaptionList").innerHTML = captions.map((cue) => {
    const confidence = Number(cue.ocr_confidence);
    const hasConfidence = Number.isFinite(confidence);
    const confidenceLabel = hasConfidence ? `OCR ${Math.round(confidence * (confidence <= 1 ? 100 : 1))}%` : "";
    const attention = Boolean(cue.attention);
    return `<article class="monitor-caption ${attention ? "attention" : ""}">
      <div class="monitor-caption-meta"><strong>${escapeHtml(formatMonitorTime(cue.start_ms))} → ${escapeHtml(formatMonitorTime(cue.end_ms))}</strong>${confidenceLabel ? `<span>${escapeHtml(confidenceLabel)}</span>` : ""}${attention ? '<span class="attention-label">Cần chú ý</span>' : ""}</div>
      ${cue.source_text ? `<p class="monitor-source"><span>Gốc</span>${escapeHtml(cue.source_text)}</p>` : ""}
      <p class="monitor-translation"><span>${isOcrMode(run) ? "English" : "Phụ đề"}</span>${escapeHtml(cue.translated_text || "Đang chờ bản dịch...")}</p>
    </article>`;
  }).join("");
}

function renderMonitorEvidence(run) {
  const monitor = run?.monitor || {};
  const analysis = monitor.analysis || run?.analysis_progress || {};
  const provider = monitor.provider || {};
  const render = monitor.render_progress || {};
  const metrics = [
    progressMetric("Frame đã lấy", analysis.sampled_frames_completed, analysis.sampled_frames_total),
    progressMetric("Crop phụ đề", analysis.dense_crops_completed, analysis.dense_crops_total),
    progressMetric("OCR batch", analysis.ocr_batches_completed, analysis.ocr_batches_total),
    progressMetric("Dịch", analysis.translations_completed, analysis.translations_total),
  ].filter(Boolean);
  if (isOcrMode(run)) {
    metrics.push(monitorMetric("Gemini", `${Number(provider.request_count || 0)} request`, `${Number(provider.cache_hits || 0)} cache hit · ${Number(provider.retry_count || 0)} retry`));
  }
  if (render.duration_seconds > 0) {
    metrics.push(monitorMetric("Render", `${formatDuration(Number(render.processed_seconds || 0))}/${formatDuration(Number(render.duration_seconds))}`, render.speed || "Đang xuất"));
  }
  $("monitorMetrics").innerHTML = metrics.join("") || monitorMetric("Tiến trình", "Đang khởi tạo", "Chưa có counter định lượng");

  const modeText = isOcrMode(run) ? "PaddleOCR → Gemini" : "AutoSubs → Argos";
  $("monitorMode").textContent = modeText;
  $("monitorElapsed").textContent = `Đã chạy ${formatDuration(Number(monitor.elapsed_seconds || 0))}`;
  const age = Number(monitor.activity_age_seconds);
  const stalled = Boolean(monitor.stalled);
  $("monitorActivity").textContent = stalled ? "Có dấu hiệu đứng" : Number.isFinite(age) ? `Hoạt động ${Math.round(age)}s trước` : "Đang hoạt động";
  $("monitorActivity").classList.toggle("warning", stalled);
  $("monitorActivity").classList.toggle("neutral", !stalled);

  const evidence = [
    ["Worker", stalled ? "Cần kiểm tra" : (monitor.worker_state || "Đang chạy")],
    ["Provider", provider.name || (isOcrMode(run) ? "Gemini" : "Chỉ chạy local")],
    ["Model", provider.model || (isOcrMode(run) ? "Đang xác định" : "Không áp dụng")],
    ["Interval", String(Number(analysis.caption_intervals_total || 0))],
  ];
  $("monitorEvidenceGrid").innerHTML = evidence.map(([label, value]) => `<div><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
  $("monitorWarning").textContent = stalled
    ? "Không có tiến triển vật chất gần đây. AutoSub sẽ không báo hoàn tất nếu worker đã dừng hoặc kết quả chưa hợp lệ."
    : provider.prevalidated_evidence_reused
      ? "Đang tái sử dụng kết quả Gemini đã được xác minh/cache; đây không phải request mới."
      : "Các số liệu trên lấy từ evidence của run hiện tại; không dùng phần trăm tổng giả.";

  const renderBar = $("processingProgress");
  const renderPercent = Number(render.percentage);
  renderBar.classList.toggle("determinate", Number.isFinite(renderPercent));
  if (Number.isFinite(renderPercent)) {
    renderBar.setAttribute("role", "progressbar");
    renderBar.setAttribute("aria-valuemin", "0");
    renderBar.setAttribute("aria-valuemax", "100");
    renderBar.setAttribute("aria-valuenow", String(Math.round(renderPercent)));
    renderBar.querySelector("span").style.width = `${Math.max(0, Math.min(100, renderPercent))}%`;
  } else {
    renderBar.removeAttribute("role");
    renderBar.removeAttribute("aria-valuenow");
    renderBar.querySelector("span").style.width = "32%";
  }
}

function renderResultQc(run) {
  const qc = run?.monitor?.qc || {};
  const values = [
    ["Đã phát hiện", qc.detected_caption_count || "—"],
    ["Đã giải quyết", qc.resolved_caption_count || "—"],
    ["ASS Dialogue", qc.rendered_dialogue_count ?? "—"],
    ["Mất câu", qc.caption_loss_detected ? "Có — bị chặn" : "Không phát hiện"],
    ["Nội dung", qc.content_validation || run?.result_validation?.status || "—"],
    ["Output", qc.output_eligible ? "Hợp lệ" : "Chưa hợp lệ"],
  ];
  $("resultQcSummary").innerHTML = values.map(([label, value]) => `<div class="qc-card ${String(value).includes("bị chặn") ? "bad" : ""}"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
}

function renderProcessing(run) {
  const progress = run?.progress || {};
  const currentStage = progress.current_stage || "checking_video";
  const completedStages = new Set(progress.completed_stages || []);
  const failed = isFailedRun(run);
  $("processingFilename").textContent = run?.source?.filename || state.validatedSource?.filename || "Video đã chọn";
  $("processingCurrent").textContent = failed
    ? "Quá trình xử lý đã dừng."
    : progress.status_label || stageLabel(run, currentStage) || "Đang xử lý";
  $("processingDescription").textContent = failed
    ? "Ứng dụng đã dừng an toàn và không công bố kết quả chưa hợp lệ."
    : isOcrMode(run)
      ? "PaddleOCR đọc phụ đề trên máy; Gemini dùng kết nối Internet để sửa/giải nghĩa và dịch nội dung."
      : currentStage.includes("runtime") || currentStage.includes("autosubs") || currentStage === "preparing_translation"
        ? "AutoSub đang kiểm tra hoặc chuẩn bị các khả năng cục bộ cần thiết. Bạn không cần tải hay cấu hình thủ công."
        : "Trạng thái được cập nhật từ tiến trình xử lý thực trên máy.";
  $("processingTechnicalOutput").textContent = JSON.stringify(run || {}, null, 2);
  renderCaptionMonitor(run);
  renderMonitorEvidence(run);
  $("processingStages").innerHTML = workflowSteps(run).map((step) => {
    const active = step.stages.includes(currentStage);
    const done = run?.internal_state === "completed"
      || step.stages.every((stageId) => completedStages.has(stageId));
    const className = failed && active ? "failed" : done ? "done" : active ? "current" : "pending";
    const status = failed && active
      ? "Có lỗi"
      : done
        ? "Đã hoàn tất"
        : active
          ? "Đang thực hiện"
          : "Chưa bắt đầu";
    return `
      <li class="${className}"${active && !failed ? ' aria-current="step"' : ""}>
        <span>${escapeHtml(step.label)}</span>
        <small>${status}</small>
      </li>
    `;
  }).join("");
}

function renderCompleted(run) {
  if (!isEligibleCompletedRun(run)) {
    renderError(run, "Kết quả chưa vượt qua kiểm tra cuối.");
    return;
  }
  $("previewVideo").src = run.output.url;
  $("outputSummary").innerHTML = [
    ["Tên tệp", run.output.filename || "final_video.mp4"],
    ["Thời lượng", formatDuration(Number(run.source?.duration_seconds))],
    ["Độ phân giải", `${run.source?.resolution?.width || "?"} × ${run.source?.resolution?.height || "?"}`],
    ["Nguồn phụ đề", subtitleSourceLabel(run)],
    ["Kiểm tra cuối", run.output?.hash ? "Đạt" : "Chưa xác minh"],
  ].map(([label, value]) => `
    <div class="summary-card">
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
  renderResultQc(run);
  setResultMessage(run.subtitle_tracks?.operator_notice || "");
}

function friendlyFailureMessage(run, fallback = "") {
  if (fallback) return fallback;
  if (run?.failure_detail?.message) return run.failure_detail.message;
  const messages = {
    subtitle_source_unavailable: "Không tìm thấy nguồn phiên âm khả dụng cho video này.",
    insufficient_disk_space: "Không đủ dung lượng trống để xử lý video.",
    source_missing: "Video nguồn không còn ở vị trí đã chọn.",
    render_failed: "Không thể xuất video. Tệp nguồn vẫn được giữ nguyên.",
    invalid_completed_result: "Kết quả không hợp lệ nên không được hiển thị.",
    CAPTION_OCR_RUNTIME_FAILED: "Bộ đọc phụ đề OCR chưa sẵn sàng. Hãy kiểm tra lại rồi thử lại.",
    gemini_readiness_failed: "Chưa thể kết nối Gemini. Kiểm tra API key hoặc Internet rồi thử lại.",
  };
  return messages[run?.failure_category]
    || run?.result_validation?.message
    || "Quá trình xử lý chưa thể hoàn tất. Video nguồn và thiết lập của bạn vẫn được giữ nguyên.";
}

function renderError(run, fallbackMessage = "") {
  clearPreview();
  $("errorMessage").textContent = friendlyFailureMessage(run, fallbackMessage);
  $("technicalOutput").textContent = JSON.stringify(run || { message: fallbackMessage }, null, 2);
  const category = String(run?.failure_category || "");
  const retryableRuntimeFailure = ["runtime_readiness_failed", "CAPTION_OCR_RUNTIME_FAILED", "gemini_readiness_failed"].includes(category)
    || category.startsWith("GEMINI_");
  $("retryRuntimeBtn").hidden = !retryableRuntimeFailure;
  $("retryRuntimeBtn").textContent = category === "CAPTION_OCR_RUNTIME_FAILED"
    ? "Thử kiểm tra OCR lại"
    : (category === "gemini_readiness_failed" || category.startsWith("GEMINI_"))
      ? "Thử kết nối Gemini lại"
      : "Thử chuẩn bị lại";
}

function renderRun(run, options = {}) {
  state.run = run || null;
  if (run?.source) {
    state.sourcePath = run.source.path || state.sourcePath;
    state.validatedSource = run.source;
  }
  const view = options.forceView || deriveFlowView(run, { explicitNew: options.explicitNew });
  if (view === "processing") renderProcessing(run);
  if (view === "completed") renderCompleted(run);
  if (view === "error") renderError(run, options.errorMessage || "");
  const previousView = state.view;
  setFlowView(view);
  if (previousView !== view) focusViewHeading(view);
  updateStartAction();
}

function readinessCard(label, value, pass = true) {
  return `
    <div class="summary-card ${pass ? "ready" : "needs-action"}">
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

async function renderReadiness() {
  try {
    const readiness = await api("/api/simple/runtime/readiness");
    state.readiness = readiness;
    const mode = captionMode();
    const ready = readiness?.mode_status?.[mode]?.status === "ready";
    const gemini = readiness?.gemini_runtime || {};
    const keyCount = Number(gemini.count || 0);

    $("readinessPill").textContent = ready ? "Sẵn sàng" : "Cần chuẩn bị";
    $("readinessPill").classList.toggle("pass", ready);
    $("readinessPill").classList.toggle("error", !ready);
    $("readinessSummary").innerHTML = runtimeComponents(readiness).map(([label, component]) => {
      const componentReady = component?.state === "ready";
      return readinessCard(label, componentReady ? "Sẵn sàng" : component?.message || "Cần chuẩn bị", componentReady);
    }).join("");

    $("geminiSettingsPill").textContent = keyCount > 0 ? `${keyCount} key` : "Chưa cấu hình";
    $("geminiSettingsPill").classList.toggle("pass", keyCount > 0);
    $("geminiSettingsPill").classList.toggle("error", keyCount === 0);
    $("geminiStoredCount").textContent = `Đã lưu ${keyCount} key`;
    $("geminiRequirementText").textContent = keyCount > 0
      ? `Đã có ${keyCount} key. Gemini sẽ được kiểm tra kết nối trước khi OCR bắt đầu.`
      : "Chưa có key. Mở Cài đặt để thêm key trước khi chạy OCR + Gemini.";

    $("readinessDetails").textContent = JSON.stringify({
      mode,
      status: ready ? "ready" : "not_ready",
      gemini_key_count: keyCount,
      gemini_storage: gemini.storage_path || "secrets\\gemini_api.txt",
      action: ready
        ? "Các thành phần cần cho chế độ hiện tại đã sẵn sàng."
        : isOcrMode()
          ? "OCR cần PaddleOCR trên máy và ít nhất một Gemini API key; kết nối model được kiểm tra trước khi phân tích video."
          : "AutoSub sẽ chuẩn bị các khả năng cục bộ cần thiết khi bắt đầu.",
    }, null, 2);
    updateModeUi();
    updateStartAction();
  } catch (error) {
    $("readinessPill").textContent = "Cần kiểm tra";
    $("readinessPill").classList.remove("pass");
    $("readinessPill").classList.add("error");
    $("readinessSummary").innerHTML = readinessCard("Ứng dụng", "Chưa sẵn sàng", false);
    $("readinessDetails").textContent = error.message;
  }
}

function applyValidation(validation, sourcePath) {
  if (validation.status !== "PASS") {
    const error = validation.error || {};
    state.validatedSource = null;
    state.run = null;
    $("validationPill").textContent = "Kiểm tra không đạt";
    $("validationPill").classList.remove("pass");
    renderSource(null);
    setMessage(`${error.title || "Không hỗ trợ video"}: ${error.message || "Hãy chọn video khác."}`, true);
    updateStartAction();
    return false;
  }
  state.sourcePath = sourcePath;
  state.validatedSource = validation.source;
  state.run = null;
  $("sourcePath").value = sourcePath;
  $("validationPill").textContent = "Video hợp lệ";
  $("validationPill").classList.add("pass");
  renderSource(validation.source);
  $("resourceInfo").textContent = validation.disk
    ? `Dung lượng làm việc dự kiến: ${formatBytes(validation.disk.estimated_working_bytes)}; còn trống: ${formatBytes(validation.disk.free_bytes)}.`
    : "Video đã vượt qua kiểm tra đầu vào.";
  setMessage("Video đã sẵn sàng. Chưa có lượt xử lý nào được tạo.");
  updateStartAction();
  return true;
}

async function validateSourcePath(sourcePath) {
  const normalized = String(sourcePath || "").trim();
  if (!normalized) return;
  state.sourcePath = normalized;
  state.validatedSource = null;
  state.run = null;
  $("sourcePath").value = normalized;
  $("startBtn").disabled = true;
  $("validationPill").textContent = "Đang kiểm tra";
  $("validationPill").classList.remove("pass");
  setMessage("Đang kiểm tra video...");
  const validation = await api("/api/simple/source/validate", {
    method: "POST",
    body: JSON.stringify({ source_path: normalized }),
  });
  applyValidation(validation, normalized);
}

async function uploadAndValidate(file) {
  if (!file) return;
  state.run = null;
  state.validatedSource = null;
  $("startBtn").disabled = true;
  $("validationPill").textContent = "Đang nhập video";
  setMessage("Đang nhập video vào vùng dữ liệu cục bộ...");
  const response = await fetch("/api/simple/source/upload", {
    method: "POST",
    headers: {
      "content-type": "application/octet-stream",
      "x-filename": file.name || "selected-video.mp4",
    },
    body: file,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || {};
    throw new Error(detail.message || detail.title || "Không thể nhập video.");
  }
  const validation = payload.validation || await api("/api/simple/source/validate", {
    method: "POST",
    body: JSON.stringify({ source_path: payload.uploaded_path }),
  });
  applyValidation(validation, payload.uploaded_path);
  setMessage("Video đã được nhập cục bộ và sẵn sàng. Tệp gốc không bị thay đổi.");
}

function schedulePathValidation() {
  clearTimeout(state.validationTimer);
  const sourcePath = $("sourcePath").value.trim();
  if (!sourcePath) {
    state.sourcePath = "";
    state.validatedSource = null;
    state.run = null;
    renderSource(null);
    updateStartAction();
    return;
  }
  state.validationTimer = setTimeout(() => {
    validateSourcePath(sourcePath).catch((error) => {
      setMessage(error.message, true);
      updateStartAction();
    });
  }, 500);
}

function collectSettings() {
  return {
    target_language: $("targetLanguage").value,
    copy_source_into_workspace: $("copySource").checked,
    caption_mode: $("cleanupMode").value,
  };
}

async function ensureRun() {
  if (!state.validatedSource || !state.sourcePath) {
    throw new Error("Hãy chọn và kiểm tra một video trước.");
  }
  if (state.run && ["selected", "processing"].includes(state.run.internal_state)) {
    return state.run;
  }
  const created = await api("/api/simple/runs", {
    method: "POST",
    body: JSON.stringify({ source_path: state.sourcePath, settings: collectSettings() }),
  });
  let run = created.run;
  if (run?.reused && ["completed", "approved"].includes(run.internal_state)) {
    const retried = await api("/api/simple/runs/retry", {
      method: "POST",
      body: JSON.stringify({
        source_path: state.sourcePath,
        retry_parent_run_id: run.run_id,
        settings: collectSettings(),
      }),
    });
    run = retried.run;
  }
  state.run = run;
  return run;
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function refreshActiveRun() {
  if (!state.run?.run_id) return null;
  const payload = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}`);
  const run = payload.run;
  if (run.internal_state === "processing") {
    state.run = run;
    renderProcessing(run);
  } else {
    stopPolling();
    renderRun(run);
  }
  return run;
}

async function startProcessing({ retryRuntime = false } = {}) {
  if (state.starting || !state.validatedSource) return;
  state.starting = true;
  updateStartAction();
  try {
    const run = retryRuntime && state.run?.run_id
      ? (await api("/api/simple/runs/retry", {
        method: "POST",
        body: JSON.stringify({
          source_path: state.sourcePath,
          retry_parent_run_id: state.run.run_id,
          settings: collectSettings(),
        }),
      })).run
      : await ensureRun();
    const provisional = {
      ...run,
      internal_state: "processing",
      progress: run.progress || {
        current_stage: isOcrMode(run) ? "checking_runtime" : "checking_video",
        completed_stages: [],
        percentage: null,
        status_label: "Đang bắt đầu xử lý",
      },
    };
    state.run = run;
    renderProcessing(provisional);
    setFlowView("processing");
    state.startApiCalls += 1;
    document.body.dataset.startApiCalls = String(state.startApiCalls);
    const startRequest = api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/start`, {
      method: "POST",
      headers: { "x-idempotency-key": `simple-start-${run.run_id}` },
      body: "{}",
    });
    stopPolling();
    state.pollTimer = setInterval(() => {
      refreshActiveRun().catch(() => {});
    }, 900);
    const started = await startRequest;
    state.run = started.run;
    renderProcessing(started.run);
    await renderRecent();
  } catch (error) {
    stopPolling();
    let failedRun = state.run;
    if (state.run?.run_id) {
      try {
        failedRun = (await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}`)).run;
      } catch (_) {
        // Preserve the original user-facing error when status retrieval is unavailable.
      }
    }
    renderRun(failedRun, { forceView: "error", errorMessage: error.message });
  } finally {
    state.starting = false;
    updateStartAction();
  }
}

function preserveSelectionFromRun(run) {
  if (!run?.source) return;
  state.sourcePath = run.source.path || "";
  state.validatedSource = run.source;
  $("sourcePath").value = state.sourcePath;
  renderSource(run.source);
  $("validationPill").textContent = "Video hợp lệ";
  $("validationPill").classList.add("pass");
}

function returnToSetup({ clearSelection = false } = {}) {
  stopPolling();
  if (clearSelection) {
    state.run = null;
    state.sourcePath = "";
    state.validatedSource = null;
    $("sourcePath").value = "";
    $("videoPicker").value = "";
    $("validationPill").textContent = "Chưa chọn video";
    $("validationPill").classList.remove("pass", "error");
    renderSource(null);
    setMessage("Hãy chọn một video để bắt đầu. Các kết quả cũ vẫn được giữ nguyên.");
  } else {
    preserveSelectionFromRun(state.run);
    state.run = null;
    setMessage("Video và thiết lập đã được giữ lại. Bạn có thể thử lại khi sẵn sàng.");
  }
  clearPreview();
  setFlowView("setup");
  updateStartAction();
}

async function openRunReadOnly(runId) {
  const payload = await api(`/api/simple/runs/${encodeURIComponent(runId)}`);
  if (payload.run?.internal_state === "processing") {
    renderRun(payload.run, { forceView: "processing" });
    return;
  }
  if (isEligibleCompletedRun(payload.run)) {
    renderRun(payload.run, { forceView: "completed" });
    return;
  }
  if (isFailedRun(payload.run)) {
    renderRun(payload.run, { forceView: "error" });
    return;
  }
  setMessage("Lượt chạy này chưa có video hoàn chỉnh hợp lệ.", true);
  setFlowView("setup");
}

async function renderRecent() {
  const payload = await api("/api/simple/runs/recent");
  const runs = payload.runs || [];
  state.latestValidRun = runs.find(isEligibleCompletedRun) || null;
  $("latestResultBtn").hidden = !state.latestValidRun;
  $("recentRuns").innerHTML = runs.slice(0, 5).map((run) => {
    const valid = Boolean(run.output?.url && run.result_eligible && isEligibleCompletedRun(run));
    const invalid = run.result_validation?.status === "FAIL";
    const status = invalid
      ? "Kết quả không hợp lệ - Không có phụ đề hoặc nội dung chưa được xác minh"
      : valid
        ? "Video hoàn chỉnh"
        : isFailedRun(run)
          ? "Đã dừng an toàn"
          : "Chưa hoàn tất";
    return `
      <div class="recent-card">
        <strong>${escapeHtml(run.source?.filename || "Video")}</strong>
        <small>${escapeHtml(run.created_at || "")}</small>
        <span>${escapeHtml(status)}</span>
        ${valid ? `<button type="button" aria-label="Kết quả gần nhất: Xem video" data-open-run="${escapeHtml(run.run_id)}">Xem video</button>` : ""}
      </div>
    `;
  }).join("") || '<p class="message">Chưa có video gần đây.</p>';
}

function trackTypeLabel(trackType) {
  return TRACK_TYPE_LABELS[trackType] || "Nội dung";
}

function trackDisplayName(track) {
  const name = String(track?.display_name || "").trim();
  return name || trackTypeLabel(track?.track_type);
}

async function renderTracks() {
  if (!state.run?.run_id) return;
  const payload = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/tracks`);
  const select = $("contentTrackSelect");
  select.innerHTML = (payload.tracks || []).map((track) => (
    `<option value="${escapeHtml(track.track_id)}">${escapeHtml(trackDisplayName(track))}</option>`
  )).join("");
  if (payload.active_track_id) select.value = payload.active_track_id;
  const active = (payload.tracks || []).find((track) => track.track_id === payload.active_track_id);
  $("trackPill").textContent = active ? `Đang dùng: ${trackDisplayName(active)}` : "Chưa có bản nội dung";
  $("creativeCueEditor").innerHTML = "";
  if (active && active.track_type !== "translation") {
    const track = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/tracks/${encodeURIComponent(active.track_id)}`);
    $("creativeCueEditor").innerHTML = (track.items || []).map((item) => `
      <div class="recent-card">
        <strong>${escapeHtml(item.cue_id)}</strong>
        <textarea data-cue-id="${escapeHtml(item.cue_id)}">${escapeHtml(item.text)}</textarea>
        <button type="button" data-save-cue="${escapeHtml(item.cue_id)}">Lưu câu</button>
      </div>
    `).join("");
  }
}

async function ensureAdvancedRun() {
  const run = await ensureRun();
  state.run = run;
  await renderTracks();
  return run;
}

async function exportCreativeTemplate(format) {
  const run = await ensureAdvancedRun();
  const payload = await api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/creative/template?format=${format}`);
  $("creativeImportText").value = payload.content;
  $("creativePreview").textContent = `Đã xuất mẫu: ${payload.filename}\nSố câu: ${payload.cue_count}\nSHA-256: ${payload.sha256}`;
}

function creativeFormatMode() {
  const value = $("creativeImportFormat").value;
  if (value === "json") return { format: "json", mode: "cue_id", filename: "creative_script.json" };
  if (value === "txt-line") return { format: "txt", mode: "line_by_line", filename: "creative_script_lines.txt" };
  return { format: "txt", mode: "cue_id", filename: "creative_script.txt" };
}

async function previewCreativeImport() {
  const run = await ensureAdvancedRun();
  const selected = creativeFormatMode();
  const preview = await api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/creative/import/preview`, {
    method: "POST",
    body: JSON.stringify({
      content: $("creativeImportText").value,
      format: selected.format,
      mode: selected.mode,
      filename: selected.filename,
    }),
  });
  $("creativePreview").textContent = JSON.stringify({
    status: preview.status,
    matched_cues: preview.matched_cues,
    missing_cues: preview.missing_cues,
    unknown_cues: preview.unknown_cues,
    duplicate_cues: preview.duplicate_cues,
    empty_cues: preview.empty_cues,
    warnings: preview.warnings,
  }, null, 2);
}

async function applyCreativeImport(trackType = "creative") {
  const run = await ensureAdvancedRun();
  const selected = creativeFormatMode();
  const applied = await api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/creative/import/apply`, {
    method: "POST",
    body: JSON.stringify({
      content: $("creativeImportText").value,
      format: selected.format,
      mode: selected.mode,
      filename: selected.filename,
      track_type: trackType,
      display_name: trackType === "imported" ? "Nội dung đã nhập" : "Nội dung sáng tạo",
      fallback_policy: $("fallbackPolicy").value,
    }),
  });
  $("creativePreview").textContent = JSON.stringify({ status: applied.status, track_id: applied.track.track_id }, null, 2);
  await renderTracks();
}

async function setActiveTrack() {
  const run = await ensureAdvancedRun();
  await api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/tracks/active`, {
    method: "POST",
    body: JSON.stringify({
      track_id: $("contentTrackSelect").value,
      fallback_policy: $("fallbackPolicy").value,
    }),
  });
  await renderTracks();
}

async function saveCueEdit(cueId) {
  const run = await ensureAdvancedRun();
  const trackId = $("contentTrackSelect").value;
  const text = document.querySelector(`[data-cue-id="${CSS.escape(cueId)}"]`)?.value || "";
  await api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/tracks/${encodeURIComponent(trackId)}/items`, {
    method: "POST",
    body: JSON.stringify({ cue_id: cueId, text }),
  });
  await renderTracks();
}

async function approveResult() {
  if (!state.run?.run_id) return;
  const payload = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/approve`, {
    method: "POST",
    body: "{}",
  });
  renderRun(payload.run, { forceView: "completed" });
  setResultMessage("Kết quả đã được duyệt.");
}

async function rejectResult() {
  if (!state.run?.run_id) return;
  const payload = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/reject`, {
    method: "POST",
    body: "{}",
  });
  preserveSelectionFromRun(payload.run);
  state.run = null;
  setMessage("Kết quả được giữ nguyên để bạn có thể tạo lại với thiết lập đã chọn.");
  setFlowView("setup");
  updateStartAction();
}

async function saveCopy() {
  if (!state.run?.run_id) return;
  const payload = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/save-copy`, {
    method: "POST",
    body: JSON.stringify({ destination_folder: $("saveDestination").value.trim() }),
  });
  $("saveResult").textContent = JSON.stringify(payload, null, 2);
  setResultMessage("Đã lưu bản sao.");
}

async function restoreInitialState() {
  const params = new URLSearchParams(window.location.search);
  const requestedRun = params.get("run_id");
  const explicitNew = params.get("new") === "1";
  await renderRecent();
  if (requestedRun && !explicitNew) {
    await openRunReadOnly(requestedRun);
    return;
  }
  renderRun(null, { explicitNew: true });
  setMessage(explicitNew
    ? "Hãy chọn một video để bắt đầu. Các kết quả cũ vẫn được giữ nguyên."
    : "Chọn video để bắt đầu một lượt xử lý mới.");
}

function handleActionError(error) {
  setMessage(error.message || "Đã xảy ra lỗi.", true);
}

function updateModeUi() {
  const ocr = isOcrMode();
  $("geminiRequirement").hidden = !ocr;
  $("captionModeHelp").textContent = ocr
    ? "PaddleOCR đọc phụ đề trên máy; Gemini sửa/giải nghĩa và dịch sang tiếng Anh qua Internet."
    : "AutoSubs nhận dạng lời nói; Argos dịch Trung → Anh trên máy.";
  if (ocr && state.readiness) {
    const keyCount = Number(state.readiness?.gemini_runtime?.count || 0);
    $("geminiRequirementText").textContent = keyCount > 0
      ? `Đã có ${keyCount} key. Gemini sẽ được kiểm tra kết nối trước khi OCR bắt đầu.`
      : "Chưa có key. Mở Cài đặt để thêm key trước khi chạy OCR + Gemini.";
  }
  updateStartAction();
}

function setGeminiSettingsMessage(text, kind = "neutral") {
  const element = $("geminiSettingsMessage");
  element.textContent = text;
  element.classList.toggle("success", kind === "success");
  element.classList.toggle("error", kind === "error");
  element.setAttribute("role", kind === "error" ? "alert" : "status");
  element.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
}

async function saveGeminiKeys() {
  const input = $("geminiKeysInput");
  const keys = String(input.value || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (!keys.length) {
    input.setAttribute("aria-invalid", "true");
    setGeminiSettingsMessage("Hãy nhập ít nhất một Gemini API key, mỗi key một dòng.", "error");
    input.focus();
    return;
  }
  input.removeAttribute("aria-invalid");

  const button = $("saveGeminiKeysBtn");
  button.disabled = true;
  setGeminiSettingsMessage(`Đang kiểm tra và thêm ${keys.length} key...`);
  try {
    const result = await api("/api/simple/gemini/credentials", {
      method: "POST",
      body: JSON.stringify({ api_keys: keys }),
    });
    input.value = "";
    input.removeAttribute("aria-invalid");
    const added = Number(result.added_count || 0);
    const duplicates = Number(result.duplicate_count || 0);
    const total = Number(result.count || 0);
    setGeminiSettingsMessage(`Đã thêm ${added} key mới. Tổng ${total} key đã lưu.`, "success");
    $("geminiLastSaveSummary").textContent = duplicates > 0
      ? `${duplicates} key trùng được bỏ qua. Danh sách cũ không bị ghi đè.`
      : "Không có key trùng. Danh sách cũ được giữ nguyên và key mới đã được thêm.";
    await renderReadiness();
  } catch (error) {
    input.setAttribute("aria-invalid", "true");
    setGeminiSettingsMessage(error.message || "Không thể lưu Gemini API key.", "error");
    input.focus();
  } finally {
    button.disabled = false;
  }
}

function wireEvents() {
  $("settingsNavBtn").addEventListener("click", openSettings);
  $("settingsBackBtn").addEventListener("click", closeSettings);
  $("homeNavBtn").addEventListener("click", () => {
    if (state.view === "settings") closeSettings();
  });
  $("openGeminiSettingsBtn").addEventListener("click", openSettings);
  $("saveGeminiKeysBtn").addEventListener("click", () => saveGeminiKeys());
  $("refreshGeminiStatusBtn").addEventListener("click", () => {
    setGeminiSettingsMessage("Đang làm mới trạng thái...");
    renderReadiness()
      .then(() => setGeminiSettingsMessage("Đã làm mới trạng thái Gemini.", "success"))
      .catch((error) => setGeminiSettingsMessage(error.message || "Không thể làm mới trạng thái.", "error"));
  });
  $("chooseVideo").addEventListener("click", (event) => {
    event.stopPropagation();
    $("videoPicker").click();
  });
  $("dropArea").addEventListener("click", (event) => {
    if (event.target?.id !== "chooseVideo") $("videoPicker").click();
  });
  $("dropArea").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      $("videoPicker").click();
    }
  });
  $("videoPicker").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    const localPath = file?.path;
    if (localPath) {
      validateSourcePath(localPath).catch(handleActionError);
    } else if (file) {
      uploadAndValidate(file).catch(handleActionError);
    }
  });
  $("cleanupMode").addEventListener("change", () => {
    if (state.run?.internal_state === "selected") state.run = null;
    updateModeUi();
    renderReadiness().catch(handleActionError);
  });
  $("sourcePath").addEventListener("input", schedulePathValidation);
  $("sourcePath").addEventListener("change", () => {
    clearTimeout(state.validationTimer);
    validateSourcePath($("sourcePath").value).catch(handleActionError);
  });
  $("sourcePath").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      clearTimeout(state.validationTimer);
      validateSourcePath($("sourcePath").value).catch(handleActionError);
    }
  });
  $("dropArea").addEventListener("dragover", (event) => {
    event.preventDefault();
    $("dropArea").classList.add("dragging");
  });
  $("dropArea").addEventListener("dragleave", () => $("dropArea").classList.remove("dragging"));
  $("dropArea").addEventListener("drop", (event) => {
    event.preventDefault();
    $("dropArea").classList.remove("dragging");
    const file = event.dataTransfer?.files?.[0];
    if (file?.path) validateSourcePath(file.path).catch(handleActionError);
    else if (file) uploadAndValidate(file).catch(handleActionError);
  });
  $("startBtn").addEventListener("click", () => startProcessing());
  $("retryRuntimeBtn").addEventListener("click", () => startProcessing({ retryRuntime: true }));
  $("newVideoBtn").addEventListener("click", () => returnToSetup({ clearSelection: true }));
  $("backToSetupBtn").addEventListener("click", () => returnToSetup({ clearSelection: false }));
  $("latestResultBtn").addEventListener("click", () => {
    if (state.latestValidRun) openRunReadOnly(state.latestValidRun.run_id).catch(handleActionError);
  });
  $("recentRuns").addEventListener("click", (event) => {
    const runId = event.target?.dataset?.openRun;
    if (runId) openRunReadOnly(runId).catch(handleActionError);
  });
  $("openFolderBtn").addEventListener("click", async () => {
    if (!state.run?.run_id) return;
    try {
      await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/open-output-folder`, {
        method: "POST",
        body: "{}",
      });
      setResultMessage("Đã mở thư mục kết quả.");
    } catch (error) {
      setResultMessage(error.message, true);
    }
  });
  $("viewOutputBtn").addEventListener("click", () => {
    if (state.run?.output?.url) window.open(state.run.output.url, "_blank", "noopener");
  });
  $("reloadPreviewBtn").addEventListener("click", () => {
    if ($("previewVideo").src) $("previewVideo").load();
  });
  $("copyPathBtn").addEventListener("click", () => navigator.clipboard?.writeText(state.run?.output?.path || ""));
  $("approveBtn").addEventListener("click", () => approveResult().catch((error) => setResultMessage(error.message, true)));
  $("adjustBtn").addEventListener("click", () => rejectResult().catch((error) => setResultMessage(error.message, true)));
  $("saveCopyBtn").addEventListener("click", () => saveCopy().catch((error) => setResultMessage(error.message, true)));
  $("exportTxtTemplateBtn").addEventListener("click", () => exportCreativeTemplate("txt").catch(handleActionError));
  $("exportJsonTemplateBtn").addEventListener("click", () => exportCreativeTemplate("json").catch(handleActionError));
  $("previewCreativeBtn").addEventListener("click", () => previewCreativeImport().catch(handleActionError));
  $("applyCreativeBtn").addEventListener("click", () => applyCreativeImport("creative").catch(handleActionError));
  $("applyImportedBtn").addEventListener("click", () => applyCreativeImport("imported").catch(handleActionError));
  $("contentTrackSelect").addEventListener("change", () => setActiveTrack().catch(handleActionError));
  $("fallbackPolicy").addEventListener("change", () => {
    if (state.run) setActiveTrack().catch(handleActionError);
  });
  $("undoImportBtn").addEventListener("click", async () => {
    try {
      const run = await ensureAdvancedRun();
      await api(`/api/simple/runs/${encodeURIComponent(run.run_id)}/tracks/undo-import`, {
        method: "POST",
        body: "{}",
      });
      await renderTracks();
    } catch (error) {
      handleActionError(error);
    }
  });
  $("creativeCueEditor").addEventListener("click", (event) => {
    const cueId = event.target?.dataset?.saveCue;
    if (cueId) saveCueEdit(cueId).catch(handleActionError);
  });
}

wireEvents();
updateModeUi();
renderReadiness();
renderRun(null, { explicitNew: true });
restoreInitialState().catch((error) => {
  renderRun(null, { explicitNew: true });
  setMessage(error.message, true);
});
