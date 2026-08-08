const WINDOW_SIZE = 36;
const FRONTEND_ASSET_VERSION = "cp09c";
const STORAGE_KEY = "tool-auto-sub-operator-nav";
const SAFE_NAV_KEYS = ["projectId", "stageId", "selectedIssueId", "selectedSegmentId", "issueFilter"];
const RECENT_PROJECTS_KEY = "tool-auto-sub-operator-recent-projects";
const FALLBACK_STAGES_ORDER = ["preflight", "delogo", "transcript", "english", "voice", "preview", "complete"];

const state = {
  projectId: "vertical_slice_cp07",
  stageId: "complete",
  mode: "project",
  selectedIssueId: null,
  selectedSegmentId: null,
  issueFilter: "all",
  segmentQuery: "",
  segmentOffset: 0,
  projectList: [],
  projectSearch: "",
  projectSearchDraft: "",
  projectSearchComposing: false,
  projectPickerOpen: false,
  projectPickerActiveIndex: 0,
  projectPickerKeyboardActive: false,
  projectPickerIndex: [],
  projectPickerIndexDirty: true,
  projectSwitching: false,
  pendingProjectId: null,
  selectionRequestToken: 0,
  workspaceState: "loading_project",
  workspaceError: null,
  runtimeBuild: null,
  recentProjectIds: [],
  summary: null,
  intake: defaultIntake(),
  sourcePreflight: null,
  pending: false,
};

let projectPickerListenersBound = false;
let projectSearchApplyTimer = null;

function defaultIntake() {
  return {
    name: "Untitled localization",
    slug: "untitled-localization",
    source_path: "",
    source_language: "zh-CN",
    target_language: "en-US",
    content_mode: "sentence-level narrated localization",
    localization_scope: "dialogue_subtitles_only",
    voice: "Production voice configured",
    elevenlabs_model: "eleven_multilingual_v2",
    source_audio_policy: "replace source speech with generated narration",
    subtitle_style_preset: "CP07A compact plate",
    output_resolution: "1280x720",
    provenance_acknowledged: false,
    notes: "",
  };
}

function loadNav() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    for (const key of SAFE_NAV_KEYS) {
      if (typeof stored[key] === "string") state[key] = stored[key];
    }
    const recent = JSON.parse(localStorage.getItem(RECENT_PROJECTS_KEY) || "[]");
    if (Array.isArray(recent)) state.recentProjectIds = recent.filter((value) => typeof value === "string");
  } catch (_) {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(RECENT_PROJECTS_KEY);
  }
}

