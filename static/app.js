import {
  EditorView, basicSetup, EditorState, Compartment, keymap, undo, redo, selectAll, indentWithTab,
  openSearchPanel, StreamLanguage, oneDark, python, javascript, java, cpp, go, php, html, css, json,
  markdown, csharp, shell,
} from "/static/vendor/codemirror.js";

const POLL_MS = 1000;
const POLL_HIDDEN_MS = 4000;
const SAVE_DEBOUNCE_MS = 350;
const LIVE_DEBOUNCE_MS = 1200;
const KEEP_FILE = ".keep";
const NOTE_LINE = /^\[(Installing|HTML|uv|pip|npm)[^\]]*\]$/;
const CLIENT_ID = crypto.randomUUID();
const STORAGE = { theme: "wmc:theme", live: "wmc:live", org: "wmc:org", token: (id) => `wmc:token:${id}` };

const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/* API ------------------------------------------------------------------------ */

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(path, { method = "GET", body, sessionId } = {}) {
  const headers = { "X-Client-Id": CLIENT_ID, Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = sessionId ? localStorage.getItem(STORAGE.token(sessionId)) : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d) => d.msg).join("; ");
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? null : res.json();
}

/* Shared UI helpers -------------------------------------------------------------- */

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3200);
}

function promptDialog({ title, label = "", value = "", message = "", okLabel = "OK", input = true }) {
  const dialog = $("prompt-dialog");
  $("prompt-title").textContent = title;
  $("prompt-label").textContent = label;
  $("prompt-message").textContent = message;
  $("prompt-ok").textContent = okLabel;
  const field = $("prompt-input");
  field.parentElement.hidden = !input;
  field.value = value;
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "ok" ? (input ? field.value.trim() : true) : null),
      { once: true },
    );
    dialog.returnValue = "";
    dialog.showModal();
    if (input) {
      field.focus();
      const dot = value.lastIndexOf(".");
      field.setSelectionRange(0, dot > 0 ? dot : value.length);
    }
  });
}

function infoDialog(title, body) {
  $("info-title").textContent = title;
  $("info-body").replaceChildren(body);
  $("info-dialog").showModal();
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(STORAGE.theme, theme);
  ide.onThemeChange();
  player.onThemeChange();
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

function closeMenus(except) {
  document.querySelectorAll(".menu.open").forEach((menu) => {
    if (menu === except) return;
    menu.classList.remove("open");
    menu.querySelector(".dropdown").hidden = true;
    menu.querySelector("button")?.setAttribute("aria-expanded", "false");
  });
}

function setupMenus() {
  document.querySelectorAll(".menu").forEach((menu) => {
    const trigger = menu.querySelector(":scope > button");
    const dropdown = menu.querySelector(".dropdown");
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = !menu.classList.contains("open");
      closeMenus(menu);
      menu.classList.toggle("open", open);
      dropdown.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
    });
    trigger.addEventListener("mouseenter", () => {
      const anyOpen = document.querySelector(".menubar .menu.open");
      if (anyOpen && anyOpen !== menu && menu.closest(".menubar")) trigger.click();
    });
  });
  document.addEventListener("click", () => closeMenus());
  document.addEventListener("keydown", (event) => event.key === "Escape" && closeMenus());
  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    event.preventDefault();
    closeMenus();
    ide.action(target.dataset.action);
  });
}

/* App state ------------------------------------------------------------------------- */

const app = {
  me: null,
  info: null,
  languages: [],
  sessions: [],
  orgs: [],
};

async function loadOrgs() {
  if (!app.me) {
    app.orgs = [];
  } else {
    try {
      app.orgs = await api("/api/orgs");
    } catch {
      app.orgs = [];
    }
  }
  renderOrgSelect();
}

function renderOrgSelect() {
  const select = $("org-select");
  $("org-field").hidden = !app.orgs.length;
  const saved = localStorage.getItem(STORAGE.org) ?? "";
  select.replaceChildren(
    h("option", { value: "" }, "Personal"),
    ...app.orgs.map((org) => h("option", { value: org.id }, org.name)),
  );
  select.value = app.orgs.some((org) => org.id === saved) ? saved : "";
}

function languageLabel(id) {
  return app.languages.find((l) => l.id === id)?.label ?? id;
}

function runnableLanguage(path) {
  const lower = path.toLowerCase();
  return app.languages.find((l) => l.extensions.some((ext) => lower.endsWith(ext)));
}

function renderUser() {
  const signedIn = Boolean(app.me);
  const inIde = !$("view-ide").hidden || !$("view-player").hidden;
  $("user-menu").hidden = !signedIn;
  $("signin-btn").hidden = signedIn || inIde || !app.info?.google_enabled;
  renderWorkspaceChip(signedIn && !inIde);
  if (!signedIn) return;
  $("user-name").textContent = app.me.name;
  $("user-email").textContent = app.me.email;
  const avatar = app.me.picture
    ? h("img", { class: "avatar", src: app.me.picture, alt: "", referrerpolicy: "no-referrer" })
    : h("span", { class: "avatar" }, (app.me.name || "?").slice(0, 1).toUpperCase());
  $("user-avatar").replaceChildren(avatar);
}

function workspaceStatus(ws) {
  if (ws.state === "expired") return "Trial ended";
  if (ws.state === "trialing") return `Trial · ${ws.trial_days_left} day${ws.trial_days_left === 1 ? "" : "s"} left`;
  if (ws.state === "past_due") return `${ws.plan_name} · payment due`;
  if (ws.state === "canceled") return "Canceled";
  return ws.plan_name;
}

function renderWorkspaceChip(show) {
  const ws = app.info?.workspace;
  $("menu-workspace").hidden = !ws;
  $("menu-switch").hidden = !ws;
  if (ws) $("menu-switch").href = ws.switch_url;
  const chip = $("workspace-chip");
  chip.hidden = !ws || !show;
  if (!ws) return;
  chip.classList.toggle("warn", ["expired", "past_due", "canceled"].includes(ws.state));
  chip.replaceChildren(h("strong", {}, ws.name), h("span", {}, workspaceStatus(ws)));
}

async function loadIdentity() {
  app.forbidden = null;
  try {
    app.me = await api("/api/me");
  } catch (err) {
    if (!(err instanceof ApiError) || ![401, 403].includes(err.status)) throw err;
    app.me = null;
    if (err.status === 403) app.forbidden = err.message;
  }
  renderUser();
  await loadOrgs();
}

/* Landing + portal ---------------------------------------------------------------------- */

function staggerTiles(root) {
  [...root.querySelectorAll(":scope > .tile:not([hidden])")].forEach((tile, i) => tile.style.setProperty("--i", i));
}

function renderHome() {
  const signedIn = Boolean(app.me);
  $("create-tile").hidden = !signedIn;
  $("portal-tile").hidden = !signedIn;
  $("how-tile").hidden = signedIn;

  const actions = $("hero-actions");
  actions.replaceChildren();
  if (signedIn) {
    actions.append(
      h("span", { class: "caption" }, `Signed in as ${app.me.name}`),
      h("a", { class: "btn btn-light", href: "#portal", onclick: (e) => { e.preventDefault(); $("candidate-input").focus(); } }, "Start a new session"),
    );
  } else if (app.forbidden && app.info?.workspace) {
    actions.append(
      h("span", { class: "caption" }, `${app.forbidden}. Ask a workspace admin to add you.`),
      h("a", { class: "btn btn-light", href: app.info.workspace.switch_url }, "Switch workspace"),
      h("a", { class: "btn btn-light", href: "/auth/logout" }, "Use another account"),
    );
  } else if (app.info?.google_enabled) {
    actions.append(
      h("a", { class: "btn btn-light", href: "/auth/google" }, "Interviewer sign in with Google"),
      h("span", { class: "caption" }, "Candidates join with a PIN →"),
    );
  } else {
    actions.append(h("span", { class: "caption" }, "Google sign-in is not configured on this server."));
  }
  staggerTiles($("view-home"));
  if (signedIn) loadSessions();
}

async function loadSessions() {
  const filter = $("portal-search").value.trim();
  const query = filter ? `?candidate_name=${encodeURIComponent(filter)}` : "";
  try {
    app.sessions = await api(`/api/sessions${query}`);
  } catch (err) {
    if (err.status === 401) { app.me = null; renderUser(); renderHome(); return; }
    toast(err.message);
    return;
  }
  renderPortal(filter);
}

