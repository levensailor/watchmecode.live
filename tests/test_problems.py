import json

import httpx
import pytest

from app.problems.catalog import parse_csv, slug_from_link
from app.problems.generate import ProblemGenerationError, build, leaks
from app.languages import LanguageId

CSV = """Difficulty,Title,Frequency,Acceptance Rate,Link,Topics
EASY,Two Sum,100.0,0.0058036178765062,https://leetcode.com/problems/two-sum,"Array, Hash Table"
HARD,Median of Two Sorted Arrays,74.3,0.004785841111513346,https://leetcode.com/problems/median-of-two-sorted-arrays,"Array, Binary Search"
MEDIUM,Second Highest Salary,60.0,0.0048,https://leetcode.com/problems/second-highest-salary,Database
MEDIUM,Broken Row,10,0.5,https://example.com/not-a-problem,Array
"""

LISTING = {
    "files": [
        {"name": "/.gitignore"},
        {"name": "/Acme Corp/1. Thirty Days.csv"},
        {"name": "/Acme Corp/5. All.csv"},
        {"name": "/Globex/5. All.csv"},
        {"name": "/README.md"},
    ]
}

GENERATED = {
    "name": "Pair Ledger",
    "summary": "Find two ledger entries that balance to a target.",
    "statement_md": "# Pair Ledger\n\nGiven a list of ledger entries and a target, return the indices of two entries "
    "that add up to the target.\n\n## Example\n\n- entries `[3, 5, 9]`, target `14` -> `[1, 2]`",
    "stub": 'def find_pair(entries, target):\n    """Return the indices of two entries that sum to target."""\n'
    "    return []\n\n\nif __name__ == \"__main__\":\n    print(find_pair([3, 5, 9], 14))\n",
    "interviewer_notes": {
        "approach": "One pass with a hash map from value to index.",
        "complexity": "O(n) time, O(n) space.",
        "hints": ["What do you need to remember about earlier entries?"],
        "follow_ups": ["Return every pair."],
        "pitfalls": ["Using the same entry twice."],
    },
}


