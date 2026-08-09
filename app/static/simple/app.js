const state = {
  view: "setup",
  run: null,
  latestValidRun: null,
  sourcePath: "",
  validatedSource: null,
  validationTimer: null,
  pollTimer: null,
  starting: false,
  startApiCalls: 0,
};

const FLOW_VIEWS = ["setup", "processing", "completed", "error"];
const VIEW_IDS = {
  setup: "setupView",
  processing: "processingView",
  completed: "completedView",
  error: "errorView",
};

const WORKFLOW_STEPS = [
  { id: "prepare", label: "Chuẩn bị âm thanh", stages: ["checking_video"] },
  { id: "recognize", label: "Nhận dạng lời nói", stages: ["analysing_dialogue"] },
  { id: "subtitles", label: "Tạo phụ đề", stages: ["preparing_english_subtitles", "cleaning_dialogue_subtitles"] },
  { id: "render", label: "Xuất video", stages: ["rendering_video"] },
  { id: "verify", label: "Kiểm tra kết quả", stages: ["verifying_result"] },
];

const STAGE_LABELS = {
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
  document.body.dataset.flowState = next;
  window.scrollTo({ top: 0, behavior: "auto" });
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
  const ready = Boolean(state.validatedSource && state.sourcePath && !state.starting);
  $("startBtn").disabled = !ready;
  $("startReason").textContent = state.starting
    ? "Đang bắt đầu lượt xử lý..."
    : ready
      ? "Video đã sẵn sàng. Một lần bấm sẽ tạo phụ đề và xuất video."
      : "Hãy chọn một video để tiếp tục.";
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

function renderProcessing(run) {
  const progress = run?.progress || {};
  const currentStage = progress.current_stage || "checking_video";
  const completedStages = new Set(progress.completed_stages || []);
  const failed = isFailedRun(run);
  $("processingFilename").textContent = run?.source?.filename || state.validatedSource?.filename || "Video đã chọn";
  $("processingCurrent").textContent = failed
    ? "Quá trình xử lý đã dừng."
    : progress.status_label || STAGE_LABELS[currentStage] || "Đang xử lý";
  $("processingDescription").textContent = failed
    ? "Ứng dụng đã dừng an toàn và không công bố kết quả chưa hợp lệ."
    : "Trạng thái được cập nhật từ tiến trình xử lý thực trên máy.";
  $("processingTechnicalOutput").textContent = JSON.stringify(run || {}, null, 2);
  $("processingStages").innerHTML = WORKFLOW_STEPS.map((step) => {
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
  };
  return messages[run?.failure_category]
    || run?.result_validation?.message
    || "Quá trình xử lý chưa thể hoàn tất. Video nguồn và thiết lập của bạn vẫn được giữ nguyên.";
}

function renderError(run, fallbackMessage = "") {
  clearPreview();
  $("errorMessage").textContent = friendlyFailureMessage(run, fallbackMessage);
  $("technicalOutput").textContent = JSON.stringify(run || { message: fallbackMessage }, null, 2);
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
  setFlowView(view);
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
    const [payload, capabilities] = await Promise.all([
      api("/api/health"),
      api("/api/simple/capabilities"),
    ]);
    const ocr = payload.ocr_runtime || {};
    const gemini = capabilities.gemini_translation || {};
    const ready = payload.status === "ok";
    const geminiSource = String(gemini.credential_source || "missing").replace(/_/g, " ");
    const geminiModel = String(gemini.model || "");
    $("readinessPill").textContent = ready ? "Sẵn sàng" : "Cần kiểm tra";
    $("readinessPill").classList.toggle("pass", ready);
    $("readinessSummary").innerHTML = [
      readinessCard("Ứng dụng", ready ? "Sẵn sàng" : "Chưa sẵn sàng", ready),
      readinessCard("FFmpeg", ready ? "Có thể sử dụng" : "Cần kiểm tra", ready),
      readinessCard("OCR tùy chọn", ocr.available ? "Sẵn sàng" : "Không khả dụng", Boolean(ocr.available)),
      readinessCard("Gemini free tier", gemini.configured ? "Đã xác minh" : "Chưa cấu hình", Boolean(gemini.configured)),
    ].join("");
    $("geminiStatus").textContent = gemini.configured
      ? `Gemini ${geminiModel || "đã chọn"} · ${geminiSource}`
      : "Cần cấu hình Gemini free tier trước khi tạo video.";
    $("readinessDetails").textContent = JSON.stringify({
      status: payload.status,
      ocr_available: Boolean(ocr.available),
      gemini_configured: Boolean(gemini.configured),
      gemini_credential_source: gemini.credential_source || "missing",
      action: ready ? "Chọn video để bắt đầu." : "Khởi động lại ứng dụng bằng launcher chính thức.",
    }, null, 2);
  } catch (error) {
    $("readinessPill").textContent = "Cần kiểm tra";
    $("readinessPill").classList.remove("pass");
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
    output_filename: $("outputName").value || "final_video.mp4",
    output_destination: $("outputDestination").value || "",
    include_ass_sidecar: $("includeAss").checked,
    copy_source_into_workspace: $("copySource").checked,
    subtitle_style: $("subtitleStyle").value,
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

async function startProcessing() {
  if (state.starting || !state.validatedSource) return;
  state.starting = true;
  updateStartAction();
  try {
    const run = await ensureRun();
    const provisional = {
      ...run,
      internal_state: "processing",
      progress: run.progress || {
        current_stage: "checking_video",
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

function wireEvents() {
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
      const location = await api(`/api/simple/runs/${encodeURIComponent(state.run.run_id)}/output-location`);
      setResultMessage(`Thư mục kết quả: ${location.folder}`);
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
renderReadiness();
renderRun(null, { explicitNew: true });
restoreInitialState().catch((error) => {
  renderRun(null, { explicitNew: true });
  setMessage(error.message, true);
});
