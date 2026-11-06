/* watchmecode.live product site: theme, nav, pricing toggle, signup, and sign-in. */
(() => {
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const SLUG = /^[a-z][a-z0-9-]{1,30}$/;

  // Old links like watchmecode.live/#/s/<id> belong to the default workspace now.
  const legacy = body.dataset.legacyApp;
  if (legacy && /^#\/(s|j|org)\//.test(location.hash)) {
    location.replace(`${legacy}/${location.hash}`);
    return;
  }

  const workspaceUrl = (slug) => (body.dataset.tenantTemplate || "").replace("__slug__", slug);

  $("theme-toggle")?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("wmc:theme", next);
  });

  $("nav-toggle")?.addEventListener("click", (event) => {
    const open = document.querySelector(".site-top").classList.toggle("nav-open");
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });

  const toggle = $("interval-toggle");
  toggle?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-interval]");
    if (!button) return;
    const interval = button.dataset.interval;
    toggle.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    });
    document.querySelectorAll("[data-annual][data-monthly]").forEach((el) => {
      el.textContent = el.dataset[interval];
    });
  });

  async function platformMe() {
    try {
      const response = await fetch("/api/platform/me", { credentials: "same-origin" });
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  }

  function slugFrom(name) {
    return name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 31);
  }

  async function setupSignup() {
    const form = $("signup-form");
    if (!form) return;
    const me = await platformMe();
    const name = $("ws-name");
    const slug = $("ws-slug");
    const hint = $("slug-hint");
    const create = $("create-btn");
    if (me?.user) {
      $("account-status").textContent = `Signed in as ${me.user.email}.`;
      $("google-btn")?.remove();
      $("step-account").classList.add("done");
      $("step-workspace").classList.remove("disabled");
      [name, slug, create].forEach((el) => { el.disabled = false; });
      name.focus();
    }
    let slugEdited = false;
    let checkTimer = null;
    let available = false;

    const check = () => {
      clearTimeout(checkTimer);
      available = false;
      const value = slug.value.trim().toLowerCase();
      if (!value) { hint.textContent = "Lowercase letters, numbers, and hyphens."; hint.className = "hint"; return; }
      if (!SLUG.test(value)) { hint.textContent = "Start with a letter; use 2–31 lowercase letters, numbers, or hyphens."; hint.className = "hint bad"; return; }
      hint.textContent = "Checking…";
      hint.className = "hint";
      checkTimer = setTimeout(async () => {
        const result = await fetch(`/api/platform/subdomains/${encodeURIComponent(value)}`).then((r) => r.json()).catch(() => null);
        if (slug.value.trim().toLowerCase() !== value || !result) return;
        available = result.available;
        hint.textContent = result.available ? `${value}.${document.querySelector("[data-root]").dataset.root} is available` : result.reason;
        hint.className = `hint ${result.available ? "good" : "bad"}`;
      }, 250);
    };

    name.addEventListener("input", () => { if (!slugEdited) { slug.value = slugFrom(name.value); check(); } });
    slug.addEventListener("input", () => { slugEdited = true; slug.value = slug.value.toLowerCase(); check(); });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      $("signup-error").textContent = "";
      if (!name.value.trim() || !available) {
        $("signup-error").textContent = !name.value.trim() ? "Enter your company or team name." : "Pick an available subdomain.";
        return;
      }
      create.disabled = true;
      create.textContent = "Creating…";
      const response = await fetch("/api/platform/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.value.trim(), slug: slug.value.trim() }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = Array.isArray(result.detail) ? result.detail[0]?.msg : result.detail;
        $("signup-error").textContent = detail || "Something went wrong. Try again.";
        create.disabled = false;
        create.textContent = "Create workspace";
        return;
      }
      create.textContent = "Opening your workspace…";
      location.href = result.go_url;
    });
  }

  async function setupLogin() {
    if (!$("login")) return;
    const me = await platformMe();
    if (me?.user) {
      $("signed-out").hidden = true;
      $("signed-in").hidden = false;
      $("me-email").textContent = me.user.email;
      const list = $("workspace-list");
      $("no-workspaces").hidden = me.workspaces.length > 0;
      for (const ws of me.workspaces) {
        const row = document.createElement("a");
        row.className = "workspace-row";
        row.href = `/api/platform/go/${encodeURIComponent(ws.slug)}?next=/`;
        const label = ws.state === "trialing" ? `Trial · ${ws.trial_days_left} days left` : ws.state === "expired" ? "Trial ended" : ws.plan_name;
        row.innerHTML = `<span><strong></strong><small></small></span><span class="chip"></span>`;
        row.querySelector("strong").textContent = ws.name;
        row.querySelector("small").textContent = ws.url.replace(/^https?:\/\//, "");
        row.querySelector(".chip").textContent = label;
        list.append(row);
      }
    }
    $("goto-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const value = $("goto-slug").value.trim().toLowerCase();
      if (SLUG.test(value)) location.href = me?.user ? `/api/platform/go/${value}?next=/` : `${workspaceUrl(value)}/`;
    });
    $("candidate-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const value = $("cand-slug").value.trim().toLowerCase();
      const pin = $("cand-pin").value.trim();
      if (SLUG.test(value) && /^\d{6}$/.test(pin)) location.href = `${workspaceUrl(value)}/?pin=${pin}`;
    });
  }

  setupSignup();
  setupLogin();
})();
