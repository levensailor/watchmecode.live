from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.languages import LANGUAGES, LanguageId
from app.runner import CodeRunner
from app.sandbox_runner import WORKDIR, SandboxRunner
from tests.conftest import INTERVIEWER, tenant_client


def create(client, language):
    response = client.post("/api/sessions", json={"candidate_name": "Ada", "language": language})
    assert response.status_code == 201, response.text
    return response.json()


def test_sql_lab_loads_csv_tables_and_prints_results(interviewer):
    session = create(interviewer, "sql")
    detail = interviewer.get(f"/api/sessions/{session['id']}").json()
    assert {f["path"] for f in detail["files"]} == {"query.sql", "employees.csv", "departments.csv", "README.md"}
    run = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "query.sql"}).json()
    assert run["exit_code"] == 0, run["stderr"]
    assert "employees(id integer, name text, department_id integer, salary integer, hired_on text) · 8 rows" in run["stdout"]
    assert "| Platform   | 3         | 182333.0   |" in run["stdout"]


def test_sql_lab_reports_errors(interviewer):
    session = create(interviewer, "sql")
    interviewer.put(f"/api/sessions/{session['id']}/files", json={"path": "query.sql", "content": "SELECT * FROM nope;"})
    run = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "query.sql"}).json()
    assert run["exit_code"] == 1
    assert "no such table: nope" in run["stderr"]


def test_react_and_whiteboard_labs_have_starters(interviewer):
    react = interviewer.get(f"/api/sessions/{create(interviewer, 'react')['id']}").json()
    index = next(f["content"] for f in react["files"] if f["path"] == "index.html")
    assert '"react": "https://esm.sh/react@' in index and 'type="module" src="app.js"' in index

    board = create(interviewer, "whiteboard")
    run = interviewer.post(f"/api/sessions/{board['id']}/run", json={"entry_path": "board.excalidraw"}).json()
    assert run["exit_code"] == 0
    assert "nothing to run" in run["stderr"]


def test_labs_require_a_plan_with_labs(multi_app, multi_settings):
    registry = multi_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    assert owner.post("/api/sessions", json={"candidate_name": "Ada", "language": "sql"}).status_code == 201
    registry.platform.update_tenant(tenant["id"], plan="starter", status="active")
    registry.invalidate("acme")
    blocked = owner.post("/api/sessions", json={"candidate_name": "Ada", "language": "sql"})
    assert blocked.status_code == 402
    assert "Team plan" in blocked.json()["detail"]
    assert owner.post("/api/sessions", json={"candidate_name": "Ada", "language": "python"}).status_code == 201


# Sandbox runner -----------------------------------------------------------------------


@dataclass
class Completed:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeFs:
    written: dict[str, str] = field(default_factory=dict)

    def write_text(self, path, text):
        self.written[path] = text


@dataclass
class FakeBox:
    toolchains_installed: set[str] = field(default_factory=set)
    commands: list[tuple] = field(default_factory=list)
    fs: FakeFs = field(default_factory=FakeFs)

    def run_command(self, command, args=None, **kwargs):
        self.commands.append((command, tuple(args or ()), kwargs))
        if command == "test":
            return Completed(0 if args[1].rsplit("/", 1)[-1] in self.toolchains_installed else 1)
        if kwargs.get("sudo"):
            self.toolchains_installed.add(args[1].rsplit(" ", 1)[-1].rsplit("/", 1)[-1])
            return Completed()
        if command == "bash" and args and args[0] == "-lc":
            return Completed(stdout="hello\n")
        return Completed()


def sandbox_runner(settings, box=None, fail=False):
    names = []

    def factory(name):
        names.append(name)
        if fail:
            raise RuntimeError("no credentials")
        return box

    return SandboxRunner(replace(settings, runs_dir=settings.runs_dir), CodeRunner(settings), factory), names


def test_sandbox_runner_syncs_files_and_runs_in_the_vm(settings):
    box = FakeBox()
    runner, names = sandbox_runner(settings, box)
    result = runner.run("s1", "main.py", {"main.py": "import requests\nprint(1)\n", "lib/util.py": "X = 1\n"})
    assert names == ["wmc-app-s1"]
    assert result.exit_code == 0 and result.stdout == "hello\n"
    assert "[Installing requests…]" in result.stderr
    assert box.fs.written[f"{WORKDIR}/main.py"].startswith("import requests")
    assert f"{WORKDIR}/lib/util.py" in box.fs.written
    script = next(args[1] for cmd, args, _ in box.commands if cmd == "bash" and args[0] == "-lc")
    assert "pip install" in script and script.endswith("exec python3 main.py")


def test_sandbox_runner_installs_toolchains_once(settings):
    box = FakeBox()
    runner, _ = sandbox_runner(settings, box)
    go = LANGUAGES[LanguageId.go]
    first = runner.run("s1", go.entry, dict(go.starter))
    second = runner.run("s1", go.entry, dict(go.starter))
    installs = [c for c in box.commands if c[2].get("sudo")]
    assert len(installs) == 1 and "golang-go" in installs[0][1][1]
    assert "[Installed golang-go" in first.stderr and "Installed" not in second.stderr


def test_sandbox_runner_keeps_browser_labs_and_sql_local(settings):
    box = FakeBox()
    runner, names = sandbox_runner(settings, box)
    sql = LANGUAGES[LanguageId.sql]
    result = runner.run("s1", sql.entry, dict(sql.starter))
    assert result.exit_code == 0 and "Platform" in result.stdout
    assert names == []


def test_sandbox_runner_falls_back_to_local(settings):
    runner, _ = sandbox_runner(settings, fail=True)
    result = runner.run("s1", "main.py", {"main.py": "print('local')\n"})
    assert result.exit_code == 0 and result.stdout.strip() == "local"
    assert "Sandbox unavailable" in result.stderr