function formatDate(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatClock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

async function showRecordings(sessionId, candidateName) {
  let recordings;
  try {
    recordings = await api(`/api/sessions/${sessionId}/recordings`);
  } catch (err) {
    toast(err.message);
    return;
  }
  const body = recordings.length
    ? h("div", { class: "recording-list" },
        recordings.map((r) => {
          const length = r.ended_at ? formatClock(Date.parse(r.ended_at) - Date.parse(r.started_at)) : "in progress";
          return h("div", { class: "recording-row" },
            h("div", { class: "grow" },
              h("strong", {}, formatDate(r.started_at)),
              h("div", { class: "meta" }, `${length} · ${r.event_count} changes · by ${r.created_by}`),
            ),
            h("a", {
              class: "btn btn-sm btn-primary",
              href: `#/s/${sessionId}/play/${r.id}`,
              onclick: () => $("info-dialog").close(),
            }, "▶ Play"),
          );
        }),
      )
    : h("p", { class: "empty" }, "No recordings yet. Press ● Record in the IDE during an interview.");
  infoDialog(`Recordings${candidateName ? ` · ${candidateName}` : ""}`, body);
}

function renderPortal(filter) {
  const sessions = app.sessions;
  const groups = new Map();
  for (const session of sessions) {
    const key = session.candidate_name.toLowerCase();
    if (!groups.has(key)) groups.set(key, { name: session.candidate_name, sessions: [] });
    groups.get(key).sessions.push(session);
  }
  const container = $("candidates");
  if (!groups.size) {
    container.replaceChildren(h("div", { class: "empty" }, filter ? "No candidates match that filter." : "No sessions yet. Generate a PIN to start your first interview."));
    return;
  }
  container.replaceChildren(
    ...[...groups.values()].map((group) =>
      h("article", { class: "candidate" },
        h("h3", {},
          h("span", { class: "candidate-name", title: group.name }, group.name),
          h("span", { class: "caption" }, `${group.sessions.length} session${group.sessions.length > 1 ? "s" : ""}`),
        ),
        ...group.sessions.map((s) =>
          h("div", { class: "session-row" },
            h("div", { class: "session-info" },
              h("div", { class: "row" },
                h("strong", {}, languageLabel(s.language)),
                s.mode === "take_home" ? h("span", { class: "chip mode" }, "Take-home") : null,
              ),
              s.title ? h("div", { class: "title", title: s.title }, s.title) : null,
              h("div", { class: "meta" }, `Created ${formatDate(s.created_at)}`),
            ),
            h("div", { class: "session-actions" },
              s.status === "active" ? h("span", { class: "chip" }, `PIN ${s.pin}`) : h("span", { class: "chip ended" }, "Ended"),
              h("div", { class: "session-buttons" },
                s.status === "active" ? h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: () => endSession(s) }, "End") : null,
                h("button", { class: "btn btn-sm", type: "button", onclick: () => showRecordings(s.id, s.candidate_name) }, "Recordings"),
                h("a", { class: "btn btn-sm btn-primary", href: `#/s/${s.id}` }, "Open"),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

async function endSession(session) {
  const ok = await promptDialog({
    title: `End ${session.candidate_name}'s session?`,
    message: "The PIN stops working. You can still open the session to review files and runs.",
    okLabel: "End session",
    input: false,
  });
  if (!ok) return;
  try {
    await api(`/api/sessions/${session.id}/end`, { method: "POST" });
    toast("Session ended");
    loadSessions();
  } catch (err) {
    toast(err.message);
  }
}

function setupHome() {
  const pinInput = $("pin-input");
  pinInput.addEventListener("input", () => {
    pinInput.value = pinInput.value.replace(/\D/g, "").slice(0, 6);
    $("join-error").textContent = "";
  });
  $("join-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const pin = pinInput.value;
    if (!/^\d{6}$/.test(pin)) {
      $("join-error").textContent = "Enter all 6 digits.";
      return;
    }
    $("join-btn").disabled = true;
    try {
      const joined = await api("/api/join", { method: "POST", body: { pin } });
      localStorage.setItem(STORAGE.token(joined.session.id), joined.token);
      pinInput.value = "";
      location.hash = `#/s/${joined.session.id}`;
    } catch (err) {
      $("join-error").textContent = err.status === 404 ? "That PIN doesn't match an active session." : err.message;
    } finally {
      $("join-btn").disabled = false;
    }
  });

  $("create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const candidate_name = $("candidate-input").value.trim();
    if (!candidate_name) return;
    $("create-btn").disabled = true;
    $("create-error").textContent = "";
    try {
      const org_id = $("org-select").value || null;
      localStorage.setItem(STORAGE.org, org_id ?? "");
      const session = await api("/api/sessions", {
        method: "POST",
        body: { candidate_name, language: $("language-select").value, org_id },
      });
      $("pin-result").hidden = false;
      $("pin-result-name").textContent = session.candidate_name;
      $("pin-result-pin").textContent = session.pin;
      $("open-new-session").href = `#/s/${session.id}`;
      $("candidate-input").value = "";
      loadSessions();
    } catch (err) {
      $("create-error").textContent = err.message;
    } finally {
      $("create-btn").disabled = false;
    }
  });

  $("copy-pin").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("pin-result-pin").textContent);
    toast("PIN copied");
  });

  let searchTimer;
  $("portal-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadSessions, 250);
  });
}

/* IDE -------------------------------------------------------------------------------- */

function languageExtension(path) {
  const ext = path.toLowerCase().split(".").pop();
  switch (ext) {
    case "py": return python();
    case "js": case "mjs": case "cjs": return javascript();
    case "jsx": return javascript({ jsx: true });
    case "ts": case "mts": return javascript({ typescript: true });
    case "tsx": return javascript({ typescript: true, jsx: true });
    case "java": return java();
    case "c": case "h": case "cpp": case "cc": case "cxx": case "hpp": return cpp();
    case "cs": return StreamLanguage.define(csharp);
    case "go": return go();
    case "php": return php();
    case "sh": case "bash": return StreamLanguage.define(shell);
    case "html": case "htm": return html();
    case "css": return css();
    case "json": return json();
    case "md": return markdown();
    default: return [];
  }
}

function lightTheme() {
  return EditorView.theme({
    "&": { backgroundColor: "transparent" },
    ".cm-gutters": { backgroundColor: "transparent", border: "none", color: "#9ca3af" },
    ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "rgba(110, 86, 207, 0.06)" },
    "&.cm-focused .cm-cursor": { borderLeftColor: "#6e56cf" },
  });
}

function commonPrefixSuffix(a, b) {
  let start = 0;
  const max = Math.min(a.length, b.length);
  while (start < max && a.charCodeAt(start) === b.charCodeAt(start)) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a.charCodeAt(endA - 1) === b.charCodeAt(endB - 1)) { endA--; endB--; }
  return { from: start, to: endA, insert: b.slice(start, endB) };
}

