"""Seed a demo workspace through the HTTP API (used for marketing screenshots and demos).

Usage:
    uv run python scripts/seed_demo.py --base http://localhost:8000 [--email jordan@acme.example]

The interviewer session cookie is minted with SESSION_SECRET, so run this against an
instance that shares your .env (local dev or the internal demo workspace).
"""

from __future__ import annotations

import argparse
import base64
import json
import time

import httpx
from itsdangerous import TimestampSigner

from app.config import load_settings

SESSION_COOKIE = "wmc_session"

TWO_SUM = '''from typing import List


def two_sum(nums: List[int], target: int) -> List[int]:
    """Return indices of the two numbers that add up to target."""
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target - n], i]
        seen[n] = i
    return []


if __name__ == "__main__":
    print(two_sum([2, 7, 11, 15], 9))
    print(two_sum([3, 2, 4], 6))
'''

HTTP_CHECK = '''import requests

response = requests.get("https://httpbin.org/json", timeout=5)
print(response.status_code, response.json()["slideshow"]["title"])
'''

WEB_HTML = '''<!doctype html>
<html lang="en">
  <head>
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <main class="card">
      <h1>Pricing calculator</h1>
      <label>Interviews per month <input id="n" type="range" min="5" max="100" value="40" /></label>
      <p id="out"></p>
    </main>
    <script src="script.js"></script>
  </body>
</html>
'''

WEB_CSS = '''body { font-family: system-ui, sans-serif; display: grid; place-items: center; min-height: 100vh; margin: 0;
  background: linear-gradient(135deg, #f5f5f7, #ece9fb); }
.card { background: #fff; padding: 28px 36px; border-radius: 24px; box-shadow: 0 8px 24px rgba(0,0,0,.08); width: 320px; }
h1 { margin-top: 0; color: #6e56cf; }
input { width: 100%; }
#out { font-size: 32px; font-weight: 700; }
'''

WEB_JS = '''const slider = document.getElementById("n");
const out = document.getElementById("out");
function render() {
  const n = Number(slider.value);
  out.textContent = `${n} interviews → $${n <= 50 ? 249 : 249 + (n - 50) * 10}/mo`;
  console.log("interviews", n);
}
slider.addEventListener("input", render);
render();
'''

LRU = '''class LRUCache<K, V> {
  private map = new Map<K, V>();
  constructor(private capacity: number) {}

  get(key: K): V | undefined {
    if (!this.map.has(key)) return undefined;
    const value = this.map.get(key)!;
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  put(key: K, value: V): void {
    this.map.delete(key);
    this.map.set(key, value);
    if (this.map.size > this.capacity) this.map.delete(this.map.keys().next().value!);
  }
}

const cache = new LRUCache<string, number>(2);
cache.put("a", 1);
cache.put("b", 2);
cache.get("a");
cache.put("c", 3);
console.log([...["a", "b", "c"]].map((k) => `${k}=${cache.get(k) ?? "evicted"}`).join(" "));
'''


def cookie_for(email: str, name: str) -> str:
    settings = load_settings()
    payload = base64.b64encode(json.dumps({"user": {"name": name, "email": email, "picture": None}}).encode())
    return TimestampSigner(settings.session_secret).sign(payload).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--email", default="jordan@acme.example")
    parser.add_argument("--name", default="Jordan Lee")
    args = parser.parse_args()

    iv = httpx.Client(base_url=args.base, cookies={SESSION_COOKIE: cookie_for(args.email, args.name)}, timeout=120)
    iv.headers["X-Client-Id"] = "seed-interviewer"

    orgs = iv.get("/api/orgs").json()
    org = next((o for o in orgs if o["name"] == "Acme Engineering"), None) or iv.post("/api/orgs", json={"name": "Acme Engineering"}).json()
    oid = org["id"]
    for email, role in (("priya@acme.example", "admin"), ("sam@acme.example", "interviewer"), ("alex@acme.example", "interviewer")):
        iv.post(f"/api/orgs/{oid}/members", json={"email": email, "role": role})
    if not iv.get(f"/api/orgs/{oid}/api-keys").json():
        iv.post(f"/api/orgs/{oid}/api-keys", json={"name": "Ashby assessments"})
        iv.post(f"/api/orgs/{oid}/api-keys", json={"name": "ModernLoop"})
    iv.put(
        f"/api/orgs/{oid}/integrations/ashby",
        json={"api_key": "demo-ashby-key", "settings": {"candidate_link_field_id": "5f1c2a7e-demo", "add_completed_assessment": True}},
    )
    iv.put(f"/api/orgs/{oid}/integrations/modernloop", json={"api_token": "demo-modernloop-token"})
    if not iv.get(f"/api/orgs/{oid}/assessments").json():
        iv.post(f"/api/orgs/{oid}/assessments", json={"name": "Rate limiter", "description": "Token bucket with tests", "language": "python", "time_limit_minutes": 45})
        iv.post(f"/api/orgs/{oid}/assessments", json={"name": "LRU cache", "description": "Generic LRU in TypeScript", "language": "typescript", "time_limit_minutes": 60})

    def session(name: str, language: str) -> dict:
        return iv.post("/api/sessions", json={"candidate_name": name, "language": language, "org_id": oid}).json()

    def join(pin: str) -> httpx.Client:
        candidate = httpx.Client(base_url=args.base, timeout=120, headers={"X-Client-Id": "seed-candidate"})
        token = candidate.post("/api/join", json={"pin": pin}).json()["token"]
        candidate.headers["Authorization"] = f"Bearer {token}"
        return candidate

    ada = session("Ada Lovelace", "python")
    cand = join(ada["pin"])
    rid = iv.post(f"/api/sessions/{ada['id']}/recordings").json()["id"]
    for end in range(40, len(TWO_SUM) + 40, 40):
        cand.put(f"/api/sessions/{ada['id']}/files", json={"path": "solution.py", "content": TWO_SUM[:end]})
        time.sleep(0.25)
    cand.post(f"/api/sessions/{ada['id']}/run", json={"entry_path": "solution.py"})
    iv.put(f"/api/sessions/{ada['id']}/files", json={"path": "http_check.py", "content": HTTP_CHECK})
    iv.post(f"/api/sessions/{ada['id']}/run", json={"entry_path": "http_check.py"})
    iv.post(f"/api/sessions/{ada['id']}/recordings/{rid}/stop")

    grace = session("Grace Hopper", "web")
    gc = join(grace["pin"])
    for path, content in (("index.html", WEB_HTML), ("style.css", WEB_CSS), ("script.js", WEB_JS)):
        gc.put(f"/api/sessions/{grace['id']}/files", json={"path": path, "content": content})

    linus = session("Linus Torvalds", "typescript")
    lc = join(linus["pin"])
    lc.put(f"/api/sessions/{linus['id']}/files", json={"path": "index.ts", "content": LRU})
    lc.post(f"/api/sessions/{linus['id']}/run", json={"entry_path": "index.ts"})
    iv.post(f"/api/sessions/{linus['id']}/end")

    for name, language in (("Katherine Johnson", "go"), ("Margaret Hamilton", "java"), ("Alan Turing", "cpp")):
        session(name, language)

    print(json.dumps({"org": oid, "ada": ada["id"], "ada_recording": rid, "grace": grace["id"], "linus": linus["id"]}, indent=2))


if __name__ == "__main__":
    main()