class FakeSources:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.generated = dict(GENERATED)
        self.gateway_status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "data.jsdelivr.com":
            return httpx.Response(200, json=LISTING)
        if host == "raw.githubusercontent.com":
            return httpx.Response(200, text=CSV)
        if host == "ai-gateway.vercel.sh":
            if self.gateway_status != 200:
                return httpx.Response(self.gateway_status, json={"error": {"message": "down"}})
            return httpx.Response(200, json={
                "model": "anthropic/claude-sonnet-5.5",
                "choices": [{"message": {"content": json.dumps(self.generated)}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 900},
            })
        return httpx.Response(404)

    def count(self, host: str) -> int:
        return sum(1 for r in self.requests if r.url.host == host)


@pytest.fixture
def sources(app, settings, monkeypatch):
    fake = FakeSources()
    app.state.http_transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(app.state, "settings", settings.__class__(**{**settings.__dict__, "ai_gateway_api_key": "test-key"}))
    return fake


REF = {"company": "Acme Corp", "window": "all", "slug": "two-sum"}


def test_parse_csv_reads_rows_and_skips_sql_and_broken_ones():
    rows = parse_csv(CSV)
    assert [r["slug"] for r in rows] == ["two-sum", "median-of-two-sorted-arrays"]
    assert rows[0] == {
        "slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "frequency": 100.0,
        "acceptance": 58.0, "topics": ["Array", "Hash Table"],
    }
    assert slug_from_link("https://leetcode.com/problems/two-sum/") == "two-sum"
    assert slug_from_link("https://leetcode.com/problems/../etc") is None


def test_companies_and_lists_come_from_the_source_and_are_cached(interviewer, sources):
    catalog = interviewer.get("/api/problems/companies").json()
    assert catalog["source_url"] == "https://github.com/liquidslr/leetcode-company-wise-problems"
    assert [c["name"] for c in catalog["companies"]] == ["Acme Corp", "Globex"]
    assert catalog["companies"][0]["windows"] == [
        {"id": "thirty-days", "label": "Last 30 days"}, {"id": "all", "label": "All time"},
    ]
    assert "python" in catalog["languages"] and "sql" not in catalog["languages"]

    listed = interviewer.get("/api/problems", params={"company": "Acme Corp", "window": "all"}).json()
    assert [p["title"] for p in listed["problems"]] == ["Two Sum", "Median of Two Sorted Arrays"]
    raw = [r for r in sources.requests if r.url.host == "raw.githubusercontent.com"]
    assert str(raw[0].url) == (
        "https://raw.githubusercontent.com/liquidslr/leetcode-company-wise-problems/main/Acme%20Corp/5.%20All.csv"
    )
    interviewer.get("/api/problems", params={"company": "Acme Corp", "window": "all"})
    interviewer.get("/api/problems/companies")
    assert sources.count("raw.githubusercontent.com") == 1
    assert sources.count("data.jsdelivr.com") == 1


def test_unknown_companies_windows_and_paths_are_rejected(interviewer, sources):
    for params in ({"company": "Nope", "window": "all"}, {"company": "Globex", "window": "thirty-days"},
                   {"company": "../../etc", "window": "all"}):
        assert interviewer.get("/api/problems", params=params).status_code == 404
    assert sources.count("raw.githubusercontent.com") == 0
    missing = interviewer.post("/api/problems/generate", json={**REF, "slug": "not-on-the-list"})
    assert missing.status_code == 404
    assert sources.count("ai-gateway.vercel.sh") == 0


def test_problem_endpoints_need_a_signed_in_interviewer(client, sources):
    assert client.get("/api/problems/companies").status_code == 401
    assert client.post("/api/problems/generate", json=REF).status_code == 401


def test_generate_returns_an_original_problem_and_caches_it(interviewer, sources):
    first = interviewer.post("/api/problems/generate", json={**REF, "language": "python"})
    assert first.status_code == 200, first.text
    problem = first.json()
    assert problem["name"] == "Pair Ledger" and problem["cached"] is False
    assert set(problem["files"]) == {"PROBLEM.md", "main.py"}
    assert problem["files"]["PROBLEM.md"].startswith("# Pair Ledger\n\nGiven a list")
    assert problem["files"]["PROBLEM.md"].count("# Pair Ledger") == 1
    assert problem["notes"]["complexity"] == "O(n) time, O(n) space."
    assert problem["source"]["title"] == "Two Sum"

    sent = json.loads(next(r for r in sources.requests if r.url.host == "ai-gateway.vercel.sh").content)
    assert sent["response_format"]["json_schema"]["name"] == "interview_problem"
    assert "title: Two Sum" in sent["messages"][1]["content"]
    assert "main.py for Python 3.12" in sent["messages"][1]["content"]

    again = interviewer.post("/api/problems/generate", json={**REF, "language": "python"}).json()
    assert again["cached"] is True and again["files"] == problem["files"]
    assert sources.count("ai-gateway.vercel.sh") == 1


def test_only_code_languages_get_company_problems(interviewer, sources):
    response = interviewer.post("/api/problems/generate", json={**REF, "language": "sql"})
    assert response.status_code == 422
    languages = {l["id"]: l["problems"] for l in interviewer.get("/api/languages").json()}
    assert languages["python"] and languages["go"] and not languages["web"] and not languages["prompt"]


def test_new_session_starts_from_the_problem_without_revealing_the_source(interviewer, sources):
    created = interviewer.post(
        "/api/sessions", json={"candidate_name": "Ada", "language": "python", "problem": REF}
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["title"] == "Pair Ledger"
    state = interviewer.get(f"/api/sessions/{session['id']}/state").json()
    files = {f["path"]: f["content"] for f in state["files"]}
    assert set(files) == {"PROBLEM.md", "main.py"}
    everything = " ".join(files.values()).lower()
    for secret in ("acme", "two sum", "two-sum", "leetcode", "hash map from value"):
        assert secret not in everything

    plain = interviewer.post("/api/sessions", json={"candidate_name": "Bea", "language": "python"}).json()
    plain_files = {f["path"] for f in interviewer.get(f"/api/sessions/{plain['id']}/state").json()["files"]}
    assert plain_files == {"main.py"}
    assert sources.count("ai-gateway.vercel.sh") == 1


def test_generation_failures_and_leaks_are_reported(interviewer, sources):
    sources.gateway_status = 402
    failed = interviewer.post("/api/sessions", json={"candidate_name": "Ada", "language": "python", "problem": REF})
    assert failed.status_code == 502 and "unavailable right now (402)" in failed.json()["detail"]

    sources.gateway_status = 200
    sources.generated = {**GENERATED, "name": "Two Sum"}
    leaked = interviewer.post("/api/problems/generate", json=REF)
    assert leaked.status_code == 502 and "too close to the original" in leaked.json()["detail"]
    retry = json.loads([r for r in sources.requests if r.url.host == "ai-gateway.vercel.sh"][-1].content)
    assert len(retry["messages"]) == 4 and retry["messages"][-1]["content"].startswith("Rejected: name matches")


def test_a_leaked_draft_is_retried_once_with_feedback(interviewer, sources):
    drafts = iter([{**GENERATED, "name": "Two Sum"}, GENERATED])
    original = sources.handle

    def handle(request):
        if request.url.host == "ai-gateway.vercel.sh":
            sources.generated = next(drafts)
        return original(request)

    interviewer.app.state.http_transport = httpx.MockTransport(handle)
    problem = interviewer.post("/api/problems/generate", json=REF)
    assert problem.status_code == 200 and problem.json()["name"] == "Pair Ledger"
    assert sources.count("ai-gateway.vercel.sh") == 2


def test_hourly_generation_limit(interviewer, sources, app):
    app.state.settings = app.state.settings.__class__(**{**app.state.settings.__dict__, "problem_generations_per_hour": 1})
    assert interviewer.post("/api/problems/generate", json=REF).status_code == 200
    assert interviewer.post("/api/problems/generate", json=REF).status_code == 200  # cached, not a new generation
    limited = interviewer.post("/api/problems/generate", json={**REF, "slug": "median-of-two-sorted-arrays"})
    assert limited.status_code == 429
    assert sources.count("ai-gateway.vercel.sh") == 1


def test_leak_checks():
    source = {"title": "Median of Two Sorted Arrays", "slug": "median-of-two-sorted-arrays"}
    clean = {"name": "Middle Value", "statement_md": "Find the middle of two sorted lists.", "stub": "def f(): pass"}
    assert leaks(clean, source) == []
    assert leaks({**clean, "statement_md": "Like median of two sorted arrays."}, source)
    assert leaks({**clean, "stub": "# see leetcode"}, source)
    one_word = {"title": "Subsets", "slug": "subsets"}
    assert leaks({**clean, "statement_md": "Return all subsets of the set."}, one_word) == []


def test_statements_that_show_the_models_working_are_rejected():
    source = {"title": "Ship Packages", "slug": "ship-packages"}
    messy = {**GENERATED, "statement_md": GENERATED["statement_md"] + "\n\nNo, this can't happen. Let's re-simulate:"}
    with pytest.raises(ProblemGenerationError, match="mistakes in its examples"):
        build(messy, source, LanguageId.python)
    story = {**GENERATED, "statement_md": GENERATED["statement_md"] + "\n\nCustomers must wait. Trucks wait, then leave."}
    assert build(story, source, LanguageId.python)["name"] == "Pair Ledger"


def test_build_rejects_starters_that_would_not_run():
    source = {"title": "Two Sum", "slug": "two-sum"}
    java = {**GENERATED, "stub": "static int[] findPair(int[] a, int t) { return new int[0]; }"}
    with pytest.raises(ProblemGenerationError, match="won't run"):
        build(java, source, LanguageId.java)
    with pytest.raises(ProblemGenerationError, match="incomplete"):
        build({**GENERATED, "statement_md": "short"}, source, LanguageId.python)