const ide = {
  sessionId: null,
  state: null,
  files: new Map(),
  editorStates: new Map(),
  openTabs: [],
  active: null,
  view: null,
  themeCompartment: new Compartment(),
  applyingRemote: false,
  dirty: new Map(),
  cursor: 0,
  pollTimer: null,
  polling: false,
  liveTimer: null,
  running: false,
  rerunQueued: false,
  lastRun: null,
  layout: { explorer: true, preview: true, console: true },
  collapsed: new Set(),
  generation: 0,

  themeExtension() {
    return document.documentElement.dataset.theme === "dark" ? oneDark : lightTheme();
  },

  onThemeChange() {
    if (this.view) this.view.dispatch({ effects: this.themeCompartment.reconfigure(this.themeExtension()) });
  },

  async open(sessionId) {
    this.close();
    const generation = ++this.generation;
    this.sessionId = sessionId;
    $("view-home").hidden = true;
    $("view-ide").hidden = false;
    $("menubar").hidden = false;
    renderUser();
    this.setSync("saving", "Loading…");
    let state;
    try {
      state = await api(`/api/sessions/${sessionId}/state`, { sessionId });
    } catch (err) {
      if (generation !== this.generation) return;
      const message = err.status === 401 ? "Join with your PIN or sign in to open this session." : err.message;
      toast(message);
      location.hash = "#/";
      return;
    }
    if (generation !== this.generation) return;
    this.state = state;
    this.cursor = state.cursor;
    this.files = new Map(state.files.map((f) => [f.path, f.content]));
    this.renderHeader();
    this.renderTree();
    const lang = app.languages.find((l) => l.id === state.session.language);
    const first = (lang && this.files.has(lang.entry) && lang.entry) || this.visiblePaths()[0];
    if (first) this.openFile(first);
    else this.renderEditor();
    $("console").replaceChildren();
    state.runs.forEach((run) => this.appendRun(run));
    this.lastRun = state.runs.at(-1) ?? null;
    this.renderPreview();
    $("live-toggle").checked = localStorage.getItem(STORAGE.live) === "1";
    this.syncLiveLabel();
    this.recording = state.recording;
    this.renderRecording();
    this.setSync("ok", "Synced");
    this.schedulePoll(POLL_MS);
  },

  renderRecording() {
    const isInterviewer = this.state?.role === "interviewer";
    const active = Boolean(this.recording);
    const canRecord = isInterviewer && (this.state.session.status === "active" || active);
    $("record-btn").hidden = !canRecord;
    $("record-btn").textContent = active ? "■ Stop recording" : "● Record";
    $("record-btn").classList.toggle("recording", active);
    $("recordings-btn").hidden = !isInterviewer;
    $("rec-indicator").hidden = !active;
    clearInterval(this.recTimer);
    if (!active) return;
    const started = Date.parse(this.recording.started_at);
    const tick = () => ($("rec-elapsed").textContent = formatClock(Date.now() - started));
    tick();
    this.recTimer = setInterval(tick, 1000);
  },

  async toggleRecording() {
    const button = $("record-btn");
    button.disabled = true;
    try {
      if (this.recording) {
        await api(`/api/sessions/${this.sessionId}/recordings/${this.recording.id}/stop`, { method: "POST" });
        this.recording = null;
        toast("Recording saved");
      } else {
        this.recording = await api(`/api/sessions/${this.sessionId}/recordings`, { method: "POST" });
        toast("Recording started");
      }
    } catch (err) {
      toast(err.message);
    } finally {
      button.disabled = false;
      this.renderRecording();
    }
  },

  close() {
    clearTimeout(this.pollTimer);
    clearTimeout(this.liveTimer);
    for (const pending of this.dirty.values()) clearTimeout(pending);
    this.dirty.clear();
    this.view?.destroy();
    this.view = null;
    this.sessionId = null;
    this.state = null;
    this.files = new Map();
    this.editorStates = new Map();
    this.openTabs = [];
    this.active = null;
    this.lastRun = null;
    this.recording = null;
    clearInterval(this.recTimer);
    clearInterval(this.countdownTimer);
    this.locked = false;
    $("run-btn").disabled = false;
    $("ide-mode").hidden = true;
    $("ide-countdown").hidden = true;
    $("submit-btn").hidden = true;
    $("menubar").hidden = true;
    $("session-chip").hidden = true;
    $("ide-status").hidden = true;
    $("rec-indicator").hidden = true;
    $("record-btn").hidden = true;
    $("recordings-btn").hidden = true;
  },

  renderHeader() {
    const { session, role, pin } = this.state;
    $("ide-title").textContent = session.candidate_name;
    $("ide-language").textContent = languageLabel(session.language);
    $("ide-status").hidden = session.status !== "ended";
    const chip = $("session-chip");
    chip.hidden = false;
    chip.replaceChildren(
      ...[
        h("span", {}, role === "interviewer" ? "Interviewer" : `Candidate · ${session.candidate_name}`),
        pin && session.status === "active" ? h("span", { class: "pin", title: "Session PIN" }, pin) : null,
      ].filter(Boolean),
    );
    document.title = `${session.candidate_name} · ${app.info?.app_name ?? ""}`;
    this.renderTakeHome();
  },

  renderTakeHome() {
    const session = this.state?.session;
    const takeHome = session?.mode === "take_home";
    const active = session?.status === "active";
    $("ide-mode").hidden = !takeHome;
    $("ide-mode").textContent = session?.submitted_at ? "Take-home · submitted" : "Take-home";
    $("ide-mode").className = "chip mode";
    $("submit-btn").hidden = !(takeHome && active && this.state.role === "candidate");
    clearInterval(this.countdownTimer);
    const showCountdown = takeHome && active && session.started_at && session.time_limit_minutes;
    $("ide-countdown").hidden = !showCountdown;
    if (!showCountdown) return;
    const deadline = Date.parse(session.started_at) + session.time_limit_minutes * 60_000;
    const tick = () => {
      const left = deadline - Date.now();
      $("ide-countdown").textContent = `${formatClock(left)} left`;
      $("ide-countdown").classList.toggle("low", left < 5 * 60_000);
      if (left <= 0) {
        clearInterval(this.countdownTimer);
        if (this.state?.role === "candidate") this.submitTakeHome(true);
      }
    };
    tick();
    this.countdownTimer = setInterval(tick, 1000);
  },

  async submitTakeHome(timeUp = false) {
    if (!timeUp) {
      const ok = await promptDialog({
        title: "Submit your assessment?",
        message: "Your files are saved and sent to the hiring team. You can't edit after submitting.",
        okLabel: "Submit",
        input: false,
      });
      if (!ok) return;
    }
    $("submit-btn").disabled = true;
    try {
      await this.flushAll();
      const session = await api(`/api/sessions/${this.sessionId}/submit`, { method: "POST", sessionId: this.sessionId });
      this.state.session = { ...this.state.session, ...session };
      this.finishLocally(timeUp ? "Time is up. Your work was submitted." : "Submitted. Thanks! You can close this tab.");
    } catch (err) {
      if (err.status === 403) this.finishLocally(err.message);
      else toast(err.message);
    } finally {
      $("submit-btn").disabled = false;
    }
  },

  finishLocally(message) {
    if (this.locked) return;
    this.locked = true;
    clearTimeout(this.pollTimer);
    clearTimeout(this.liveTimer);
    clearInterval(this.countdownTimer);
    for (const pending of this.dirty.values()) clearTimeout(pending);
    this.dirty.clear();
    if (this.state) this.state.session.status = "ended";
    $("submit-btn").hidden = true;
    $("ide-countdown").hidden = true;
    $("ide-status").hidden = false;
    $("run-btn").disabled = true;
    this.setSync("offline", "Submitted");
    infoDialog("Assessment submitted", h("p", {}, message));
  },

  visiblePaths() {
    return [...this.files.keys()].filter((p) => !p.endsWith(`/${KEEP_FILE}`) && p !== KEEP_FILE).sort();
  },

  folders() {
    const set = new Set();
    for (const path of this.files.keys()) {
      const parts = path.split("/");
      for (let i = 1; i < parts.length; i++) set.add(parts.slice(0, i).join("/"));
    }
    return set;
  },

  renderTree() {
    const root = { dirs: new Map(), files: [] };
    for (const path of this.files.keys()) {
      const parts = path.split("/");
      let node = root;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!node.dirs.has(parts[i])) node.dirs.set(parts[i], { dirs: new Map(), files: [] });
        node = node.dirs.get(parts[i]);
      }
      if (parts.at(-1) !== KEEP_FILE) node.files.push(path);
    }
    const build = (node, prefix) => {
      const items = [];
      for (const [name, child] of [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b))) {
        const folderPath = prefix ? `${prefix}/${name}` : name;
        const collapsed = this.collapsed.has(folderPath);
        items.push(
          h("li", {},
            h("div", { class: "tree-item", tabindex: "0", onclick: () => this.toggleFolder(folderPath) },
              h("span", { class: "tree-icon" }, collapsed ? "▸" : "▾"),
              h("span", { class: "label" }, name),
              this.treeOps(folderPath),
            ),
            collapsed ? null : h("ul", {}, build(child, folderPath)),
          ),
        );
      }
      for (const path of node.files.sort()) {
        items.push(
          h("li", {},
            h("div", { class: `tree-item${path === this.active ? " active" : ""}`, tabindex: "0", title: path, onclick: () => this.openFile(path) },
              h("span", { class: "tree-icon" }, "◇"),
              h("span", { class: "label" }, path.split("/").pop()),
              this.treeOps(path),
            ),
          ),
        );
      }
      return items;
    };
    const items = build(root, "");
    $("file-tree").replaceChildren(...(items.length ? items : [h("li", { class: "empty" }, "No files yet")]));
  },

  treeOps(path) {
    const stop = (fn) => (event) => { event.stopPropagation(); fn(); };
    return h("span", { class: "ops" },
      h("button", { type: "button", title: "Rename", "aria-label": `Rename ${path}`, onclick: stop(() => this.renamePath(path)) }, "✎"),
      h("button", { type: "button", title: "Delete", "aria-label": `Delete ${path}`, onclick: stop(() => this.deletePath(path)) }, "✕"),
    );
  },

  toggleFolder(path) {
    if (this.collapsed.has(path)) this.collapsed.delete(path);
    else this.collapsed.add(path);
    this.renderTree();
  },

  createEditorState(path) {
    return EditorState.create({
      doc: this.files.get(path) ?? "",
      extensions: [
        basicSetup,
        keymap.of([
          indentWithTab,
          { key: "Mod-Enter", run: () => { this.run(); return true; } },
          { key: "Mod-s", run: () => { this.flush(this.active); return true; }, preventDefault: true },
        ]),
        languageExtension(path),
        this.themeCompartment.of(this.themeExtension()),
        EditorView.updateListener.of((update) => {
          if (update.docChanged && !this.applyingRemote) this.onLocalEdit(path, update.state.doc.toString());
        }),
      ],
    });
  },

  openFile(path) {
    if (!this.files.has(path)) return;
    if (this.view && this.active) this.editorStates.set(this.active, this.view.state);
    if (!this.openTabs.includes(path)) this.openTabs.push(path);
    this.active = path;
    if (!this.editorStates.has(path)) this.editorStates.set(path, this.createEditorState(path));
    this.renderEditor();
    this.renderTree();
    this.updateRunTarget();
    if (this.layout.preview && /\.(html?|css)$/i.test(path)) this.renderPreview();
  },

  renderEditor() {
    const host = $("editor-host");
    this.renderTabs();
    if (!this.active) {
      this.view?.destroy();
      this.view = null;
      host.replaceChildren(h("div", { class: "editor-empty" }, "Open or create a file to start coding."));
      return;
    }
    const state = this.editorStates.get(this.active);
    if (!this.view) {
      host.replaceChildren();
      this.view = new EditorView({ state, parent: host });
    } else {
      this.view.setState(state);
    }
    this.view.dispatch({ effects: this.themeCompartment.reconfigure(this.themeExtension()) });
    this.view.focus();
  },

  renderTabs() {
    $("tabs").replaceChildren(
      ...this.openTabs.map((path) =>
        h("div", { class: `tab${path === this.active ? " active" : ""}`, role: "tab", title: path, onclick: () => this.openFile(path) },
          path.split("/").pop(),
          h("button", { type: "button", "aria-label": `Close ${path}`, onclick: (e) => { e.stopPropagation(); this.closeTab(path); } }, "×"),
        ),
      ),
    );
  },

  closeTab(path) {
    if (!path) return;
    this.flush(path);
    if (this.view && this.active === path) this.editorStates.set(path, this.view.state);
    const index = this.openTabs.indexOf(path);
    this.openTabs = this.openTabs.filter((p) => p !== path);
    if (this.active === path) {
      this.active = this.openTabs[Math.max(0, index - 1)] ?? null;
    }
    this.renderEditor();
    this.renderTree();
    this.updateRunTarget();
  },

  onLocalEdit(path, content) {
    this.files.set(path, content);
    if (this.locked) return;
    clearTimeout(this.dirty.get(path));
    this.dirty.set(path, setTimeout(() => this.save(path), SAVE_DEBOUNCE_MS));
    this.setSync("saving", "Saving…");
    if ($("live-toggle").checked) {
      clearTimeout(this.liveTimer);
      this.liveTimer = setTimeout(() => this.run({ live: true }), LIVE_DEBOUNCE_MS);
    }
  },

  flush(path) {
    if (path && this.dirty.has(path)) {
      clearTimeout(this.dirty.get(path));
      this.save(path);
    }
  },

  async flushAll() {
    await Promise.all([...this.dirty.keys()].map((path) => { clearTimeout(this.dirty.get(path)); return this.save(path); }));
  },

  async save(path) {
    this.dirty.delete(path);
    if (!this.sessionId || !this.files.has(path)) return;
    try {
      await api(`/api/sessions/${this.sessionId}/files`, {
        method: "PUT",
        sessionId: this.sessionId,
        body: { path, content: this.files.get(path) },
      });
      if (!this.dirty.size) this.setSync("ok", "Saved");
    } catch (err) {
      this.handleSyncError(err);
    }
  },

  /* Sync ---------------------------------------------------------------------- */

  schedulePoll(delay) {
    clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(() => this.poll(), delay);
  },

  async poll() {
    if (!this.sessionId || this.polling) return;
    this.polling = true;
    const generation = this.generation;
    try {
      const changes = await api(`/api/sessions/${this.sessionId}/changes?since=${this.cursor}`, { sessionId: this.sessionId });
      if (generation !== this.generation) return;
      for (const event of changes.events) this.applyEvent(event);
      this.cursor = changes.cursor;
      if (!this.dirty.size) this.setSync("ok", "Synced");
    } catch (err) {
      this.handleSyncError(err);
    } finally {
      this.polling = false;
      if (generation === this.generation && this.sessionId) {
        this.schedulePoll(document.hidden ? POLL_HIDDEN_MS : POLL_MS);
      }
    }
  },

  handleSyncError(err) {
    if (err.status === 401 || err.status === 403 || err.status === 404) {
      this.setSync("offline", err.status === 403 ? "Session ended" : "Access lost");
      if (err.status === 403) {
        $("ide-status").hidden = false;
        if (this.state?.session.mode === "take_home") this.finishLocally(err.message);
        else toast("The interviewer ended this session.");
      }
      clearTimeout(this.pollTimer);
      this.sessionId && this.state?.role === "candidate" && err.status !== 403 && (location.hash = "#/");
      return;
    }
    this.setSync("offline", "Reconnecting…");
  },

  applyEvent(event) {
    if (event.kind === "session" && event.data?.status) {
      this.state.session.status = event.data.status;
      this.renderHeader();
      this.renderRecording();
      return;
    }
    if (event.kind === "recording") {
      const started = event.data?.state === "started";
      if (started && this.recording?.id !== event.data.id) {
        this.recording = { id: event.data.id, started_at: event.created_at };
        if (this.state.role === "candidate") toast("The interviewer started recording this session.");
      } else if (!started && this.recording?.id === event.data?.id) {
        this.recording = null;
        if (this.state.role === "candidate") toast("Recording stopped.");
      }
      this.renderRecording();
      return;
    }
    if (event.client_id === CLIENT_ID) return;
    const who = event.actor;
    switch (event.kind) {
      case "file_upsert":
        this.applyRemoteContent(event.path, event.data.content);
        this.setSync("ok", `${who} edited ${event.path.split("/").pop()}`);
        break;
      case "file_delete":
        for (const path of event.data.paths) this.removeLocal(path);
        this.renderTree();
        this.renderEditor();
        break;
      case "file_rename":
        this.applyRename(event.data.mapping);
        break;
      case "run":
        this.appendRun(event.data, who);
        this.lastRun = event.data;
        this.renderPreview();
        break;
    }
  },

  applyRemoteContent(path, content) {
    if (this.dirty.has(path)) return;
    const isNew = !this.files.has(path);
    this.files.set(path, content);
    if (isNew) this.renderTree();
    if (this.active === path && this.view) {
      const current = this.view.state.doc.toString();
      if (current !== content) {
        this.applyingRemote = true;
        try {
          this.view.dispatch({ changes: commonPrefixSuffix(current, content) });
        } finally {
          this.applyingRemote = false;
        }
      }
    } else if (this.editorStates.has(path)) {
      const state = this.editorStates.get(path);
      const current = state.doc.toString();
      if (current !== content) this.editorStates.set(path, state.update({ changes: commonPrefixSuffix(current, content) }).state);
    }
    if ($("live-toggle").checked && this.isWebPreview()) this.renderPreview();
  },

  removeLocal(path) {
    this.files.delete(path);
    this.editorStates.delete(path);
    clearTimeout(this.dirty.get(path));
    this.dirty.delete(path);
    this.openTabs = this.openTabs.filter((p) => p !== path);
    if (this.active === path) this.active = this.openTabs.at(-1) ?? null;
  },

  applyRename(mapping) {
    if (this.view && this.active) this.editorStates.set(this.active, this.view.state);
    for (const [from, to] of Object.entries(mapping)) {
      this.files.set(to, this.files.get(from) ?? "");
      this.files.delete(from);
      this.editorStates.delete(from);
      this.openTabs = this.openTabs.map((p) => (p === from ? to : p));
      if (this.active === from) this.active = to;
    }
    for (const path of this.openTabs) if (!this.editorStates.has(path)) this.editorStates.set(path, this.createEditorState(path));
    this.renderTree();
    this.renderEditor();
    this.updateRunTarget();
  },

  setSync(kind, text) {
    $("sync-dot").className = `dot${kind === "ok" ? "" : ` ${kind}`}`;
    $("sync-text").textContent = text;
  },

  /* File operations -------------------------------------------------------------- */

  suggestedDir() {
    return this.active && this.active.includes("/") ? this.active.slice(0, this.active.lastIndexOf("/") + 1) : "";
  },

  async newFile() {
    const path = await promptDialog({ title: "New file", label: "Path", value: this.suggestedDir(), message: "Use slashes for folders, e.g. src/utils.py", okLabel: "Create" });
    if (!path) return;
    if (this.files.has(path)) { this.openFile(path); return; }
    try {
      const file = await api(`/api/sessions/${this.sessionId}/files`, { method: "PUT", sessionId: this.sessionId, body: { path, content: "" } });
      this.files.set(file.path, file.content);
      this.renderTree();
      this.openFile(file.path);
    } catch (err) {
      toast(err.message);
    }
  },

  async newFolder() {
    const name = await promptDialog({ title: "New folder", label: "Folder path", value: this.suggestedDir(), okLabel: "Create" });
    if (!name) return;
    const path = `${name.replace(/\/+$/, "")}/${KEEP_FILE}`;
    try {
      const file = await api(`/api/sessions/${this.sessionId}/files`, { method: "PUT", sessionId: this.sessionId, body: { path, content: "" } });
      this.files.set(file.path, "");
      this.renderTree();
    } catch (err) {
      toast(err.message);
    }
  },

  async renamePath(path = this.active) {
    if (!path) return toast("Select a file to rename.");
    const target = await promptDialog({ title: "Rename", label: "New path", value: path, okLabel: "Rename" });
    if (!target || target === path) return;
    this.flush(path);
    try {
      const result = await api(`/api/sessions/${this.sessionId}/files/rename`, {
        method: "POST",
        sessionId: this.sessionId,
        body: { old_path: path, new_path: target },
      });
      this.applyRename(result.renamed);
    } catch (err) {
      toast(err.message);
    }
  },

  async deletePath(path = this.active) {
    if (!path) return toast("Select a file to delete.");
    const isFolder = this.folders().has(path);
    const ok = await promptDialog({ title: `Delete ${path}?`, message: isFolder ? "This deletes the folder and everything inside it." : "This cannot be undone.", okLabel: "Delete", input: false });
    if (!ok) return;
    try {
      const result = await api(`/api/sessions/${this.sessionId}/files?path=${encodeURIComponent(path)}`, { method: "DELETE", sessionId: this.sessionId });
      for (const p of result.deleted) this.removeLocal(p);
      this.renderTree();
      this.renderEditor();
      this.updateRunTarget();
    } catch (err) {
      toast(err.message);
    }
  },

  /* Run + preview ------------------------------------------------------------------ */

  isWebPreview() {
    if (!this.state) return false;
    return this.state.session.language === "web" || (this.active && /\.(html?|css)$/i.test(this.active));
  },

  runEntry() {
    if (!this.state) return null;
    if (this.active && runnableLanguage(this.active) && !this.active.endsWith(".css")) {
      if (!(this.state.session.language === "web" && /\.js$/.test(this.active))) return this.active;
    }
    const lang = app.languages.find((l) => l.id === this.state.session.language);
    if (lang && this.files.has(lang.entry)) return lang.entry;
    return this.visiblePaths().find((p) => runnableLanguage(p)) ?? null;
  },

  updateRunTarget() {
    const entry = this.runEntry();
    $("run-target").textContent = entry ? entry.split("/").pop() : "";
  },

  async run({ live = false } = {}) {
    const entry = this.runEntry();
    if (!entry) return toast("Create a file to run.");
    if (/\.(html?|css)$/i.test(entry) || this.state.session.language === "web") {
      this.renderPreview(true);
      if (!live) toast("Preview refreshed");
      return;
    }
    if (this.running) { this.rerunQueued = true; return; }
    this.running = true;
    $("run-btn").disabled = true;
    try {
      await this.flushAll();
      const result = await api(`/api/sessions/${this.sessionId}/run`, { method: "POST", sessionId: this.sessionId, body: { entry_path: entry } });
      this.appendRun(result, "You");
      this.lastRun = result;
      this.renderPreview();
    } catch (err) {
      toast(err.message);
    } finally {
      this.running = false;
      $("run-btn").disabled = false;
      if (this.rerunQueued) { this.rerunQueued = false; this.run({ live: true }); }
    }
  },

  appendRun(run, who) {
    const consoleEl = $("console");
    const notes = [];
    const errLines = [];
    for (const line of (run.stderr || "").split("\n")) {
      if (NOTE_LINE.test(line)) notes.push(line.slice(1, -1));
      else errLines.push(line);
    }
    const stderr = errLines.join("\n").trim();
    consoleEl.append(
      h("div", { class: "entry" },
        h("div", { class: "cmd" }, `$ run ${run.entry_path}`),
        notes.length ? h("div", { class: "note" }, notes.join("\n")) : null,
        run.stdout ? h("div", { class: "out" }, run.stdout) : null,
        stderr ? h("div", { class: "err" }, stderr) : null,
        h("div", { class: "meta" }, `exit ${run.exit_code} · ${run.duration_ms} ms${who ? ` · by ${who}` : ""} · ${new Date(run.created_at).toLocaleTimeString()}`),
      ),
    );
    consoleEl.parentElement.scrollTop = consoleEl.parentElement.scrollHeight;
  },

  logToConsole(level, text) {
    const consoleEl = $("console");
    consoleEl.append(h("div", { class: `entry ${level === "error" ? "err" : "log"}` }, `[preview ${level}] ${text}`));
    consoleEl.parentElement.scrollTop = consoleEl.parentElement.scrollHeight;
  },

  htmlEntry() {
    if (this.active && /\.html?$/i.test(this.active)) return this.active;
    if (this.files.has("index.html")) return "index.html";
    return this.visiblePaths().find((p) => /\.html?$/i.test(p)) ?? null;
  },

  buildPreviewDocument(entry) {
    const base = entry.includes("/") ? entry.slice(0, entry.lastIndexOf("/") + 1) : "";
    const resolve = (ref) => {
      if (/^(https?:)?\/\//.test(ref) || ref.startsWith("data:")) return null;
      const parts = (base + ref.replace(/^\.\//, "")).split("/");
      const out = [];
      for (const part of parts) part === ".." ? out.pop() : part !== "." && out.push(part);
      const path = out.join("/");
      return this.files.has(path) ? path : null;
    };
    const escapeClose = (text, tag) => text.replace(new RegExp(`</${tag}`, "gi"), `<\\/${tag}`);
    let doc = this.files.get(entry) ?? "";
    doc = doc.replace(/<link\b[^>]*href=["']([^"']+)["'][^>]*>/gi, (tag, href) => {
      const path = /stylesheet/i.test(tag) ? resolve(href) : null;
      return path ? `<style data-src="${path}">\n${escapeClose(this.files.get(path), "style")}\n</style>` : tag;
    });
    doc = doc.replace(/<script\b([^>]*)\bsrc=["']([^"']+)["']([^>]*)><\/script>/gi, (tag, pre, src, post) => {
      const path = resolve(src);
      return path ? `<script${pre}${post} data-src="${path}">\n${escapeClose(this.files.get(path), "script")}\n</script>` : tag;
    });
    const bridge = `<script>(() => {
      const send = (level, args) => parent.postMessage({ wmcPreview: true, level, text: args.map((a) => { try { return typeof a === "string" ? a : JSON.stringify(a); } catch { return String(a); } }).join(" ") }, "*");
      for (const level of ["log", "info", "warn", "error"]) { const orig = console[level]; console[level] = (...args) => { send(level, args); orig.apply(console, args); }; }
      addEventListener("error", (e) => send("error", [e.message]));
    })();<\/script>`;
    return /<head[^>]*>/i.test(doc) ? doc.replace(/<head([^>]*)>/i, `<head$1>${bridge}`) : bridge + doc;
  },

  renderPreview(force = false) {
    const body = $("preview-body");
    const entry = this.htmlEntry();
    if (entry && (this.isWebPreview() || force || !this.lastRun)) {
      $("preview-title").textContent = `Preview · ${entry}`;
      const frame = h("iframe", { class: "preview-frame", sandbox: "allow-scripts allow-modals", title: "Preview", referrerpolicy: "no-referrer" });
      frame.srcdoc = this.buildPreviewDocument(entry);
      body.replaceChildren(frame);
      this.previewFrame = frame;
      return;
    }
    $("preview-title").textContent = "Output";
    if (!this.lastRun) {
      body.replaceChildren(h("div", { class: "editor-empty" }, "Run your code to see output here."));
      return;
    }
    const run = this.lastRun;
    const stderr = (run.stderr || "").split("\n").filter((line) => !NOTE_LINE.test(line)).join("\n").trim();
    $("preview-title").textContent = `Output · ${run.entry_path}`;
    body.replaceChildren(
      ...[
        run.stdout || run.exit_code === 0 ? h("pre", { class: "output" }, run.stdout || "(no output)") : null,
        run.exit_code !== 0 ? h("pre", { class: "output", style: "color: var(--danger)" }, `${stderr}\n\nexit ${run.exit_code}`.trim()) : null,
      ].filter(Boolean),
    );
  },

  syncLiveLabel() {
    $("menu-live-state").textContent = $("live-toggle").checked ? "On" : "Off";
  },

  applyLayout() {
    const ideEl = $("view-ide");
    ideEl.classList.toggle("no-explorer", !this.layout.explorer);
    const side = this.layout.preview || this.layout.console;
    ideEl.classList.toggle("no-side", !side);
    $("pane-side").classList.toggle("only-preview", this.layout.preview && !this.layout.console);
    $("pane-side").classList.toggle("only-console", !this.layout.preview && this.layout.console);
    $("preview-pane").hidden = !this.layout.preview;
    $("console-pane").hidden = !this.layout.console;
  },

  showShortcuts() {
    const rows = [
      ["Run", "Mod+Enter"], ["Save now", "Mod+S"], ["New file", "Alt+N"], ["Close tab", "Alt+W"],
      ["Find", "Mod+F"], ["Undo / Redo", "Mod+Z / Mod+Shift+Z"], ["Indent", "Tab"],
    ];
    infoDialog("Keyboard shortcuts", h("div", { class: "shortcuts" }, rows.flatMap(([label, key]) => [h("span", {}, label), h("kbd", {}, key)])));
  },

  action(name) {
    const inIde = Boolean(this.sessionId);
    const editorCmd = (cmd) => { if (this.view) { cmd(this.view); this.view.focus(); } };
    const handlers = {
      "toggle-theme": toggleTheme,
      "new-file": () => inIde && this.newFile(),
      "new-folder": () => inIde && this.newFolder(),
      rename: () => inIde && this.renamePath(),
      delete: () => inIde && this.deletePath(),
      save: () => { this.flushAll(); toast("Saved"); },
      "close-tab": () => this.closeTab(this.active),
      leave: () => (location.hash = "#/"),
      undo: () => editorCmd(undo),
      redo: () => editorCmd(redo),
      find: () => editorCmd(openSearchPanel),
      "select-all": () => editorCmd(selectAll),
      "toggle-explorer": () => { this.layout.explorer = !this.layout.explorer; this.applyLayout(); },
      "toggle-preview": () => { this.layout.preview = !this.layout.preview; this.applyLayout(); },
      "toggle-console": () => { this.layout.console = !this.layout.console; this.applyLayout(); },
      run: () => inIde && this.run(),
      "toggle-live": () => { $("live-toggle").checked = !$("live-toggle").checked; $("live-toggle").dispatchEvent(new Event("change")); },
      "clear-console": () => $("console").replaceChildren(),
      "refresh-preview": () => this.renderPreview(true),
      shortcuts: () => this.showShortcuts(),
      about: () => infoDialog(app.info?.app_name ?? "About", h("p", {}, `${app.info?.tagline ?? ""}. Built by ${app.info?.author ?? ""}.`)),
    };
    handlers[name]?.();
  },
};

function setupIde() {
  $("run-btn").addEventListener("click", () => ide.run());
  $("record-btn").addEventListener("click", () => ide.toggleRecording());
  $("submit-btn").addEventListener("click", () => ide.submitTakeHome());
  $("recordings-btn").addEventListener("click", () => showRecordings(ide.sessionId, ide.state?.session.candidate_name));
  $("live-toggle").addEventListener("change", () => {
    localStorage.setItem(STORAGE.live, $("live-toggle").checked ? "1" : "0");
    ide.syncLiveLabel();
    toast(`Live refresh ${$("live-toggle").checked ? "on" : "off"}`);
  });
  window.addEventListener("message", (event) => {
    if (!event.data?.wmcPreview || event.source !== ide.previewFrame?.contentWindow) return;
    ide.logToConsole(event.data.level, event.data.text);
  });
  document.addEventListener("keydown", (event) => {
    if (!ide.sessionId || event.defaultPrevented) return;
    if (event.altKey && event.code === "KeyN") { event.preventDefault(); ide.newFile(); }
    if (event.altKey && event.code === "KeyW") { event.preventDefault(); ide.closeTab(ide.active); }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); ide.run(); }
  });
  window.addEventListener("beforeunload", () => ide.flushAll());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && ide.sessionId) ide.schedulePoll(0);
  });
}

