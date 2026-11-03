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
const STORAGE = { theme: "wmc:theme", live: "wmc:live", token: (id) => `wmc:token:${id}` };

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
};

function languageLabel(id) {
  return app.languages.find((l) => l.id === id)?.label ?? id;
}

function runnableLanguage(path) {
  const lower = path.toLowerCase();
  return app.languages.find((l) => l.extensions.some((ext) => lower.endsWith(ext)));
}

function renderUser() {
  const signedIn = Boolean(app.me);
  const inIde = !$("view-ide").hidden;
  $("user-menu").hidden = !signedIn;
  $("signin-btn").hidden = signedIn || inIde || !app.info?.google_enabled;
  if (!signedIn) return;
  $("user-name").textContent = app.me.name;
  $("user-email").textContent = app.me.email;
  const avatar = app.me.picture
    ? h("img", { class: "avatar", src: app.me.picture, alt: "", referrerpolicy: "no-referrer" })
    : h("span", { class: "avatar" }, (app.me.name || "?").slice(0, 1).toUpperCase());
  $("user-avatar").replaceChildren(avatar);
}

async function loadIdentity() {
  try {
    app.me = await api("/api/me");
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) throw err;
    app.me = null;
  }
  renderUser();
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
        h("h3", {}, group.name, h("span", { class: "caption" }, `${group.sessions.length} session${group.sessions.length > 1 ? "s" : ""}`)),
        ...group.sessions.map((s) =>
          h("div", { class: "session-row" },
            h("div", {},
              h("div", { class: "row" },
                h("strong", {}, languageLabel(s.language)),
                s.status === "active" ? h("span", { class: "chip" }, `PIN ${s.pin}`) : h("span", { class: "chip ended" }, "Ended"),
              ),
              h("div", { class: "meta" }, `Created ${formatDate(s.created_at)} · updated ${formatDate(s.updated_at)}`),
            ),
            h("div", { class: "row" },
              s.status === "active" ? h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: () => endSession(s) }, "End") : null,
              h("a", { class: "btn btn-sm btn-primary", href: `#/s/${s.id}` }, "Open"),
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
      const session = await api("/api/sessions", {
        method: "POST",
        body: { candidate_name, language: $("language-select").value },
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
    this.setSync("ok", "Synced");
    this.schedulePoll(POLL_MS);
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
    $("menubar").hidden = true;
    $("session-chip").hidden = true;
    $("ide-status").hidden = true;
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
        toast("The interviewer ended this session.");
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

/* Routing ----------------------------------------------------------------------------- */

async function route() {
  const match = location.hash.match(/^#\/s\/([A-Za-z0-9]+)/);
  closeMenus();
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