function saveNav() {
  const safe = {};
  for (const key of SAFE_NAV_KEYS) safe[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(safe));
  localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(state.recentProjectIds.slice(0, 8)));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Operator action failed (${response.status}). ${detail}`);
  }
  return response.json();
}

async function init() {
  loadNav();
  loadRuntimeBuild();
  await refreshProjects();
  document.getElementById("newProjectBtn").addEventListener("click", () => {
    state.mode = "intake";
    state.stageId = "preflight";
    renderAll();
  });
  await loadSummary();
  bindProjectPicker();
  bindGlobalActions();
}

async function loadSummary(projectId = state.projectId, options = {}) {
  state.workspaceState = "loading_project";
  state.workspaceError = null;
  const summary = await api(`/api/operator/projects/${projectId}/summary`);
  if (options.token && options.token !== state.selectionRequestToken) return null;
  commitProjectSnapshot(projectId, summary, options);
  return summary;
}

function visibleProjectById(projectId) {
  return state.projectList.find((project) => project.project_id === projectId) || null;
}

function preferredVisibleProject() {
  return visibleProjectById("production_golden_path_cp09")
    || state.projectList.find((project) => project.is_production)
    || visibleProjectById(state.projectId)
    || state.projectList[0]
    || null;
}

function recoverHiddenProjectSelection() {
  if (state.mode === "intake") return false;
  const visible = visibleProjectById(state.projectId);
  if (visible) return false;
  const preferred = preferredVisibleProject();
  if (!preferred || preferred.project_id === state.projectId) return false;
  state.projectId = preferred.project_id;
  state.projectSearch = "";
  state.projectSearchDraft = "";
  state.projectPickerOpen = false;
  state.projectPickerKeyboardActive = false;
  state.projectPickerActiveIndex = 0;
  state.projectPickerIndexDirty = true;
  state.summary = null;
  state.workspaceState = "loading_project";
  state.workspaceError = null;
  saveNav();
  return true;
}

function commitProjectSnapshot(projectId, summary, options = {}) {
  const previousProjectId = state.projectId;
  state.projectId = projectId;
  state.summary = summary;
  state.projectSwitching = false;
  state.pendingProjectId = null;
  state.workspaceError = null;
  state.workspaceState = summary?.operator_state?.state === "operator_snapshot_missing" ? "operator_snapshot_missing" : "operator_ready";
  state.projectPickerIndexDirty = true;
  const currentStage = resolveStageSelection(state.summary.project.current_stage || state.stageId);
  const fallbackStage = resolveStageSelection(state.stageId);
  state.stageId = currentStage.stage ? currentStage.stageId : fallbackStage.stageId;
  const projectChanged = previousProjectId !== projectId;
  if (projectChanged || !state.summary.issues.some((issue) => issue.issue_id === state.selectedIssueId)) {
    state.selectedIssueId = state.summary.issues.length ? state.summary.issues[0].issue_id : null;
    state.selectedSegmentId = null;
    state.segmentOffset = 0;
  }
  document.getElementById("projectTitle").textContent = state.summary.project.title;
  document.getElementById("projectSubtitle").textContent = `${state.summary.source.filename}  -  ${state.summary.source.duration_seconds}s  -  ${state.summary.source.target_locale}`;
  document.getElementById("overallStatus").textContent = state.summary.overall_status;
  document.getElementById("technicalDetails").textContent = isOperatorSnapshotMissing()
    ? `Operator snapshot missing. Active project: ${projectId}. Provider calls on load: 0.`
    : JSON.stringify(state.summary.technical, null, 2);
  if (options.persist !== false) updateRecentProjects(state.projectId);
  renderAll();
}

function bindGlobalActions() {
  document.getElementById("backBtn").addEventListener("click", () => moveStage(-1));
  document.getElementById("nextBtn").addEventListener("click", () => moveStage(1));
  document.getElementById("prevIssue").addEventListener("click", () => moveIssue(-1));
  document.getElementById("nextIssue").addEventListener("click", () => moveIssue(1));
  document.getElementById("markReviewed").addEventListener("click", markSelectedIssueReviewed);
  document.querySelectorAll(".filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.issueFilter = button.dataset.filter;
      saveNav();
      renderIssues();
    });
  });
}

function renderAll() {
  if (state.projectSwitching) {
    renderProjectSwitching();
    return;
  }
  if (state.mode === "intake") {
    renderProjectPicker();
    bindProjectPicker();
    renderStages();
    renderIssues();
    renderIntakePanel();
    renderNavigation();
    saveNav();
    return;
  }
  renderProjectPicker();
  bindProjectPicker();
  if (!assertProjectStateSynchronized()) return;
  renderStages();
  renderIssues();
  renderStagePanel();
  renderNavigation();
  saveNav();
}

function renderProjectPicker() {
  const picker = document.getElementById("projectPicker");
  const toggle = document.getElementById("projectPickerToggle");
  const current = document.getElementById("projectPickerCurrent");
  const meta = document.getElementById("projectPickerMeta");
  const panel = document.getElementById("projectPickerPanel");
  const search = document.getElementById("projectSearch");
  if (!picker || !toggle || !current || !meta || !panel || !search) return;
  buildProjectPickerIndex();
  const selected = state.projectList.find((project) => project.project_id === state.projectId) || null;
  current.textContent = selected ? selected.display_name : "Loading project...";
  meta.textContent = selected ? selected.secondary_text || selected.project_id : "Reading local project list";
  toggle.setAttribute("aria-expanded", state.projectPickerOpen ? "true" : "false");
  picker.dataset.open = state.projectPickerOpen ? "true" : "false";
  panel.hidden = !state.projectPickerOpen;
  if (document.activeElement !== search) search.value = state.projectSearchDraft || state.projectSearch;
  renderProjectPickerList(state.projectSearch ? "top" : "selected");
}

async function loadRuntimeBuild() {
  try {
    const build = await api("/api/operator/runtime-build");
    state.runtimeBuild = build;
    renderRuntimeBuild();
  } catch (_) {
    state.runtimeBuild = { git_commit: "unknown", backend_version: "unknown", frontend_asset_version: FRONTEND_ASSET_VERSION };
    renderRuntimeBuild();
  }
}

function renderRuntimeBuild() {
  const target = document.getElementById("runtimeBuildId");
  if (!target) return;
  const build = state.runtimeBuild || {};
  target.textContent = `Build: ${build.git_commit || "unknown"} | backend ${build.backend_version || "unknown"} | frontend ${build.frontend_asset_version || FRONTEND_ASSET_VERSION}`;
}

function renderProjectPickerList(scrollMode = "none") {
  const list = document.getElementById("projectPickerList");
  const empty = document.getElementById("projectPickerEmpty");
  if (!list || !empty) return;
  const query = normalizeSearchText(state.projectSearch);
  const visibleProjects = filteredProjectList(query);
  if (visibleProjects.length) {
    if (!query && !state.projectPickerKeyboardActive) {
      const selectedIndex = visibleProjects.findIndex((project) => project.project_id === state.projectId);
      state.projectPickerActiveIndex = selectedIndex >= 0 ? selectedIndex : clampIndex(state.projectPickerActiveIndex, visibleProjects.length);
    } else if (state.projectPickerKeyboardActive) {
      state.projectPickerActiveIndex = clampIndex(state.projectPickerActiveIndex, visibleProjects.length);
    }
  } else {
    state.projectPickerActiveIndex = 0;
  }
  empty.hidden = visibleProjects.length > 0;
  list.innerHTML = visibleProjects.map((project, index) => {
    const isSelected = project.project_id === state.projectId;
    const isActive = state.projectPickerKeyboardActive && index === state.projectPickerActiveIndex;
    const secondary = [project.scope || "", project.readiness || project.status || ""].filter(Boolean).join(" - ") || project.secondary_text || project.project_id || "";
    return `<button type="button" class="project-option ${isSelected ? "current" : ""} ${isActive ? "active" : ""}" data-project-id="${escapeHtml(project.project_id)}" role="option" aria-selected="${isSelected ? "true" : "false"}" aria-label="${escapeHtml(project.display_name)}" title="${escapeHtml(project.display_name)}">
      <span class="primary-line"><span class="project-title">${escapeHtml(project.display_name)}</span>${isSelected ? '<span class="badge">Current</span>' : ""}</span>
      <span class="project-id">${escapeHtml(project.project_id)}</span>
      <span class="project-meta">${escapeHtml(secondary)}</span>
    </button>`;
  }).join("");
  updateProjectResultCount(visibleProjects.length);
  if (scrollMode !== "none") syncProjectListScroll(scrollMode);
}

function filteredProjectList(query) {
  buildProjectPickerIndex();
  if (!query) return state.projectPickerIndex.map((entry) => entry.project);
  return state.projectPickerIndex.filter((entry) => entry.searchText.includes(query)).map((entry) => entry.project);
}

function buildProjectPickerIndex() {
  if (!state.projectPickerIndexDirty) return;
  const ordered = sortProjectList(state.projectList);
  state.projectPickerIndex = ordered.map((project) => ({
    project,
    searchText: normalizeSearchText([
      project.search_text,
      project.display_name,
      project.project_id,
      project.source_filename,
      project.status,
      project.scope,
      project.secondary_text,
    ].filter(Boolean).join(" ")),
  }));
  state.projectPickerIndexDirty = false;
}

function normalizeSearchText(value) {
  return String(value || "").trim().toLowerCase();
}

function updateProjectResultCount(count) {
  const countEl = document.getElementById("projectSearchResultCount");
  if (countEl) countEl.textContent = `${count} project${count === 1 ? "" : "s"} found`;
}

function sortProjectList(projects) {
  const recent = state.recentProjectIds.filter((projectId) => projectId !== state.projectId);
  const recentRank = new Map(recent.map((projectId, index) => [projectId, index]));
  return [...projects].sort((a, b) => {
    const aId = a.project_id || "";
    const bId = b.project_id || "";
    const aCurrent = aId === state.projectId ? 0 : 1;
    const bCurrent = bId === state.projectId ? 0 : 1;
    if (aCurrent !== bCurrent) return aCurrent - bCurrent;
    const aRecent = recentRank.has(aId) ? 0 : 1;
    const bRecent = recentRank.has(bId) ? 0 : 1;
    if (aRecent !== bRecent) return aRecent - bRecent;
    const aProduction = a.is_production ? 0 : 1;
    const bProduction = b.is_production ? 0 : 1;
    if (aProduction !== bProduction) return aProduction - bProduction;
    return (a.display_name || aId).localeCompare(b.display_name || bId) || aId.localeCompare(bId);
  });
}

function clampIndex(index, length) {
  if (!length) return 0;
  if (index < 0) return 0;
  if (index >= length) return length - 1;
  return index;
}

function bindProjectPicker() {
  const picker = document.getElementById("projectPicker");
  const toggle = document.getElementById("projectPickerToggle");
  const panel = document.getElementById("projectPickerPanel");
  const search = document.getElementById("projectSearch");
  const list = document.getElementById("projectPickerList");
  if (!picker || !toggle || !panel || !search || !list) return;
  if (toggle.dataset.bound === "true") return;
  toggle.dataset.bound = "true";
  toggle.addEventListener("click", () => toggleProjectPicker());
  toggle.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openProjectPicker();
    }
  });
  const updateProjectSearch = () => {
    state.projectSearchDraft = search.value;
    if (!state.projectSearchComposing) scheduleProjectSearchApply(search.value);
  };
  search.addEventListener("input", updateProjectSearch);
  search.addEventListener("change", updateProjectSearch);
  search.addEventListener("search", updateProjectSearch);
  search.addEventListener("compositionstart", () => {
    state.projectSearchComposing = true;
  });
  search.addEventListener("compositionend", () => {
    state.projectSearchComposing = false;
    state.projectSearchDraft = search.value;
    scheduleProjectSearchApply(search.value, 0);
  });
  search.addEventListener("keydown", (event) => {
    const visible = filteredProjectList(normalizeSearchText(state.projectSearch));
    if (event.key === "Escape") {
      event.preventDefault();
      closeProjectPicker();
      return;
    }
    if (!visible.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      state.projectPickerKeyboardActive = true;
      state.projectPickerActiveIndex = clampIndex(state.projectPickerActiveIndex + 1, visible.length);
      renderProjectPickerList("none");
      focusActiveProjectOption();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      state.projectPickerKeyboardActive = true;
      state.projectPickerActiveIndex = clampIndex(state.projectPickerActiveIndex - 1, visible.length);
      renderProjectPickerList("none");
      focusActiveProjectOption();
    } else if (event.key === "Enter") {
      event.preventDefault();
      const active = visible[state.projectPickerActiveIndex] || visible[0];
      if (active) selectProject(active.project_id);
    }
  });
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-project-id]");
    if (!button) return;
    selectProject(button.dataset.projectId);
  });
  list.addEventListener("keydown", (event) => {
    const visible = filteredProjectList(normalizeSearchText(state.projectSearch));
    if (!visible.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      state.projectPickerKeyboardActive = true;
      state.projectPickerActiveIndex = clampIndex(state.projectPickerActiveIndex + delta, visible.length);
      renderProjectPickerList("none");
      focusActiveProjectOption();
    } else if (event.key === "Enter") {
      event.preventDefault();
      const active = visible[state.projectPickerActiveIndex] || visible[0];
      if (active) selectProject(active.project_id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeProjectPicker();
    }
  });
  if (!projectPickerListenersBound) {
    projectPickerListenersBound = true;
    document.addEventListener("click", (event) => {
      if (!state.projectPickerOpen) return;
      const currentPicker = document.getElementById("projectPicker");
      if (currentPicker && !currentPicker.contains(event.target)) closeProjectPicker();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.projectPickerOpen) {
        event.preventDefault();
        closeProjectPicker();
      }
    });
  }
}

function toggleProjectPicker() {
  if (state.projectPickerOpen) closeProjectPicker();
  else openProjectPicker();
}

function openProjectPicker() {
  state.projectPickerOpen = true;
  state.projectSearchDraft = state.projectSearch;
  if (!state.projectSearch) {
    const visible = filteredProjectList("");
    const selectedIndex = visible.findIndex((project) => project.project_id === state.projectId);
    state.projectPickerActiveIndex = selectedIndex >= 0 ? selectedIndex : 0;
    state.projectPickerKeyboardActive = false;
  }
  renderProjectPicker();
  syncProjectListScroll("selected");
  const search = document.getElementById("projectSearch");
  if (search) {
    search.focus();
    search.select();
  }
}

function closeProjectPicker() {
  window.clearTimeout(projectSearchApplyTimer);
  state.projectPickerOpen = false;
  renderProjectPicker();
  const toggle = document.getElementById("projectPickerToggle");
  if (toggle) toggle.focus();
}

async function selectProject(projectId) {
  if (!projectId) return;
  window.clearTimeout(projectSearchApplyTimer);
  state.workspaceError = null;
  if (projectId === state.projectId) {
    state.selectionRequestToken += 1;
    state.projectSwitching = false;
    state.pendingProjectId = null;
    state.projectSearch = "";
    state.projectSearchDraft = "";
    state.projectPickerKeyboardActive = false;
    state.projectPickerIndexDirty = true;
    closeProjectPicker();
    renderAll();
    return;
  }
  const token = state.selectionRequestToken + 1;
  state.selectionRequestToken = token;
  state.projectSwitching = true;
  state.pendingProjectId = projectId;
  state.workspaceState = "loading_project";
  state.projectPickerOpen = false;
  state.mode = "project";
  state.projectSearch = "";
  state.projectSearchDraft = "";
  state.projectPickerActiveIndex = 0;
  state.projectPickerKeyboardActive = false;
  state.projectPickerIndexDirty = true;
  state.stageId = "complete";
  renderAll();
  try {
    await loadSummary(projectId, { token });
  } catch (error) {
    if (token !== state.selectionRequestToken) return;
    state.projectSwitching = false;
    state.pendingProjectId = null;
    state.workspaceState = "unexpected_error";
    state.workspaceError = error;
    renderProjectLoadError(projectId, error);
  } finally {
    const toggle = document.getElementById("projectPickerToggle");
    if (toggle) toggle.focus();
  }
}

function focusActiveProjectOption() {
  const active = document.querySelector('.project-option.active');
  if (active && !isProjectOptionFullyVisible(active)) active.scrollIntoView({ behavior: "auto", block: "nearest", inline: "nearest" });
}

function syncProjectListScroll(mode) {
  if (!state.projectPickerOpen) return;
  const list = document.getElementById("projectPickerList");
  if (!list) return;
  requestAnimationFrame(() => {
    if (mode === "top") {
      list.scrollTop = 0;
      return;
    }
    const target = document.querySelector(".project-option.active") || document.querySelector(".project-option.current");
    if (!target) {
      list.scrollTop = 0;
      return;
    }
    target.scrollIntoView({ behavior: "auto", block: "nearest", inline: "nearest" });
  });
}

function scheduleProjectSearchApply(value, delay = 70) {
  window.clearTimeout(projectSearchApplyTimer);
  projectSearchApplyTimer = window.setTimeout(() => {
    const nextQuery = normalizeSearchText(value);
    const previousQuery = state.projectSearch;
    state.projectSearch = nextQuery;
    if (nextQuery !== previousQuery) state.projectPickerKeyboardActive = false;
    renderProjectPickerList(nextQuery ? "top" : "selected");
  }, delay);
}

function isProjectOptionFullyVisible(option) {
  const list = document.getElementById("projectPickerList");
  if (!list || !option) return true;
  const optionRect = option.getBoundingClientRect();
  const listRect = list.getBoundingClientRect();
  return optionRect.top >= listRect.top && optionRect.bottom <= listRect.bottom;
}

function renderStages() {
  const rail = document.getElementById("stageRail");
  if (state.mode === "intake") {
    rail.innerHTML = `<button class="stage-button active"><span>New Project</span>Project Intake</button>`;
    return;
  }
  const activeStage = resolveStageSelection(state.stageId).stageId;
  rail.innerHTML = state.summary.stages
    .map((stage) => `
      <button class="stage-button ${stage.stage_id === activeStage ? "active" : ""}" data-stage="${stage.stage_id}">
        ${escapeHtml(stage.label)}
        <span>${escapeHtml(stage.status)}  -  ${stage.unresolved_issue_count} unresolved</span>
      </button>
    `)
    .join("");
  rail.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.stageId = button.dataset.stage;
      renderAll();
    });
  });
}

function renderIssues() {
  if (state.mode === "intake") {
    document.getElementById("issueStats").innerHTML = [badge("0 blockers"), badge("0 provider calls")].join("");
    document.getElementById("issueList").innerHTML = `<div class="issue-card"><span>${badge("intake")}</span><span><strong>Create a local project safely.</strong><br><small>No provider work starts from this screen.</small></span><span>Ready</span></div>`;
    return;
  }
  document.querySelectorAll(".filter").forEach((button) => button.classList.toggle("active", button.dataset.filter === state.issueFilter));
  const stats = state.summary.issue_summary;
  document.getElementById("issueStats").innerHTML = [
    badge(`${stats.total} total`),
    badge(`${stats.blockers} blockers`, stats.blockers ? "bad" : ""),
    badge(`${stats.warnings} warnings`, stats.warnings ? "warn" : ""),
    badge(`${stats.needs_review} needs review`),
    badge(`${stats.reviewed} reviewed`),
    badge(`${stats.clean_without_review_requirement} clean`),
  ].join("");
  const issues = filteredIssues();
  const list = document.getElementById("issueList");
  list.innerHTML = issues.map((issue) => `
    <button class="issue-card ${issue.issue_id === state.selectedIssueId ? "selected" : ""}" data-issue="${issue.issue_id}">
      <span>${badge(issue.severity, issue.severity === "blocker" ? "bad" : issue.severity === "warning" ? "warn" : "")}</span>
      <span><strong>${escapeHtml(issue.title)}</strong><br><small>${escapeHtml(issue.category)}  -  ${escapeHtml(issue.stage)} ${issue.timestamp ? " -  " + formatTime(issue.timestamp) : ""}</small></span>
      <span>${issueStatusLabel(issue)}</span>
    </button>
  `).join("");
  list.querySelectorAll(".issue-card").forEach((button) => {
    button.addEventListener("click", () => openIssue(button.dataset.issue));
  });
}

function renderStagePanel() {
  if (state.mode === "intake") {
    renderIntakePanel();
    return;
  }
  const panel = document.getElementById("stagePanel");
  if (isOperatorSnapshotMissing()) {
    renderUnreadyProject(panel);
    return;
  }
  const { stageId, stage } = resolveStageSelection(state.stageId);
  const renderers = {
    preflight: renderPreflight,
    delogo: renderDelogo,
    transcript: renderTranscript,
    english: renderEnglish,
    voice: renderVoice,
    preview: renderPreview,
    human_review: renderComplete,
    final_selection: renderComplete,
    export: renderComplete,
    complete: renderComplete,
  };
  const renderer = renderers[stageId];
  if (!stage || !renderer) {
    panel.innerHTML = `<div class="panel-heading"><div><p class="eyebrow">Current stage</p><h2>${escapeHtml(formatStageLabel(stageId))}</h2></div>${badge(stage ? stage.status : "Unavailable", stage ? "" : "warn")}</div><div class="card wide"><p class="muted">Stage data was recovered to a compatible view. Refresh the summary if this selection should have a dedicated panel.</p></div>`;
    bindStagePanel();
    return;
  }
  state.stageId = stageId;
  panel.innerHTML = `<div class="panel-heading"><div><p class="eyebrow">Current stage</p><h2>${escapeHtml(stage.label)}</h2></div>${badge(stage.status)}</div>${renderer()}`;
  bindStagePanel();
}

function renderIntakePanel() {
  const panel = document.getElementById("stagePanel");
  const pf = state.sourcePreflight;
  const checks = pf ? pf.checks : {};
  const canCreate = Boolean(pf && pf.status === "PASS" && state.intake.provenance_acknowledged && !state.pending);
  panel.innerHTML = `<div class="panel-heading"><div><p class="eyebrow">New Project</p><h2>Production Intake</h2></div>${badge(canCreate ? "Ready" : "Needs preflight", canCreate ? "" : "warn")}</div>
    <div class="intake-grid">
      ${intakeInput("Project name", "name")}
      ${intakeInput("Project slug", "slug")}
      ${intakeInput("Register existing local file", "source_path", "text", "D:\\\\path\\\\to\\\\source.mp4")}
      <label>Browser file picker<input id="intakeUpload" type="file" accept=".mp4,.mov,.mkv,.webm,video/mp4,video/webm" /></label>
      ${intakeInput("Source language", "source_language")}
      ${intakeInput("Target language", "target_language")}
      ${intakeInput("Content mode", "content_mode")}
      ${intakeInput("Localization scope", "localization_scope")}
      ${intakeInput("Voice", "voice")}
      ${intakeInput("ElevenLabs model", "elevenlabs_model")}
      ${intakeInput("Source audio policy", "source_audio_policy")}
      ${intakeInput("Subtitle style preset", "subtitle_style_preset")}
      ${intakeInput("Output resolution", "output_resolution")}
      <label class="wide"><input id="intakeProvenance" type="checkbox" ${state.intake.provenance_acknowledged ? "checked" : ""} /> I acknowledge the source provenance and local usage rights.</label>
      <label class="wide">Optional notes<textarea id="intake_notes">${escapeHtml(state.intake.notes)}</textarea></label>
    </div>
    <div class="intake-actions">
      <button id="validateSourceBtn" class="secondary" ${state.pending ? "disabled" : ""}>Validate source</button>
      <button id="createProjectBtn" class="primary" ${canCreate ? "" : "disabled"} title="${canCreate ? "Create canonical local project" : escapeHtml(createDisabledReason())}">Create Project</button>
      <span class="progress-note">${escapeHtml(createDisabledReason())}</span>
    </div>
    ${renderPreflightChecks(pf)}`;
  bindIntakePanel();
}

function intakeInput(label, key, type = "text", placeholder = "") {
  return `<label>${escapeHtml(label)}<input id="intake_${key}" type="${type}" value="${escapeHtml(state.intake[key])}" placeholder="${escapeHtml(placeholder)}" /></label>`;
}

function renderPreflightChecks(preflight) {
  if (!preflight) return `<div class="card wide"><h3>Preflight</h3><p class="muted">Validate a source before creating the project.</p></div>`;
  const labels = {
    exists: "source file exists",
    regular_file: "regular file",
    format_supported: "format supported",
    ffprobe: "FFprobe PASS",
    audio_stream_present: "audio stream present",
    disk_ok: "disk requirement",
    ffmpeg_ready: "FFmpeg ready",
    asr_ready: "ASR ready",
    gemini_configured: "Gemini configured",
    elevenlabs_configured: "ElevenLabs configured",
    slug_available: "project slug available",
  };
  const items = Object.entries(labels).map(([key, label]) => `<div class="preflight-item ${preflight.checks[key] ? "pass" : "fail"}">${preflight.checks[key] ? "PASS" : "FAIL"}  -  ${escapeHtml(label)}</div>`).join("");
  const media = preflight.media ? kv({ Duration: `${preflight.media.duration_seconds}s`, Resolution: `${preflight.media.video.width}x${preflight.media.video.height}`, FPS: preflight.media.video.avg_frame_rate, "Free disk": `${preflight.disk.free_gib} GiB`, "Estimated disk": `${preflight.disk.estimated_required_gib} GiB` }) : `<p class="muted">${escapeHtml(preflight.error || "Preflight failed.")}</p>`;
  return `<div class="card wide"><h3>Preflight</h3><div class="preflight-list">${items}</div>${media}</div>`;
}

function bindIntakePanel() {
  for (const key of Object.keys(state.intake)) {
    const input = document.getElementById(`intake_${key}`);
    if (!input) continue;
    input.addEventListener("input", async () => {
      state.intake[key] = input.value;
      if (key === "name") {
        const slug = await api(`/api/operator/slug?name=${encodeURIComponent(input.value)}`);
        state.intake.slug = slug.slug;
        const slugInput = document.getElementById("intake_slug");
        if (slugInput) slugInput.value = slug.slug;
      }
      state.sourcePreflight = null;
    });
  }
  document.getElementById("intakeProvenance").addEventListener("change", (event) => {
    state.intake.provenance_acknowledged = event.target.checked;
    renderIntakePanel();
  });
  document.getElementById("intake_notes").addEventListener("input", (event) => {
    state.intake.notes = event.target.value;
  });
  document.getElementById("validateSourceBtn").addEventListener("click", validateSource);
  document.getElementById("createProjectBtn").addEventListener("click", createProject);
  document.getElementById("intakeUpload").addEventListener("change", uploadSource);
}

async function uploadSource(event) {
  const file = event.target.files && event.target.files[0];
  if (!file || state.pending) return;
  state.pending = true;
  renderIntakePanel();
  try {
    const response = await fetch("/api/operator/source/upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "x-filename": file.name },
      body: file,
    });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    state.intake.source_path = result.uploaded_path;
    state.sourcePreflight = result.preflight;
  } finally {
    state.pending = false;
    renderIntakePanel();
  }
}

async function validateSource() {
  if (state.pending) return;
  state.pending = true;
  renderIntakePanel();
  try {
    state.sourcePreflight = await api("/api/operator/source/preflight", {
      method: "POST",
      body: JSON.stringify({ source_path: state.intake.source_path, slug: state.intake.slug }),
    });
  } finally {
    state.pending = false;
    renderIntakePanel();
  }
}

async function createProject() {
  if (state.pending || !state.sourcePreflight || state.sourcePreflight.status !== "PASS") return;
  state.pending = true;
  renderIntakePanel();
  try {
    const created = await api("/api/operator/projects/create", { method: "POST", body: JSON.stringify(state.intake) });
    state.projectId = created.project_id;
    state.stageId = "preflight";
    state.mode = "project";
    await refreshProjects();
    await loadSummary();
  } finally {
    state.pending = false;
  }
}

async function refreshProjects() {
  const projects = await api("/api/operator/projects");
  state.projectList = projects.projects || [];
  state.projectPickerIndexDirty = true;
  recoverHiddenProjectSelection();
  renderProjectPicker();
}

function updateRecentProjects(projectId) {
  if (!projectId) return;
  state.recentProjectIds = [projectId, ...state.recentProjectIds.filter((value) => value !== projectId)].slice(0, 8);
  state.projectPickerIndexDirty = true;
  saveNav();
}

function workspaceProjectId() {
  return state.summary?.project?.project_id || null;
}

function assertProjectStateSynchronized() {
  const workspaceId = workspaceProjectId();
  if (!workspaceId || workspaceId === state.projectId) return true;
  console.error(`Project state mismatch: sidebar=${state.projectId}, workspace=${workspaceId}`);
  renderProjectStateMismatch(workspaceId);
  return false;
}

function renderProjectSwitching() {
  const target = state.projectList.find((project) => project.project_id === state.pendingProjectId);
  const targetName = target ? target.display_name : state.pendingProjectId || "selected project";
  renderProjectPicker();
  document.getElementById("projectTitle").textContent = `Loading ${targetName}`;
  document.getElementById("projectSubtitle").textContent = "Loading the selected project's canonical snapshot. No providers are called.";
  document.getElementById("overallStatus").textContent = "Switching project";
  document.getElementById("technicalDetails").textContent = JSON.stringify({ active_project: state.projectId, pending_project: state.pendingProjectId, provider_calls: 0 }, null, 2);
  document.getElementById("stageRail").innerHTML = `<button class="stage-button active"><span>Loading</span>${escapeHtml(targetName)}</button>`;
  document.getElementById("issueStats").innerHTML = "";
  document.getElementById("issueList").innerHTML = "";
  document.getElementById("stagePanel").innerHTML = `<div class="stage-grid">${card("Project loading", `<p>The workspace is switching to <strong>${escapeHtml(targetName)}</strong>. Existing project content is hidden until the new snapshot is ready.</p>`)}</div>`;
  renderNeutralNavigation("Loading selected project...");
}

function renderProjectStateMismatch(workspaceId) {
  document.getElementById("stageRail").innerHTML = "";
  document.getElementById("issueStats").innerHTML = "";
  document.getElementById("issueList").innerHTML = "";
  document.getElementById("stagePanel").innerHTML = `<div class="stage-grid">${card("Project state needs refresh", `<p>The sidebar and workspace are out of sync. Please reload the operator UI before continuing.</p><p class="muted">Sidebar: ${escapeHtml(state.projectId)}. Workspace: ${escapeHtml(workspaceId)}.</p>`)}</div>`;
}

function renderProjectLoadError(projectId, error) {
  const target = state.projectList.find((project) => project.project_id === projectId);
  const targetName = target ? target.display_name : projectId;
  renderProjectPicker();
  document.getElementById("projectTitle").textContent = "Project selection needs retry";
  document.getElementById("projectSubtitle").textContent = `Could not load ${targetName}. The previous project remains active.`;
  document.getElementById("overallStatus").textContent = "Selection blocked";
  document.getElementById("technicalDetails").textContent = JSON.stringify({ active_project: state.projectId, failed_project: projectId, error: String(error && error.message ? error.message : error) }, null, 2);
  document.getElementById("stageRail").innerHTML = "";
  document.getElementById("issueStats").innerHTML = "";
  document.getElementById("issueList").innerHTML = "";
  document.getElementById("stagePanel").innerHTML = `<div class="stage-grid">${card("Project selection failed", `<p>The selected project snapshot could not be loaded. The workspace did not switch to avoid mixed project state.</p>`)}</div>`;
  renderNeutralNavigation("Retry project selection");
}

function renderNeutralNavigation(reason) {
  const back = document.getElementById("backBtn");
  const next = document.getElementById("nextBtn");
  const note = document.getElementById("nextReason");
  if (back) back.disabled = true;
  if (next) {
    next.disabled = true;
    next.textContent = "Unavailable";
  }
  if (note) note.textContent = reason;
}

function isOperatorSnapshotMissing() {
  return state.summary?.operator_state?.state === "operator_snapshot_missing";
}

function renderUnreadyProject(panel) {
  const operatorState = state.summary.operator_state || {};
  const project = state.summary.project || {};
  const source = state.summary.source || {};
  const stage = operatorState.available_stage || project.current_stage || "preflight";
  const status = operatorState.available_status || state.summary.overall_status || "Not ready";
  const reason = operatorState.reason || "This project exists, but it does not yet have an accepted operator snapshot. Complete the required earlier stage before opening guided review.";
  document.getElementById("stageRail").innerHTML = `<button class="stage-button active"><span>${escapeHtml(formatStageLabel(stage))}</span>${escapeHtml(status)}</button>`;
  document.getElementById("issueStats").innerHTML = [
    badge(`${Number(operatorState.unresolved_issue_count || 0)} unresolved`),
    badge("0 provider calls"),
  ].join("");
  document.getElementById("issueList").innerHTML = "";
  panel.innerHTML = `<div class="panel-heading"><div><p class="eyebrow">Safe project summary</p><h2>Project not ready for operator review</h2></div>${badge("Not ready", "warn")}</div>
    <div class="stage-grid">
      ${card("Project", kv({
        "Project": project.title || state.projectId,
        "Project ID": project.project_id || state.projectId,
        "Current state": operatorState.project_state || "operator snapshot missing",
        "Available stage": formatStageLabel(stage),
        "Available status": status,
      }, true))}
      ${card("Prerequisite", `<p>${escapeHtml(reason)}</p><p class="muted">Complete the required earlier stage before opening guided review.</p>`)}
      ${card("Known source", kv({
        "Source": source.filename || "Unknown",
        "Duration": source.duration_seconds ? `${source.duration_seconds}s` : "Not registered",
        "Resolution": source.resolution || "Not registered",
        "Localization scope": source.localization_scope || "dialogue_subtitles_only",
      }, true))}
      ${card("Safe actions", `<p class="muted">Selection is allowed for inspection, but guided review actions are unavailable until an accepted operator snapshot exists.</p>`)}
    </div>`;
  renderNeutralNavigation("Project is not ready for guided review");
}

function createDisabledReason() {
  if (state.pending) return "Working...";
  if (!state.intake.source_path) return "Choose or register a source video.";
  if (!state.sourcePreflight) return "Validate source before creating.";
  if (state.sourcePreflight.status !== "PASS") return state.sourcePreflight.error || "Required preflight check failed.";
  if (!state.intake.provenance_acknowledged) return "Acknowledge source provenance to continue.";
  return "Ready to create.";
}

function renderPreflight() {
  const source = state.summary.source;
  const pre = state.summary.preflight;
  return `<div class="stage-grid">
    ${card("Project", kv({ "Source": source.filename, "Duration": `${source.duration_seconds}s`, "Resolution": source.resolution, "FPS": source.fps, "Source language": source.language, "Target": source.target_locale }))}
    ${card("Readiness", kv({ "FFmpeg": pre.ffmpeg, "ffprobe": pre.ffprobe, "ASR": pre.asr, "Gemini": pre.gemini, "TTS": pre.tts, "Disk": `${pre.disk.status} (${pre.disk.free_gib} GiB free)` }))}
    ${card("Policy", kv({ "Content mode": source.content_mode, "Localization scope": source.localization_scope || "dialogue_subtitles_only", "Non-dialogue CJK": "Optional/manual; not a normal blocker", "Audio policy": source.audio_replacement_policy, "Overall": pre.overall_readiness }))}
    ${gateCard("preflight")}
  </div>`;
}

function renderDelogo() {
  const d = state.summary.delogo;
  const actions = state.summary.project.completed ? `<button class="secondary" data-stage-action="next-issue">Open next delogo issue</button>` : productionActions("delogo", ["Analyze subtitle regions", "Generate inspection preview", "Review and approve removal plan"]);
  const cleanup = state.summary.cjk_cleanup ? renderCleanupSummary(state.summary.cjk_cleanup) : "";
  return `<div class="stage-grid">
    ${card("Accepted source subtitle removal", kv({ "Method": d.method, "Intervals": d.subtitle_interval_count, "Residual issues": d.residual_issue_count, "Short flashes": d.short_flash_count, "Toggle warnings": d.toggle_warning_count }))}
    ${cleanup}
    ${gateCard("delogo")}
    ${card("Safe controls", `${actions}<p class="muted">No global static mask control is exposed. CP07A is immutable.</p>`)}
  </div>`;
}

function renderTranscript() {
  if (!state.summary.project.completed && state.summary.segments.length === 0) {
    return `<div class="stage-grid">${card("Transcript actions", productionActions("asr", ["Start ASR", "Resume ASR", "Retry failed ASR", "Review transcript", "Approve transcript"]))}${gateCard("transcript")}</div>`;
  }
  return segmentReview("Transcript", (segment) => `
    <div><strong>${escapeHtml(segment.source_text)}</strong></div>
    <small>${segment.enabled ? "Enabled" : "Disabled"}  -  ${escapeHtml(segment.status)}</small>
  `);
}

function renderEnglish() {
  if (!state.summary.project.completed && state.summary.segments.length === 0) {
    return `<div class="stage-grid">${card("English content actions", productionActions("english", ["Generate English content", "Resume generation", "Retry failed segments", "Review edits", "Approve English content"]))}${gateCard("english")}</div>`;
  }
  return segmentReview("English content", (segment) => `
    <div><strong>${escapeHtml(segment.spoken_text)}</strong></div>
    <small>Subtitle: ${escapeHtml(segment.subtitle_text)}</small>
  `);
}

function renderVoice() {
  const v = state.summary.voice_timing;
  return `<div class="stage-grid">
    ${card("Voice & timing", kv({ "Voice": v.voice, "Model": v.model, "TTS groups": v.tts_group_count, "Spoken units": v.spoken_unit_count, "Bindings": v.active_binding_status, "Duration": v.generated_duration, "Fit state": v.timing_fit_state, "Missing/failed": v.missing_or_failed_groups, "Provider": v.quota_provider_summary }))}
    ${gateCard("voice")}
    ${card("Safe controls", state.summary.project.completed ? `<button class="secondary" disabled title="Explicit regeneration is intentionally not available in CP08">Regenerate explicitly</button>` : productionActions("tts", ["Calculate TTS requirement", "Show available provider quota", "Generate TTS", "Resume TTS", "Retry failed groups", "Review voice and timing", "Approve voice and timing"]) + `<p class="muted">No audio is regenerated on page load or navigation.</p>`)}
  </div>`;
}

function renderPreview() {
  const p = state.summary.preview;
  if (!state.summary.project.completed) {
    return `<div class="stage-grid">${card("Preview & QA actions", productionActions("render", ["Render preview", "Resume render", "Run QA", "Open preview", "Approve preview"]))}${gateCard("preview")}</div>`;
  }
  return `<div class="stage-grid">
    <div class="card wide">
      <video id="previewPlayer" class="video" controls preload="metadata" src="${p.url}"></video>
    </div>
    ${card("Artifact", kv({ "File": p.filename, "Duration": `${p.duration_seconds}s`, "Resolution": p.resolution, "SHA-256": p.sha256 }))}
    ${card("QA", kv({ "Audio": p.audio_qa.status, "Subtitle": p.subtitle_qa.status, "Visual": p.visual_qa.status, "Targeted visual": p.targeted_visual_qa.status, "Human review": p.human_review_state }))}
    ${nonDialogueTextHtml()}
    ${card("Checklist", p.checklist.map((item) => `<div class="check">${badge(item.state === "PASS" ? "PASS" : "Blocked", item.state === "PASS" ? "" : "bad")} ${escapeHtml(item.label)}</div>`).join(""))}
    ${gateCard("preview")}
  </div>`;
}

function nonDialogueTextHtml() {
  const actions = [
    "Review event",
    "Change classification",
    "Preserve original",
    "Trim segment",
    "Mark as provenance",
    "Approve event",
    "Seek to timestamp",
    "Preview before/after",
    "Next unresolved event",
  ].map((label) => `<button class="secondary small" data-stage-action="non-dialogue-${label.toLowerCase().replaceAll(" ", "-")}">${label}</button>`).join("");
  return card("Optional manual text review", `
    ${kv({
      "Event thumbnail": "Available in review package",
      "Timestamp": "Per event",
      "OCR text": "Safe operator view",
      "Classification": "title, document, UI prompt, CTA, provenance, unknown",
      "Proposed English": "Editable only in the manual review utility",
      "Rendering mode": "Optional manual, preserve, or approved replacement",
      "Scope": "Dialogue subtitles only in the normal production path",
      "Confidence": "OCR + review state",
      "Provenance warning": "Preserve by default",
      "Review state": "Per event",
      "Verdicts": "dialogue clean; content localized only in optional review; provenance preserved",
    })}
    <p class="muted">Optional utility only. It is disabled from the normal production path and never blocks dialogue-subtitle approval.</p>
    <div class="issue-actions cleanup-actions">${actions}</div>
  `);
}

function renderComplete() {
  if (state.summary.golden_path) return renderGoldenPathDashboard();
  const a = state.summary.artifact;
  return `<div class="stage-grid complete-grid">
    ${card("Production preview accepted", kv({ "Artifact": a.filename, "Duration": `${a.duration_seconds}s`, "Resolution": a.resolution, "SHA-256": a.sha256, "Completed checkpoint": state.summary.technical.checkpoint, "No-upload status": "No upload performed", "Next checkpoint": "CP09 available only after explicit approval" }, true), "metadata-card")}
    ${card("Provider usage", kv({ "Gemini calls on UI load": state.summary.provider_summary.gemini_calls_on_ui_load, "ElevenLabs calls on UI load": state.summary.provider_summary.elevenlabs_calls_on_ui_load, "Gemini": state.summary.provider_summary.gemini, "ElevenLabs": state.summary.provider_summary.elevenlabs }, true), "provider-card")}
    ${card("Action", `<p class="completion-status">Completed</p><a class="primary link-button" href="${a.url}" target="_blank" rel="noreferrer">View artifact details</a>`, "action-card")}
  </div>`;
}

function renderGoldenPathDashboard() {
  const gp = state.summary.golden_path;
  const dashboard = gp.dashboard || {};
  const localExport = dashboard.local_export || {};
  const artifact = state.summary.artifact || {};
  const reviewUrls = localExport.review_urls || {};
  const releaseAccepted = localExport.human_acceptance_state === "CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_PASS";
  const readyForManualPublication = localExport.closeout_state === "READY_FOR_MANUAL_PUBLICATION";
  const packagedFiles = Array.isArray(localExport.packaged_files) ? localExport.packaged_files : [];
  const fileList = packagedFiles.length
    ? `<ul class="export-file-list">${packagedFiles.map((file) => `<li><a href="${escapeHtml(file.url)}" target="_blank" rel="noreferrer">${escapeHtml(file.name)}</a><span>${escapeHtml(String(file.size_bytes || 0))} bytes</span><code>${escapeHtml((file.sha256 || "").slice(0, 12))}</code></li>`).join("")}</ul>`
    : `<p class="muted">Package file list appears after a completed local export.</p>`;
  const actions = [
    ["run_preflight", "Run Preflight"],
    ["import_cached_artifact", "Start Stage"],
    ["retry_failed_stage", "Retry Failed Stage"],
    ["resume", "Resume"],
    ["simulate_interruption", "Cancel Safely"],
    ["open_evidence", "Open Evidence"],
    ["approve_final", "Approve Final"],
    ["select_final", "Review Candidate"],
    ["create_export_package", "Create local export package"],
    ["reveal_export_folder", "Reveal Export Folder"],
    ["view_manifest", "View Manifest"],
    ["verify_checksums", "Verify Checksums"],
  ].map(([action, label]) => `<button class="secondary small" data-golden-action="${action}">${label}</button>`).join("");
  return `<div class="stage-grid complete-grid">
    ${card("Production Run Dashboard", kv({
      "Project": dashboard.project_name || state.summary.project.title,
      "Source": state.summary.source.filename,
      "Localization scope": dashboard.localization_scope || "dialogue_subtitles_only",
      "Current stage": dashboard.current_stage || state.summary.project.current_stage,
      "Overall progress": dashboard.overall_progress || "0/0",
      "Active job": dashboard.active_job || "None",
      "Blockers": (dashboard.blockers || []).join(", ") || "None",
      "Provider policy": dashboard.provider_call_policy || "disabled",
      "Disk status": state.summary.preflight.disk ? `${state.summary.preflight.disk.disk_free_gib || state.summary.preflight.disk.free_gib || "n/a"} GiB free` : "n/a",
      "Human review": dashboard.human_review_state || "pending",
      "Export readiness": dashboard.export_readiness || "NOT_STARTED",
    }), "metadata-card")}
    ${card("Final Candidate", kv({
      "Artifact": artifact.filename || "Not selected",
      "SHA-256": artifact.sha256 || "Not selected",
      "Duration": artifact.duration_seconds ? `${artifact.duration_seconds}s` : "n/a",
      "Resolution": artifact.resolution || "n/a",
      "Eligibility": dashboard.final_candidate && dashboard.final_candidate.eligible_reason || "CP08G hash required",
    }, true), "metadata-card")}
    ${card("CP09B Local Export", kv({
      "Availability": localExport.available ? "Ready after CP09A acceptance" : "Locked until final approval gates pass",
      "Final candidate": localExport.final_candidate_filename || artifact.filename || "Not selected",
      "Final hash": localExport.final_candidate_hash || artifact.sha256 || "Not selected",
      "Human approval": localExport.human_approval_status || dashboard.human_review_state || "pending",
      "Estimated export size": localExport.estimated_export_size_bytes ? `${localExport.estimated_export_size_bytes} bytes` : "n/a",
      "Available disk": localExport.available_disk_gib !== undefined ? `${localExport.available_disk_gib} GiB` : "n/a",
      "Destination": localExport.export_destination || "Project exports directory",
      "Include SRT": localExport.include_srt_available ? "Available" : "No canonical SRT",
      "Include ASS": localExport.include_ass_available ? "Available" : "No canonical ASS",
      "Create ZIP": localExport.zip_default ? "Enabled" : "Optional, off by default",
      "Package status": localExport.package_status || "not_started",
      "Release ID": localExport.generated_release_id || "Not created",
      "Release path": localExport.final_release_path || "Not created",
      "Checksum validation": localExport.checksum_validation_status || "pending",
      "Byte-identical": localExport.byte_identical ? "PASS" : "pending",
      "Publish/upload": localExport.publish_upload_state || "not_performed",
      "Local release accepted": releaseAccepted ? "YES" : "Pending human review",
      "Manual publication": readyForManualPublication ? "Ready for manual publication" : "Not ready",
    }, true), "metadata-card")}
    ${readyForManualPublication ? card("Release Closeout", kv({
      "State": "Ready for manual publication",
      "Release ID": localExport.generated_release_id || "Not created",
      "Final video": localExport.final_candidate_filename || "final_video.mp4",
      "SHA-256": localExport.final_candidate_hash || artifact.sha256 || "Not selected",
      "Publication": "Not published",
      "Upload": "Not uploaded",
    }, true) + `
      <div class="issue-actions cleanup-actions">
        ${localExport.final_release_path ? `<button class="secondary small" data-golden-action="reveal_export_folder">Open release folder</button>` : ""}
        ${localExport.final_release_path ? `<button class="secondary small" data-copy="${escapeHtml(`${localExport.final_release_path}\\\\final_video.mp4`)}">Copy final video path</button>` : ""}
        ${localExport.manual_publication_handoff_url ? `<a class="secondary small link-button" href="${escapeHtml(localExport.manual_publication_handoff_url)}" target="_blank" rel="noreferrer">View manual-publication handoff</a>` : ""}
      </div>
      <p class="muted">Upload and publication remain manual only and require a future explicit authorization checkpoint for automation.</p>
    `, "metadata-card wide") : ""}
    ${card("Review Access", `
      <p class="muted">Local export review is directly reachable from CP09. Search projects by name or ID, then open the human review package.</p>
      <div class="issue-actions cleanup-actions">
        ${reviewUrls.review ? `<a class="primary small link-button" href="${escapeHtml(reviewUrls.review)}" target="_blank" rel="noreferrer">Open local export review</a>` : ""}
      </div>
    `, "metadata-card")}
    ${card("CP09B Human Review Package", `
      ${reviewUrls.final_video ? `<video class="export-preview-video" controls preload="metadata" src="${escapeHtml(reviewUrls.final_video)}"></video>` : `<p class="muted">Final video preview appears after export completion.</p>`}
      <div class="issue-actions cleanup-actions">
        ${reviewUrls.final_video ? `<a class="secondary small link-button" href="${escapeHtml(reviewUrls.final_video)}" target="_blank" rel="noreferrer">Open final_video.mp4</a>` : ""}
        ${reviewUrls.manifest ? `<a class="secondary small link-button" href="${escapeHtml(reviewUrls.manifest)}" target="_blank" rel="noreferrer">View manifest</a>` : ""}
        ${reviewUrls.checksums ? `<a class="secondary small link-button" href="${escapeHtml(reviewUrls.checksums)}" target="_blank" rel="noreferrer">View checksums</a>` : ""}
        ${reviewUrls.release_notes ? `<a class="secondary small link-button" href="${escapeHtml(reviewUrls.release_notes)}" target="_blank" rel="noreferrer">View release notes</a>` : ""}
      </div>
      ${fileList}
      ${kv({
        "Human state": localExport.human_acceptance_state || "CP09B_HUMAN_REVIEW_REQUIRED",
        "Publication state": "Not published",
        "Upload state": "Not uploaded",
        "Upload action": "Not available",
        "Publish action": "Not available",
      })}
    `, "metadata-card wide")}
    ${card("Operator Actions", `<div class="issue-actions cleanup-actions">${actions}</div><p class="muted">Publishing is NOT_CONFIGURED. No upload occurs on approval, export, restart, or page load.</p>`, "action-card")}
  </div>`;
}

function segmentReview(title, rowRenderer) {
  const all = filteredSegments();
  const windowed = all.slice(state.segmentOffset, state.segmentOffset + WINDOW_SIZE);
  const selected = state.summary.segments.find((segment) => segment.id === state.selectedSegmentId) || windowed[0] || state.summary.segments[0];
  return `<div class="stage-grid">
    <div class="card wide">
      <div class="segment-tools">
        <input id="segmentSearch" placeholder="Search all 442 segments" value="${escapeHtml(state.segmentQuery)}" />
        <button class="secondary" id="segmentPrev">Previous window</button>
        <button class="secondary" id="segmentNext">Next window</button>
        <span class="muted">Showing ${windowed.length} of ${all.length}; full editors rendered: ${windowed.length}</span>
      </div>
      <div id="segmentWindow" class="segment-window" data-testid="segment-list" data-rendered-count="${windowed.length}" data-total-count="${state.summary.segments.length}">
        ${windowed.map((segment) => `<button class="segment-row ${selected && selected.id === segment.id ? "selected" : ""}" data-segment="${segment.id}">
          <span>${segment.id}</span><span>${formatTime(segment.start_time)}-${formatTime(segment.end_time)}</span><span>${rowRenderer(segment)}</span>
        </button>`).join("")}
      </div>
    </div>
    <div class="card wide segment-editor">
      <h3>${escapeHtml(title)} editor</h3>
      <p class="muted">Editing is intentionally explicit. Changing accepted text would invalidate downstream TTS/subtitle/render artifacts and is not performed silently.</p>
      ${selected ? `<label>Source text<textarea readonly>${escapeHtml(selected.source_text)}</textarea></label><label>Spoken English<textarea readonly>${escapeHtml(selected.spoken_text)}</textarea></label>` : ""}
    </div>
    ${gateCard(state.stageId)}
  </div>`;
}

function bindStagePanel() {
  const search = document.getElementById("segmentSearch");
  if (search) {
    search.addEventListener("input", () => {
      state.segmentQuery = search.value;
      state.segmentOffset = 0;
      renderStagePanel();
      saveNav();
    });
  }
  const prev = document.getElementById("segmentPrev");
  const next = document.getElementById("segmentNext");
  if (prev) prev.addEventListener("click", () => { state.segmentOffset = Math.max(0, state.segmentOffset - WINDOW_SIZE); renderStagePanel(); });
  if (next) next.addEventListener("click", () => { state.segmentOffset += WINDOW_SIZE; renderStagePanel(); });
  document.querySelectorAll(".segment-row").forEach((button) => button.addEventListener("click", () => {
    state.selectedSegmentId = button.dataset.segment;
    saveNav();
    renderStagePanel();
  }));
  document.querySelectorAll("[data-stage-action='next-issue']").forEach((button) => button.addEventListener("click", () => moveIssue(1)));
  document.querySelectorAll("[data-cleanup-action]").forEach((button) => button.addEventListener("click", () => runCleanupAction(button.dataset.cleanupAction)));
  document.querySelectorAll("[data-golden-action]").forEach((button) => button.addEventListener("click", () => runGoldenAction(button.dataset.goldenAction)));
  document.querySelectorAll("[data-start-stage]").forEach((button) => button.addEventListener("click", () => startStage(button.dataset.startStage, button)));
  document.querySelectorAll(".copy-value").forEach((button) => button.addEventListener("click", async () => {
    const value = button.getAttribute("data-copy") || "";
    try {
      await navigator.clipboard.writeText(decodeHtml(value));
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy"; }, 1200);
    } catch (_) {
      button.textContent = "Select";
    }
  }));
}

async function runGoldenAction(action) {
  if (!action || state.pending) return;
  if (action === "open_evidence" || action === "reveal_export_folder" || action === "view_manifest" || action === "verify_checksums") return;
  state.pending = true;
  renderStagePanel();
  try {
    const body = action === "select_final"
      ? { action, artifact_path: "data/projects/vertical_slice_cp07/renders/cp08g_dialogue_subtitle_only_final_720p.mp4" }
      : { action };
    await api(`/api/operator/projects/${state.projectId}/golden-path/action`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await loadSummary();
  } finally {
    state.pending = false;
  }
}

function renderCleanupSummary(cleanup) {
  const controls = cleanup.controls || [
    { action: "preview_source_suppressed_visual", label: "Preview source-suppressed visual" },
    { action: "preview_final_composition", label: "Preview final composition" },
    { action: "seek_to_source_event", label: "Seek to source event" },
    { action: "show_source_geometry", label: "Show source geometry" },
    { action: "show_plate_geometry", label: "Show plate geometry" },
    { action: "show_containment_failures", label: "Show containment failures" },
    { action: "seek_next_containment_failure", label: "Seek next containment failure" },
    { action: "manual_plate_geometry_override", label: "Manual plate geometry override" },
    { action: "approve_source_suppression", label: "Approve source suppression" },
    { action: "analyze_source_text_regions", label: "Analyze source text regions" },
    { action: "run_cleanup_pass", label: "Run cleanup pass" },
    { action: "scan_repaired_output", label: "Scan repaired output" },
    { action: "retry_selected_interval", label: "Retry selected interval" },
    { action: "open_next_residual_issue", label: "Open next residual issue" },
    { action: "previous_issue", label: "Previous issue" },
    { action: "seek_to_issue_timestamp", label: "Seek to issue timestamp" },
    { action: "view_before_after", label: "View before/after" },
    { action: "mark_reviewed", label: "Mark reviewed" },
    { action: "approve_cleanup", label: "Approve cleanup" },
    { action: "approve_preservation", label: "Approve preservation" },
  ];
  const controlButtons = controls.map((control) => `<button class="secondary small" data-cleanup-action="${escapeHtml(control.action)}">${escapeHtml(control.label)}</button>`).join("");
  const gate = cleanup.approval_gate || {};
  const issueState = cleanup.issue_summary || {};
  const selected = cleanup.issues && cleanup.issues.find((issue) => issue.issue_id === cleanup.selected_issue_id) || cleanup.issues && cleanup.issues[0];
  return card(
    "Residual CJK cleanup",
    `
      ${card("Source Suppression", kv({
        "Method": "Local blur/fill source suppression",
        "Blur strength": "Bounded by source geometry",
        "Feather amount": "Soft edge blend",
        "Darkening amount": "None by default",
        "Emergency opaque fallback": "Operator-visible only",
        "Preview source-suppressed layer": "Available",
        "Containment QA": "Source bbox + safety margin must be inside suppression bbox",
      }))}
      ${card("English Subtitle Plate", kv({
        "Plate enabled": "Yes",
        "Opacity": "86%",
        "Horizontal padding": "30 px",
        "Vertical padding": "14 px",
        "Minimum width": "220 px",
        "Maximum width": "80% frame",
        "Corner radius": "ASS rectangle fallback",
        "Bottom margin": "Safe lower-frame anchor",
        "Alignment": "Approved English cue anchor",
        "Preview English composition": "Available",
        "Approval order": "Source suppression -> English subtitle layout -> final preview",
      }))}
      ${kv({
        "Detected candidates": cleanup.detected_candidates ?? 0,
        "Automatically cleaned": cleanup.automatically_cleaned ?? 0,
        "Preserved in-scene text": cleanup.preserved_in_scene_text ?? 0,
        "Possible provenance/watermark": cleanup.possible_provenance_watermark ?? 0,
        "Unresolved blockers": cleanup.unresolved_blockers ?? 0,
        "Warnings": cleanup.warnings ?? 0,
        "Reviewed issues": cleanup.reviewed_issues ?? 0,
        "Clean intervals": cleanup.clean_intervals ?? 0,
        "Scan version": cleanup.scan_version ?? "n/a",
        "Repair iteration": cleanup.repair_iteration ?? 0,
      })}
      ${gateCardHtml(gate)}
      <div class="issue-actions cleanup-actions">
        ${controlButtons}
      </div>
      <p class="muted">Machine state: ${escapeHtml(cleanup.machine_verdict || "pending")}  -  human review: ${escapeHtml(cleanup.human_review_state || "pending")}</p>
      <p class="muted">Issue summary: ${escapeHtml(`${issueState.total ?? 0} total  -  ${issueState.needs_review ?? 0} needs review  -  ${issueState.reviewed ?? 0} reviewed`)}</p>
      ${selected ? `<p class="muted">Selected issue: ${escapeHtml(selected.issue_id)}  -  ${escapeHtml(selected.category)}  -  ${formatTime(selected.timestamp || 0)}</p>` : ""}
    `
  );
}

function gateCardHtml(gate) {
  if (!gate) return "";
  return `<div class="kv"><div><span>${escapeHtml(gate.label || "Approval gate")}</span><strong>${escapeHtml(gate.state || "Pending")}</strong></div><div><span>Approved at</span><strong>${escapeHtml(gate.approved_at || "Not approved")}</strong></div><div><span>Unresolved issues</span><strong>${gate.unresolved_issue_count ?? 0}</strong></div><div><span>Action required</span><strong>${escapeHtml(gate.action_required || "Review required.")}</strong></div><div><span>Blocks next</span><strong>${gate.blocks_next ? "Yes" : "No"}</strong></div></div>`;
}

async function runCleanupAction(action) {
  if (!action || state.pending) return;
  state.pending = true;
  renderStagePanel();
  try {
    const payload = { action };
    const selected = state.summary.cjk_cleanup && state.summary.cjk_cleanup.issues && state.summary.cjk_cleanup.issues.find((issue) => issue.issue_id === state.summary.cjk_cleanup.selected_issue_id);
    if (selected) payload.issue_id = selected.issue_id;
    const updated = await api(`/api/operator/projects/${state.projectId}/cleanup/action`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (updated && updated.state) state.summary.cjk_cleanup = updated.state;
  } finally {
    state.pending = false;
    renderAll();
  }
}

function productionActions(stage, labels) {
  const job = (state.summary.jobs || []).find((item) => item.kind === stage);
  const status = job ? job.status : "not_started";
  const buttons = labels.map((label, index) => {
    const disabled = state.pending || index > 0;
    const title = index > 0 ? "Available after backend stage support progresses." : "Queue this stage explicitly without provider execution in CP08C.";
    return `<button class="${index === 0 ? "primary" : "secondary"}" data-start-stage="${stage}" ${disabled ? "disabled" : ""} title="${escapeHtml(title)}">${escapeHtml(label)}</button>`;
  }).join("");
  return `<div class="stage-actions">${buttons}</div><p class="progress-note">Status: ${escapeHtml(status)}  -  completed 0/0  -  provider calls 0  -  cache hits 0  -  retries 0</p>`;
}

async function startStage(stage, button) {
  if (state.pending) return;
  state.pending = true;
  button.disabled = true;
  try {
    await api(`/api/operator/projects/${state.projectId}/stage/start`, {
      method: "POST",
      body: JSON.stringify({ stage }),
    });
    await loadSummary();
  } finally {
    state.pending = false;
  }
}

function renderNavigation() {
  const order = stageOrder();
  const activeStage = resolveStageSelection(state.stageId).stageId;
  const index = order.indexOf(activeStage);
  const next = document.getElementById("nextBtn");
  const back = document.getElementById("backBtn");
  const reason = document.getElementById("nextReason");
  back.disabled = index <= 0;
  const blocked = stageBlocked(activeStage);
  if (state.summary.project.completed && activeStage === "complete") {
    next.disabled = true;
    next.textContent = "Completed";
    reason.textContent = "This project is already complete. Use View artifact details above.";
    return;
  }
  next.disabled = index >= order.length - 1 || blocked;
  next.textContent = index >= order.length - 2 ? "Complete" : "Next";
  reason.textContent = blocked ? "Resolve the visible approval gate before continuing." : "Navigation never approves or starts provider work.";
}

function moveStage(delta) {
  const order = stageOrder();
  const activeStage = resolveStageSelection(state.stageId).stageId;
  const index = order.indexOf(activeStage);
  const target = order[index + delta];
  if (!target) return;
  if (delta > 0 && stageBlocked(activeStage)) return;
  state.stageId = target;
  renderAll();
}

function stageBlocked(stageId) {
  return state.summary.approval_gates.some((gate) => gate.stage === stageId && gate.blocks_next);
}

function stageOrder() {
  const stageIds = Array.isArray(state.summary?.stages) ? state.summary.stages.map((stage) => stage.stage_id).filter(Boolean) : [];
  return stageIds.length ? stageIds : FALLBACK_STAGES_ORDER;
}

function resolveStageSelection(preferredStageId = state.stageId) {
  const stages = Array.isArray(state.summary?.stages) ? state.summary.stages : [];
  const stageById = new Map(stages.map((stage) => [stage.stage_id, stage]));
  const aliases = {
    complete: ["export"],
    export: ["complete"],
  };
  const current = state.summary?.project?.current_stage;
  const candidates = [
    preferredStageId,
    current,
    ...(aliases[preferredStageId] || []),
    ...(aliases[current] || []),
  ].filter((value) => typeof value === "string" && value.length > 0);
  for (const candidate of candidates) {
    if (stageById.has(candidate)) return { stageId: candidate, stage: stageById.get(candidate) };
  }
  for (const candidate of stageOrder()) {
    if (stageById.has(candidate)) return { stageId: candidate, stage: stageById.get(candidate) };
  }
  return { stageId: candidates[0] || FALLBACK_STAGES_ORDER[0], stage: null };
}

function formatStageLabel(stageId) {
  if (!stageId) return "Unknown stage";
  return stageId.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function filteredIssues() {
  const order = { blocker: 0, warning: 1, clean: 3 };
  return state.summary.issues
    .filter((issue) => {
      if (state.issueFilter === "all") return true;
      if (state.issueFilter === "needs-review") return issue.needs_review && !issue.reviewed;
      if (state.issueFilter === "reviewed") return issue.reviewed;
      if (state.issueFilter === "clean") return issue.severity === "clean" && !issue.needs_review;
      return issue.severity === state.issueFilter;
    })
    .sort((a, b) => (order[a.severity] ?? 2) - (order[b.severity] ?? 2) || Number(a.reviewed) - Number(b.reviewed));
}

function filteredSegments() {
  const q = state.segmentQuery.toLowerCase().trim();
  if (!q) return state.summary.segments;
  return state.summary.segments.filter((segment) => [segment.id, segment.source_text, segment.spoken_text, segment.subtitle_text].join(" ").toLowerCase().includes(q));
}

function openIssue(issueId) {
  const issue = state.summary.issues.find((item) => item.issue_id === issueId);
  if (!issue) return;
  state.selectedIssueId = issueId;
  state.stageId = issue.stage;
  if (issue.segment_id) state.selectedSegmentId = issue.segment_id;
  renderAll();
  const video = document.getElementById("previewPlayer");
  if (video && issue.timestamp) video.currentTime = issue.timestamp;
}

function moveIssue(delta) {
  const issues = filteredIssues();
  if (!issues.length) return;
  const current = Math.max(0, issues.findIndex((issue) => issue.issue_id === state.selectedIssueId));
  const next = issues[(current + delta + issues.length) % issues.length];
  openIssue(next.issue_id);
}

async function markSelectedIssueReviewed() {
  if (!state.selectedIssueId) return;
  const button = document.getElementById("markReviewed");
  button.disabled = true;
  try {
    await api(`/api/operator/projects/${state.projectId}/issues/review`, {
      method: "POST",
      body: JSON.stringify({ issue_id: state.selectedIssueId }),
    });
    await loadSummary();
  } finally {
    button.disabled = false;
  }
}

function gateCard(stageId) {
  const gates = state.summary.approval_gates.filter((gate) => gate.stage === stageId);
  return `<div class="card wide"><h3>Approval gates</h3>${gates.map((gate) => `
    <div class="kv"><div><span>${escapeHtml(gate.label)}</span><strong>${escapeHtml(gate.state)}</strong></div>
    <div><span>Approved at</span><strong>${escapeHtml(gate.approved_at || "Not approved")}</strong></div>
    <div><span>Unresolved issues</span><strong>${gate.unresolved_issue_count}</strong></div>
    <div><span>Action required</span><strong>${escapeHtml(gate.action_required)}</strong></div>
    <div><span>Blocks next</span><strong>${gate.blocks_next ? "Yes" : "No"}</strong></div></div>
  `).join("") || "<p class='muted'>No approval gate for this stage.</p>"}</div>`;
}

function card(title, body, className = "") {
  return `<div class="card ${className}"><h3>${escapeHtml(title)}</h3>${body}</div>`;
}

function kv(entries, copyable = false) {
  return `<div class="kv">${Object.entries(entries).map(([key, value]) => {
    const safeKey = escapeHtml(key);
    const safeValue = escapeHtml(String(value));
    const copy = copyable ? `<button class="copy-value" type="button" title="Copy ${safeKey}" data-copy="${safeValue}">Copy</button>` : "";
    return `<div><span>${safeKey}</span><strong title="${safeValue}">${safeValue}</strong>${copy}</div>`;
  }).join("")}</div>`;
}

function badge(text, tone = "") {
  return `<span class="badge ${tone}">${escapeHtml(String(text))}</span>`;
}

function issueStatusLabel(issue) {
  if (issue.needs_review && !issue.reviewed) return "Needs review";
  if (issue.reviewed) return "Reviewed";
  return "Clean";
}

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = String(Math.floor(total / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function decodeHtml(value) {
  const box = document.createElement("textarea");
  box.innerHTML = value;
  return box.value;
}

init().catch((error) => {
  document.getElementById("stagePanel").innerHTML = `<div class="card"><h2>Could not open operator UI</h2><p>${escapeHtml(error.message)}</p><details><summary>Technical Details</summary><pre>${escapeHtml(error.stack || "")}</pre></details></div>`;
});