/* Recording playback ----------------------------------------------------------------------- */

const PREVIEW_THROTTLE_MS = 500;

const player = {
  data: null,
  view: null,
  viewPath: undefined,
  themeCompartment: new Compartment(),
  frames: [],
  duration: 1,
  position: 0,
  speed: 1,
  playing: false,
  raf: null,
  lastTick: 0,
  applied: -1,
  files: new Map(),
  runs: [],
  active: null,
  actor: "",
  generation: 0,
  previewAt: 0,

  onThemeChange() {
    if (this.view) this.view.dispatch({ effects: this.themeCompartment.reconfigure(ide.themeExtension()) });
  },

  async open(sessionId, recordingId) {
    this.close();
    const generation = ++this.generation;
    $("view-home").hidden = true;
    $("view-ide").hidden = true;
    $("view-player").hidden = false;
    renderUser();
    let data;
    try {
      data = await api(`/api/sessions/${sessionId}/recordings/${recordingId}`);
    } catch (err) {
      if (generation !== this.generation) return;
      toast(err.status === 401 ? "Sign in as the interviewer to watch recordings." : err.message);
      location.hash = "#/";
      return;
    }
    if (generation !== this.generation) return;
    this.data = data;
    const start = Date.parse(data.started_at);
    this.frames = data.events.map((event) => ({ ...event, t: Math.max(0, Date.parse(event.created_at) - start) }));
    const end = data.ended_at ? Date.parse(data.ended_at) - start : Date.now() - start;
    this.duration = Math.max(1, end, this.frames.at(-1)?.t ?? 0);
    $("player-title").textContent = data.session.candidate_name;
    $("player-meta").textContent = `${languageLabel(data.session.language)} · ${formatDate(data.started_at)}`;
    $("player-back").href = `#/s/${sessionId}`;
    document.title = `Playback · ${data.session.candidate_name} · ${app.info?.app_name ?? ""}`;
    this.setSpeed(this.speed);
    this.seek(0);
  },

  close() {
    this.pause();
    this.view?.destroy();
    this.view = null;
    this.viewPath = undefined;
    this.data = null;
    this.frames = [];
    this.applied = -1;
    this.position = 0;
    $("view-player").hidden = true;
  },

  editorState(doc, path) {
    return EditorState.create({
      doc,
      extensions: [
        basicSetup,
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        path ? languageExtension(path) : [],
        this.themeCompartment.of(ide.themeExtension()),
      ],
    });
  },

  reset() {
    this.files = new Map(this.data.initial_files.map((f) => [f.path, f.content]));
    this.runs = [];
    this.applied = -1;
    this.actor = "";
    const lang = app.languages.find((l) => l.id === this.data.session.language);
    this.active = (lang && this.files.has(lang.entry) && lang.entry) || this.visiblePaths()[0] || null;
  },

  visiblePaths() {
    return [...this.files.keys()].filter((p) => !p.endsWith(`/${KEEP_FILE}`) && p !== KEEP_FILE).sort();
  },

  applyFrame(frame) {
    switch (frame.kind) {
      case "file_upsert":
        this.files.set(frame.path, frame.data.content);
        if (!frame.path.endsWith(KEEP_FILE)) this.active = frame.path;
        this.actor = `${frame.actor} editing ${frame.path}`;
        return "file";
      case "file_delete":
        for (const path of frame.data.paths) this.files.delete(path);
        if (!this.files.has(this.active)) this.active = this.visiblePaths()[0] ?? null;
        this.actor = `${frame.actor} deleted ${frame.path}`;
        return "tree";
      case "file_rename":
        for (const [from, to] of Object.entries(frame.data.mapping)) {
          this.files.set(to, this.files.get(from) ?? "");
          this.files.delete(from);
          if (this.active === from) this.active = to;
        }
        this.actor = `${frame.actor} renamed ${frame.path}`;
        return "tree";
      case "run":
        this.runs.push({ ...frame.data, actor: frame.actor });
        this.actor = `${frame.actor} ran ${frame.data.entry_path}`;
        return "run";
      default:
        return null;
    }
  },

  advanceTo(t) {
    const changes = new Set();
    while (this.applied + 1 < this.frames.length && this.frames[this.applied + 1].t <= t) {
      this.applied += 1;
      const kind = this.applyFrame(this.frames[this.applied]);
      if (kind) changes.add(kind);
    }
    return changes;
  },

  seek(t) {
    const target = Math.max(0, Math.min(this.duration, t));
    if (target < this.position || this.applied < 0) this.reset();
    this.position = target;
    this.advanceTo(target);
    this.renderAll();
  },

  renderAll() {
    this.renderTree();
    this.renderEditor(false);
    this.renderConsole();
    this.renderOutput(true);
    this.renderTime();
  },

  renderTree() {
    const items = this.visiblePaths().map((path) =>
      h("li", {},
        h("div", { class: `tree-item${path === this.active ? " active" : ""}`, title: path, onclick: () => { this.active = path; this.renderTree(); this.renderEditor(false); } },
          h("span", { class: "tree-icon" }, "◇"),
          h("span", { class: "label" }, path),
        ),
      ),
    );
    $("player-tree").replaceChildren(...(items.length ? items : [h("li", { class: "empty" }, "No files")]));
  },

  renderEditor(follow) {
    const path = this.active;
    const content = path ? this.files.get(path) ?? "" : "";
    $("player-file").textContent = path ?? "—";
    $("player-actor").textContent = this.actor;
    if (!this.view) {
      this.view = new EditorView({ state: this.editorState(content, path), parent: $("player-editor") });
      this.viewPath = path;
      return;
    }
    if (this.viewPath !== path) {
      this.view.setState(this.editorState(content, path));
      this.viewPath = path;
      return;
    }
    const current = this.view.state.doc.toString();
    if (current === content) return;
    const change = commonPrefixSuffix(current, content);
    this.view.dispatch({
      changes: change,
      selection: follow ? { anchor: change.from + change.insert.length } : undefined,
      scrollIntoView: follow,
    });
  },

  renderConsole() {
    const consoleEl = $("player-console");
    consoleEl.replaceChildren(...this.runs.map((run) => this.runEntry(run)));
    consoleEl.parentElement.scrollTop = consoleEl.parentElement.scrollHeight;
  },

  runEntry(run) {
    const lines = (run.stderr || "").split("\n");
    const notes = lines.filter((line) => NOTE_LINE.test(line)).map((line) => line.slice(1, -1));
    const stderr = lines.filter((line) => !NOTE_LINE.test(line)).join("\n").trim();
    return h("div", { class: "entry" },
      h("div", { class: "cmd" }, `$ run ${run.entry_path}`),
      notes.length ? h("div", { class: "note" }, notes.join("\n")) : null,
      run.stdout ? h("div", { class: "out" }, run.stdout) : null,
      stderr ? h("div", { class: "err" }, stderr) : null,
      h("div", { class: "meta" }, `exit ${run.exit_code} · ${run.duration_ms} ms · by ${run.actor}`),
    );
  },

  renderOutput(force) {
    const body = $("player-output");
    const htmlEntry = this.files.has("index.html") ? "index.html" : this.visiblePaths().find((p) => /\.html?$/i.test(p));
    if (this.data.session.language === "web" && htmlEntry) {
      const now = performance.now();
      if (!force && now - this.previewAt < PREVIEW_THROTTLE_MS) return;
      this.previewAt = now;
      $("player-output-title").textContent = `Preview · ${htmlEntry}`;
      const frame = h("iframe", { class: "preview-frame", sandbox: "allow-scripts", title: "Preview", referrerpolicy: "no-referrer" });
      frame.srcdoc = ide.buildPreviewDocument.call({ files: this.files }, htmlEntry);
      body.replaceChildren(frame);
      return;
    }
    const run = this.runs.at(-1);
    $("player-output-title").textContent = run ? `Output · ${run.entry_path}` : "Output";
    if (!run) {
      body.replaceChildren(h("div", { class: "editor-empty" }, "No runs yet at this point."));
      return;
    }
    const stderr = (run.stderr || "").split("\n").filter((line) => !NOTE_LINE.test(line)).join("\n").trim();
    body.replaceChildren(
      ...[
        run.stdout || run.exit_code === 0 ? h("pre", { class: "output" }, run.stdout || "(no output)") : null,
        run.exit_code !== 0 ? h("pre", { class: "output", style: "color: var(--danger)" }, `${stderr}\n\nexit ${run.exit_code}`.trim()) : null,
      ].filter(Boolean),
    );
  },

  renderTime() {
    $("player-time").textContent = `${formatClock(this.position)} / ${formatClock(this.duration)}`;
    $("player-seek").value = String(Math.round((this.position / this.duration) * 1000));
  },

  play() {
    if (!this.data) return;
    if (this.position >= this.duration) this.seek(0);
    this.playing = true;
    $("player-play").textContent = "❚❚ Pause";
    this.lastTick = performance.now();
    this.raf = requestAnimationFrame((now) => this.tick(now));
  },

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    $("player-play").textContent = "▶ Play";
  },

  toggle() {
    this.playing ? this.pause() : this.play();
  },

  tick(now) {
    if (!this.playing) return;
    this.position = Math.min(this.duration, this.position + (now - this.lastTick) * this.speed);
    this.lastTick = now;
    const changes = this.advanceTo(this.position);
    if (changes.has("tree") || changes.has("file")) this.renderTree();
    if (changes.size) this.renderEditor(true);
    if (changes.has("run")) this.renderConsole();
    if (changes.has("run") || changes.has("file") || changes.has("tree")) this.renderOutput(changes.has("run"));
    this.renderTime();
    if (this.position >= this.duration) this.pause();
    else this.raf = requestAnimationFrame((next) => this.tick(next));
  },

  setSpeed(speed) {
    this.speed = speed;
    document.querySelectorAll("#player-speed button").forEach((button) => {
      const active = Number(button.dataset.speed) === speed;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  },
};

function setupPlayer() {
  $("player-play").addEventListener("click", () => player.toggle());
  $("player-speed").addEventListener("click", (event) => {
    const button = event.target.closest("[data-speed]");
    if (button) player.setSpeed(Number(button.dataset.speed));
  });
  $("player-seek").addEventListener("input", (event) => {
    player.seek((Number(event.target.value) / 1000) * player.duration);
  });
  document.addEventListener("keydown", (event) => {
    if ($("view-player").hidden || event.target.closest("input, textarea, button, a, .cm-editor")) return;
    if (event.code === "Space") { event.preventDefault(); player.toggle(); }
  });
}

/* Organization settings ------------------------------------------------------------------ */

function fieldEl(label, input, hint) {
  const id = input.id || `f-${Math.random().toString(36).slice(2, 9)}`;
  input.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("span", { class: "help" }, hint) : null);
}

function toggleEl(label, checked, onChange) {
  const input = h("input", { type: "checkbox" });
  input.checked = Boolean(checked);
  if (onChange) input.addEventListener("change", onChange);
  return { el: h("label", { class: "switch" }, input, label), input };
}

function copyField(value, label = "Copy") {
  return h("div", { class: "copy-field" },
    h("code", { title: value }, value),
    h("button", { class: "btn btn-sm", type: "button", onclick: async () => { await navigator.clipboard.writeText(value); toast("Copied"); } }, label),
  );
}

function languageSelect(value) {
  const select = h("select", { class: "input" }, ...app.languages.map((l) => h("option", { value: l.id }, l.label)));
  select.value = value || "python";
  return select;
}

function splitIds(text) {
  return text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
}

const orgView = {
  orgId: null,
  detail: null,
  templates: [],
  editing: null,

  async open(orgId) {
    $("view-home").hidden = true;
    $("view-ide").hidden = true;
    $("view-player").hidden = true;
    $("view-org").hidden = false;
    document.title = `Organization settings · ${app.info?.app_name ?? ""}`;
    renderUser();
    if (!app.me) {
      $("view-org").replaceChildren(
        h("section", { class: "tile span-6 static" }, h("h2", {}, "Sign in required"), h("p", { class: "help" }, "Sign in with Google to manage organizations."),
          app.info?.google_enabled ? h("a", { class: "btn btn-primary", href: "/auth/google" }, "Interviewer sign in") : null),
      );
      return;
    }
    await loadOrgs();
    const saved = localStorage.getItem(STORAGE.org);
    this.orgId = orgId || (app.orgs.some((o) => o.id === saved) ? saved : app.orgs[0]?.id) || null;
    this.editing = null;
    await this.refresh();
  },

  async refresh() {
    this.detail = null;
    this.templates = [];
    if (this.orgId) {
      try {
        [this.detail, this.templates] = await Promise.all([
          api(`/api/orgs/${this.orgId}`),
          api(`/api/orgs/${this.orgId}/assessments`),
        ]);
        localStorage.setItem(STORAGE.org, this.orgId);
      } catch (err) {
        toast(err.message);
        this.orgId = null;
      }
    }
    this.render();
  },

  isAdmin() {
    return this.detail?.role === "admin";
  },

  async call(path, options, success) {
    try {
      const result = await api(path, options);
      if (success) toast(success);
      return result ?? true;
    } catch (err) {
      toast(err.message);
      return null;
    }
  },

  render() {
    const tiles = [this.headerTile()];
    if (this.detail) {
      tiles.push(this.membersTile(), this.apiKeysTile(), this.ashbyTile(), this.modernLoopTile(), this.templatesTile());
    }
    $("view-org").replaceChildren(...tiles.filter(Boolean));
    staggerTiles($("view-org"));
  },

  headerTile() {
    const nameInput = h("input", { class: "input", maxlength: "80", placeholder: "e.g. Acme Engineering", required: true });
    const createForm = h("form", { class: "inline-form", onsubmit: async (e) => {
      e.preventDefault();
      const org = await this.call("/api/orgs", { method: "POST", body: { name: nameInput.value.trim() } }, "Organization created");
      if (org) { this.orgId = org.id; await loadOrgs(); location.hash = `#/org/${org.id}`; await this.refresh(); }
    } }, fieldEl("New organization", nameInput), h("button", { class: "btn btn-primary", type: "submit" }, "Create"));

    const switcher = app.orgs.length
      ? (() => {
          const select = h("select", { class: "input" }, ...app.orgs.map((o) => h("option", { value: o.id }, `${o.name} (${o.role})`)));
          select.value = this.orgId ?? "";
          select.addEventListener("change", () => { location.hash = `#/org/${select.value}`; });
          return fieldEl("Organization", select);
        })()
      : null;

    return h("section", { class: "tile span-6 static" },
      h("div", { class: "tile-head" },
        h("div", { class: "grow" }, h("h2", {}, "Organization settings"),
          h("span", { class: "caption" }, "Share sessions with your team and connect Ashby or ModernLoop.")),
        h("a", { class: "btn btn-sm", href: "#/" }, "← Portal"),
      ),
      this.detail ? h("div", { class: "status-line" }, h("span", { class: "chip" }, this.detail.slug), `Your role: ${this.detail.role}`) : null,
      h("div", { class: "form-grid" }, switcher, createForm),
      app.orgs.length ? null : h("p", { class: "help" }, "Create an organization to invite interviewers, issue API keys, and connect your ATS."),
    );
  },

  membersTile() {
    const admin = this.isAdmin();
    const rows = this.detail.members.map((m) =>
      h("div", { class: "org-row" },
        h("span", { class: "grow", title: m.email }, m.email),
        h("span", { class: m.role === "admin" ? "chip" : "chip ended" }, m.role),
        admin && m.email !== app.me?.email ? h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: async () => {
          const ok = await promptDialog({ title: `Remove ${m.email}?`, message: "They lose access to this organization's sessions.", okLabel: "Remove", input: false });
          if (ok && (await this.call(`/api/orgs/${this.orgId}/members/${encodeURIComponent(m.email)}`, { method: "DELETE" }, "Member removed")) !== null) this.refresh();
        } }, "Remove") : null,
      ),
    );
    const email = h("input", { class: "input", type: "email", placeholder: "teammate@company.com", required: true });
    const role = h("select", { class: "input" }, h("option", { value: "interviewer" }, "Interviewer"), h("option", { value: "admin" }, "Admin"));
    const form = admin ? h("form", { class: "inline-form", onsubmit: async (e) => {
      e.preventDefault();
      if (await this.call(`/api/orgs/${this.orgId}/members`, { method: "POST", body: { email: email.value.trim(), role: role.value } }, "Member added")) this.refresh();
    } }, fieldEl("Email", email), fieldEl("Role", role), h("button", { class: "btn btn-primary", type: "submit" }, "Add")) : null;
    return h("section", { class: "tile span-3 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "Members"), h("span", { class: "caption" }, `${this.detail.members.length}`)),
      h("p", { class: "help" }, "Members can open every session in this organization. Interviewers from Ashby and ModernLoop are added automatically."),
      h("div", { class: "org-list" }, rows),
      form,
    );
  },

  apiKeysTile() {
    if (!this.isAdmin()) return null;
    const rows = this.detail.api_keys.map((k) =>
      h("div", { class: "org-row" },
        h("div", { class: "grow" }, h("strong", {}, k.name), h("div", { class: "meta" }, `${k.prefix}… · created ${formatDate(k.created_at)}${k.last_used_at ? ` · used ${formatDate(k.last_used_at)}` : ""}`)),
        h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: async () => {
          const ok = await promptDialog({ title: `Revoke ${k.name}?`, message: "Anything using this key stops working immediately.", okLabel: "Revoke", input: false });
          if (ok && (await this.call(`/api/orgs/${this.orgId}/api-keys/${k.id}`, { method: "DELETE" }, "Key revoked")) !== null) this.refresh();
        } }, "Revoke"),
      ),
    );
    const name = h("input", { class: "input", maxlength: "80", placeholder: "e.g. Ashby assessments", required: true });
    const form = h("form", { class: "inline-form", onsubmit: async (e) => {
      e.preventDefault();
      const created = await this.call(`/api/orgs/${this.orgId}/api-keys`, { method: "POST", body: { name: name.value.trim() } });
      if (created) {
        infoDialog("API key created", h("div", { class: "stack" }, h("p", { class: "help" }, "Copy it now. It won't be shown again."), copyField(created.key)));
        this.refresh();
      }
    } }, fieldEl("Key name", name), h("button", { class: "btn btn-primary", type: "submit" }, "Create key"));
    return h("section", { class: "tile span-3 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "API keys")),
      h("p", { class: "help" }, "Used by the public API (/v1/pads) and by Ashby's Assessments integration (paste the key into Ashby)."),
      h("div", { class: "org-list" }, rows.length ? rows : [h("div", { class: "empty" }, "No API keys yet.")]),
      form,
    );
  },

  integration(provider) {
    return this.detail.integrations.find((i) => i.provider === provider) ?? { provider, connected: false, settings: {} };
  },

  statusLine(integration) {
    return h("div", { class: "status-line" },
      integration.connected ? h("span", { class: "chip ok" }, "Connected") : h("span", { class: "chip ended" }, "Not connected"),
      integration.last_event_at ? `Last webhook ${formatDate(integration.last_event_at)}` : null,
      integration.last_synced_at ? `Last sync ${formatDate(integration.last_synced_at)}` : null,
      integration.last_error ? h("span", { class: "chip err", title: integration.last_error }, "Last error") : null,
    );
  },

  ashbyTile() {
    const integration = this.integration("ashby");
    const s = integration.settings || {};
    const admin = this.isAdmin();
    const apiKey = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: integration.connected ? "•••••• stored (leave blank to keep)" : "Ashby API key" });
    const fieldId = h("input", { class: "input", placeholder: "Candidate custom field ID (optional)", value: s.candidate_link_field_id ?? "" });
    const stages = h("input", { class: "input", placeholder: "Interview stage IDs, comma separated (blank = all)", value: (s.interview_stage_ids ?? []).join(", ") });
    const language = languageSelect(s.default_language);
    const toggles = {
      post_link_note: toggleEl("Post the join link as a candidate note", s.post_link_note ?? true),
      push_results_note: toggleEl("Post results and playback as a note", s.push_results_note ?? true),
      upload_files: toggleEl("Upload the final code (zip) to the candidate", s.upload_files ?? true),
      add_completed_assessment: toggleEl("Record live interviews as completed assessments", s.add_completed_assessment ?? false),
    };
    const save = async (extra = {}) => {
      const body = {
        settings: {
          candidate_link_field_id: fieldId.value.trim() || null,
          interview_stage_ids: splitIds(stages.value),
          default_language: language.value,
          ...Object.fromEntries(Object.entries(toggles).map(([k, t]) => [k, t.input.checked])),
        },
        ...extra,
      };
      if (apiKey.value.trim()) body.api_key = apiKey.value.trim();
      if (await this.call(`/api/orgs/${this.orgId}/integrations/ashby`, { method: "PUT", body }, "Ashby settings saved")) this.refresh();
    };
    const secretBox = h("div", {});
    const actions = admin ? h("div", { class: "row" },
      h("button", { class: "btn btn-primary", type: "button", onclick: () => save() }, integration.connected ? "Save" : "Connect"),
      integration.connected ? h("button", { class: "btn", type: "button", onclick: async () => {
        const result = await this.call(`/api/orgs/${this.orgId}/integrations/ashby/test`, { method: "POST" });
        if (result) toast(result.detail);
        this.refresh();
      } }, "Test") : null,
      integration.connected ? h("button", { class: "btn btn-danger", type: "button", onclick: async () => {
        const ok = await promptDialog({ title: "Disconnect Ashby?", message: "Webhooks will be rejected and results stop syncing.", okLabel: "Disconnect", input: false });
        if (ok && (await this.call(`/api/orgs/${this.orgId}/integrations/ashby`, { method: "DELETE" }, "Ashby disconnected")) !== null) this.refresh();
      } }, "Disconnect") : null,
    ) : null;

    const webhook = integration.connected && admin ? h("div", { class: "stack" },
      h("strong", {}, "Webhook"),
      h("p", { class: "help" }, "In Ashby: Admin → Integrations → Webhooks → New. Add interviewScheduleCreate and interviewScheduleUpdate with this URL and secret."),
      copyField(integration.webhook_url),
      secretBox,
      h("div", { class: "row" },
        h("button", { class: "btn btn-sm", type: "button", onclick: async () => {
          const secret = await this.call(`/api/orgs/${this.orgId}/integrations/ashby/webhook-secret`);
          if (secret) secretBox.replaceChildren(copyField(secret.webhook_secret, "Copy secret"));
        } }, "Reveal secret"),
        h("button", { class: "btn btn-sm", type: "button", onclick: async () => {
          const ok = await promptDialog({ title: "Rotate webhook secret?", message: "Update the secret in Ashby right after, or webhooks will be rejected.", okLabel: "Rotate", input: false });
          if (ok) save({ rotate_webhook_secret: true });
        } }, "Rotate"),
      ),
    ) : null;

    return h("section", { class: "tile span-3 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "Ashby")),
      this.statusLine(integration),
      h("p", { class: "help" }, "Creates a session for each scheduled interview, writes the join link back to Ashby, and posts results when the interview ends. ",
        h("a", { href: "https://github.com/levensailor/watchmecode.live/blob/main/docs/integrations/ashby.md", target: "_blank", rel: "noopener" }, "Setup guide")),
      admin ? h("div", { class: "form-grid" },
        fieldEl("API key", apiKey, "Candidates and Interviews read/write"),
        fieldEl("Join link field", fieldId, "Candidate Short answer field with Enable in Email Templates"),
        fieldEl("Interview stages", stages),
        fieldEl("Default language", language),
      ) : null,
      admin ? h("div", { class: "toggles" }, Object.values(toggles).map((t) => t.el)) : null,
      actions,
      webhook,
      h("div", { class: "stack" },
        h("strong", {}, "Assessments (take-homes)"),
        h("p", { class: "help" }, "Partner base URL for Ashby's Assessments integration. Ashby authenticates with a watchmecode API key from this page."),
        copyField(integration.partner_base_url),
      ),
    );
  },

  modernLoopTile() {
    const integration = this.integration("modernloop");
    const s = integration.settings || {};
    const admin = this.isAdmin();
    const token = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: integration.connected ? "•••••• stored (leave blank to keep)" : "ModernLoop API token" });
    const stages = h("input", { class: "input", placeholder: "Job stage IDs, comma separated (blank = all)", value: (s.job_stage_ids ?? []).join(", ") });
    const language = languageSelect(s.default_language);
    const skip = toggleEl("Skip candidates synced from Ashby (handled by Ashby webhooks)", s.skip_ashby_candidates ?? true);
    const result = h("div", { class: "help" });
    const actions = admin ? h("div", { class: "row" },
      h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
        const body = { settings: { job_stage_ids: splitIds(stages.value), default_language: language.value, skip_ashby_candidates: skip.input.checked } };
        if (token.value.trim()) body.api_token = token.value.trim();
        if (await this.call(`/api/orgs/${this.orgId}/integrations/modernloop`, { method: "PUT", body }, "ModernLoop settings saved")) this.refresh();
      } }, integration.connected ? "Save" : "Connect"),
      integration.connected ? h("button", { class: "btn", type: "button", onclick: async () => {
        const res = await this.call(`/api/orgs/${this.orgId}/integrations/modernloop/test`, { method: "POST" });
        if (res) toast(res.detail);
      } }, "Test") : null,
      integration.connected ? h("button", { class: "btn", type: "button", onclick: async () => {
        result.textContent = "Syncing…";
        const res = await this.call(`/api/orgs/${this.orgId}/integrations/modernloop/sync`, { method: "POST" });
        result.textContent = res ? `Created ${res.created}, ended ${res.ended}, skipped ${res.skipped}${res.errors.length ? ` · ${res.errors[0]}` : ""}` : "";
      } }, "Sync now") : null,
      integration.connected ? h("button", { class: "btn btn-danger", type: "button", onclick: async () => {
        const ok = await promptDialog({ title: "Disconnect ModernLoop?", message: "Scheduled syncs stop for this organization.", okLabel: "Disconnect", input: false });
        if (ok && (await this.call(`/api/orgs/${this.orgId}/integrations/modernloop`, { method: "DELETE" }, "ModernLoop disconnected")) !== null) this.refresh();
      } }, "Disconnect") : null,
    ) : null;
    return h("section", { class: "tile span-3 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "ModernLoop")),
      this.statusLine(integration),
      h("p", { class: "help" }, "Checks ModernLoop every 10 minutes and creates a session for each newly scheduled interview. ",
        h("a", { href: "https://github.com/levensailor/watchmecode.live/blob/main/docs/integrations/modernloop.md", target: "_blank", rel: "noopener" }, "Setup guide")),
      admin ? h("div", { class: "form-grid" }, fieldEl("API token", token, "Platform API token with read scopes"), fieldEl("Job stages", stages), fieldEl("Default language", language)) : null,
      admin ? skip.el : null,
      actions,
      result,
    );
  },

  templatesTile() {
    const editing = this.editing;
    const rows = this.templates.map((t) =>
      h("div", { class: "org-row" },
        h("div", { class: "grow" }, h("strong", {}, t.name), h("div", { class: "meta" }, `${languageLabel(t.language)} · ${t.time_limit_minutes ? `${t.time_limit_minutes} min` : "no limit"} · ${Object.keys(t.files).length} file(s)`)),
        h("button", { class: "btn btn-sm", type: "button", onclick: () => { this.editing = t; this.render(); } }, "Edit"),
        h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: async () => {
          const ok = await promptDialog({ title: `Delete ${t.name}?`, message: "Ashby can no longer start this assessment.", okLabel: "Delete", input: false });
          if (ok && (await this.call(`/api/orgs/${this.orgId}/assessments/${t.id}`, { method: "DELETE" }, "Assessment deleted")) !== null) this.refresh();
        } }, "Delete"),
      ),
    );
    const name = h("input", { class: "input", maxlength: "120", required: true, value: editing?.name ?? "", placeholder: "e.g. Rate limiter (Python)" });
    const description = h("input", { class: "input", maxlength: "2000", value: editing?.description ?? "", placeholder: "Shown to recruiters in Ashby" });
    const language = languageSelect(editing?.language);
    const minutes = h("input", { class: "input", type: "number", min: "5", max: "1440", value: String(editing?.time_limit_minutes ?? 60) });
    const lang = () => app.languages.find((l) => l.id === language.value);
    const entryPath = () => (editing && Object.keys(editing.files)[0]) || lang()?.entry || "main.py";
    const starter = h("textarea", { class: "input", placeholder: "Starter code for the entry file (optional; blank uses the language template)" });
    starter.value = editing ? editing.files[entryPath()] ?? "" : "";
    const form = h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      const body = { name: name.value.trim(), description: description.value.trim(), language: language.value, time_limit_minutes: Number(minutes.value) || null };
      if (starter.value.trim()) body.files = { ...(editing && editing.language === language.value ? editing.files : {}), [entryPath()]: starter.value };
      const path = editing ? `/api/orgs/${this.orgId}/assessments/${editing.id}` : `/api/orgs/${this.orgId}/assessments`;
      if (await this.call(path, { method: editing ? "PUT" : "POST", body }, editing ? "Assessment updated" : "Assessment created")) {
        this.editing = null;
        this.refresh();
      }
    } },
      h("div", { class: "form-grid" }, fieldEl("Name", name), fieldEl("Language", language), fieldEl("Time limit (minutes)", minutes)),
      fieldEl("Description", description),
      fieldEl(`Starter code (${entryPath()})`, starter),
      h("div", { class: "row" },
        h("button", { class: "btn btn-primary", type: "submit" }, editing ? "Save assessment" : "Add assessment"),
        editing ? h("button", { class: "btn", type: "button", onclick: () => { this.editing = null; this.render(); } }, "Cancel") : null,
      ),
    );
    return h("section", { class: "tile span-6 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "Take-home assessments"), h("span", { class: "caption" }, `${this.templates.length}`)),
      h("p", { class: "help" }, "Ashby lists these when a recruiter adds a watchmecode assessment to an interview plan. Candidates get a timed, solo IDE with a Submit button."),
      h("div", { class: "org-list" }, rows.length ? rows : [h("div", { class: "empty" }, "No assessments yet.")]),
      form,
    );
  },
};

async function joinWithLink(token) {
  try {
    const joined = await api("/api/join/link", { method: "POST", body: { token } });
    localStorage.setItem(STORAGE.token(joined.session.id), joined.token);
    location.replace(`#/s/${joined.session.id}`);
  } catch (err) {
    toast(err.status === 410 ? "This interview has ended." : err.message);
    location.replace("#/");
  }
}

/* Workspace & billing (multi-tenant) ------------------------------------------------- */

const workspaceView = {
  data: null,
  interval: "annual",

  async open(query) {
    for (const id of ["view-home", "view-ide", "view-player", "view-org"]) $(id).hidden = true;
    $("view-workspace").hidden = false;
    document.title = `Workspace · ${app.info?.app_name ?? ""}`;
    renderUser();
    const billing = new URLSearchParams(query || "").get("billing");
    if (billing === "success") toast("Thanks! Your plan updates as soon as Stripe confirms the payment.");
    if (billing === "cancelled") toast("Checkout cancelled. Nothing was charged.");
    if (!app.me) {
      $("view-workspace").replaceChildren(
        h("section", { class: "tile span-6 static" }, h("h2", {}, "Sign in required"),
          h("p", { class: "help" }, app.forbidden || "Sign in to see this workspace's plan and members."),
          app.info?.google_enabled ? h("a", { class: "btn btn-primary", href: "/auth/google?next=/%23/workspace" }, "Interviewer sign in") : null),
      );
      return;
    }
    await this.refresh();
  },

  async refresh() {
    try {
      this.data = await api("/api/workspace");
      if (this.data.billing_interval) this.interval = this.data.billing_interval;
    } catch (err) {
      toast(err.message);
      return;
    }
    this.render();
  },

  isAdmin() {
    return ["owner", "admin"].includes(this.data?.role);
  },

  async go(path) {
    try {
      const { url } = await api(path, { method: "POST", body: path.endsWith("checkout") ? this.pending : undefined });
      location.href = url;
    } catch (err) {
      toast(err.message);
    }
  },

  render() {
    const tiles = [this.headerTile(), this.usageTile()];
    if (this.isAdmin()) tiles.push(this.plansTile(), this.membersTile(), this.domainsTile());
    $("view-workspace").replaceChildren(...tiles.filter(Boolean));
    staggerTiles($("view-workspace"));
  },

  headerTile() {
    const d = this.data;
    return h("section", { class: "tile span-6 static" },
      h("div", { class: "tile-head" },
        h("div", { class: "grow" }, h("h2", {}, d.name),
          h("span", { class: "caption" }, `${d.url.replace(/^https?:\/\//, "")} · your role: ${d.role}`)),
        h("a", { class: "btn btn-sm", href: "#/" }, "← Portal"),
      ),
      h("div", { class: "status-line" },
        h("span", { class: `chip ${["expired", "past_due", "canceled"].includes(d.state) ? "chip-warn" : "chip-ok"}` }, workspaceStatus(d)),
        d.state === "expired" ? "Choose a plan to keep creating sessions. Your sessions and recordings are kept." : null),
    );
  },

  usageTile() {
    const u = this.data.usage;
    const pct = u.credits_included ? Math.min(100, Math.round((u.credits_used / u.credits_included) * 100)) : 0;
    const over = u.credits_included != null && u.credits_used > u.credits_included;
    return h("section", { class: "tile span-3 static" },
      h("h2", {}, "Interview credits"),
      h("p", { class: "help" }, "One credit when a candidate joins a live interview or starts a take-home. Practice sessions are free."),
      h("div", { class: "meter-label" }, h("strong", {}, `${u.credits_used}`), ` of ${u.credits_included ?? "∞"} used this period`),
      h("div", { class: `meter${over ? " over" : ""}`, role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100" },
        h("span", { style: `width: ${pct}%` })),
      u.overage_rate ? h("p", { class: "help" }, `Past the allowance: $${u.overage_rate} per credit.`) : null,
      this.data.trial_days_left != null ? h("p", { class: "help" }, `Your trial ends in ${this.data.trial_days_left} days. Upgrading keeps every session.`) : null,
      this.isAdmin() && this.data.has_billing_account
        ? h("button", { class: "btn", type: "button", onclick: () => this.go("/api/workspace/portal") }, "Manage billing & invoices")
        : null,
    );
  },

  plansTile() {
    const d = this.data;
    const intervalToggle = h("div", { class: "segmented", role: "group", "aria-label": "Billing interval" },
      ...[["annual", "Annual"], ["monthly", "Monthly"]].map(([value, label]) =>
        h("button", { type: "button", class: this.interval === value ? "active" : "", "aria-pressed": String(this.interval === value),
          onclick: () => { this.interval = value; this.render(); } }, label)));
    const cards = d.plans.filter((p) => ["starter", "team"].includes(p.id)).map((p) => {
      const price = this.interval === "annual" ? p.annual_price : p.monthly_price;
      const current = d.plan === p.id && d.state !== "expired";
      return h("div", { class: `plan-card${p.id === "team" ? " featured" : ""}` },
        h("div", { class: "row" }, h("strong", { class: "grow" }, p.name), p.id === "team" ? h("span", { class: "chip" }, "Most popular") : null),
        h("div", { class: "plan-price" }, `$${price}`, h("span", {}, "/mo")),
        h("span", { class: "caption" }, this.interval === "annual" ? "billed annually" : "billed monthly"),
        h("span", { class: "help" }, `${p.credits} credits a month · $${p.overage} per extra credit`),
        h("button", { class: `btn ${p.id === "team" ? "btn-primary" : ""}`, type: "button", disabled: current || !d.billing_enabled,
          onclick: () => { this.pending = { plan: p.id, interval: this.interval }; this.go("/api/workspace/checkout"); } },
          current ? "Current plan" : `Choose ${p.name}`),
      );
    });
    return h("section", { class: "tile span-3 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "Plans"), intervalToggle),
      h("div", { class: "plan-cards" }, ...cards),
      d.billing_enabled ? h("p", { class: "help" }, "Checkout is handled by Stripe. Need a dedicated instance or SSO? Talk to us about Enterprise.")
        : h("p", { class: "help" }, "Billing isn't configured on this server yet."),
    );
  },

  membersTile() {
    const rows = this.data.members.map((m) =>
      h("div", { class: "org-row" },
        h("span", { class: "grow", title: m.email }, m.email),
        h("span", { class: `chip ${m.role === "interviewer" ? "chip-muted" : ""}` }, m.role),
        m.role === "owner" ? null : h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: async () => {
          try { await api(`/api/workspace/members/${encodeURIComponent(m.email)}`, { method: "DELETE" }); toast("Removed"); await this.refresh(); }
          catch (err) { toast(err.message); }
        } }, "Remove"),
      ));
    const email = h("input", { class: "input", type: "email", placeholder: "teammate@company.com", required: true });
    const role = h("select", { class: "input" }, h("option", { value: "interviewer" }, "Interviewer"), h("option", { value: "admin" }, "Admin"));
    const form = h("form", { class: "inline-form", onsubmit: async (e) => {
      e.preventDefault();
      try { await api("/api/workspace/members", { method: "POST", body: { email: email.value.trim(), role: role.value } }); toast("Member added"); await this.refresh(); }
      catch (err) { toast(err.message); }
    } }, fieldEl("Email", email), fieldEl("Role", role), h("button", { class: "btn btn-primary", type: "submit" }, "Add"));
    return h("section", { class: "tile span-3 static" },
      h("div", { class: "tile-head" }, h("h2", { class: "grow" }, "Members"), h("span", { class: "caption" }, String(this.data.members.length))),
      h("p", { class: "help" }, "Members sign in with Google at this subdomain. Interviewers are unlimited on every plan."),
      h("div", { class: "org-list" }, ...rows),
      form,
    );
  },

  domainsTile() {
    const input = h("input", { class: "input", placeholder: "acme.com, acme.io", value: this.data.allowed_domains.join(", ") });
    return h("section", { class: "tile span-3 static" },
      h("h2", {}, "Sign-in domains"),
      h("p", { class: "help" }, "Anyone with a Google account on these domains can sign in as an interviewer without an invite."),
      h("form", { class: "inline-form", onsubmit: async (e) => {
        e.preventDefault();
        try {
          this.data = await api("/api/workspace", { method: "PUT", body: { allowed_domains: splitIds(input.value) } });
          toast("Saved");
          this.render();
        } catch (err) { toast(err.message); }
      } }, fieldEl("Domains", input), h("button", { class: "btn btn-primary", type: "submit" }, "Save")),
    );
  },
};

/* Routing ----------------------------------------------------------------------------- */

async function route() {
  closeMenus();
  const invite = location.hash.match(/^#\/j\/([A-Za-z0-9._\-]+)/);
  if (invite) {
    await joinWithLink(decodeURIComponent(invite[1]));
    return;
  }
  const workspaceRoute = location.hash.match(/^#\/workspace(?:\?(.*))?$/);
  if (workspaceRoute && app.info?.workspace) {
    if (ide.sessionId) await ide.flushAll();
    ide.close();
    player.close();
    await workspaceView.open(workspaceRoute[1]);
    return;
  }
  $("view-workspace").hidden = true;
  const orgRoute = location.hash.match(/^#\/org(?:\/([A-Za-z0-9]+))?/);
  if (orgRoute) {
    if (ide.sessionId) await ide.flushAll();
    ide.close();
    player.close();
    await orgView.open(orgRoute[1]);
    return;
  }
  $("view-org").hidden = true;
  const playback = location.hash.match(/^#\/s\/([A-Za-z0-9]+)\/play\/([A-Za-z0-9]+)/);
  if (playback) {
    if (ide.sessionId) await ide.flushAll();
    ide.close();
    await player.open(playback[1], playback[2]);
    return;
  }
  player.close();
  const match = location.hash.match(/^#\/s\/([A-Za-z0-9]+)/);
  if (match) {
    if (ide.sessionId !== match[1]) await ide.open(match[1]);
    return;
  }
  if (ide.sessionId) await ide.flushAll();
  ide.close();
  $("view-ide").hidden = true;
  $("view-home").hidden = false;
  document.title = `${app.info?.app_name ?? ""} · ${app.info?.tagline ?? ""}`;
  renderUser();
  renderHome();
}

async function init() {
  setupMenus();
  setupHome();
  setupIde();
  setupPlayer();
  $("theme-toggle").addEventListener("click", toggleTheme);

  const params = new URLSearchParams(location.search);
  if (params.has("auth_error")) {
    toast(`Sign-in failed: ${params.get("auth_error")}`);
    history.replaceState(null, "", location.pathname + location.hash);
  }

  const [info, languages] = await Promise.all([api("/api/info"), api("/api/languages")]);
  app.info = info;
  app.languages = languages;
  $("language-select").replaceChildren(...languages.map((l) => h("option", { value: l.id }, l.label)));
  await loadIdentity();
  window.addEventListener("hashchange", route);
  await route();
}

init().catch((err) => {
  console.error(err);
  toast(`Failed to load: ${err.message}`);
});
