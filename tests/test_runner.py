import shutil

import pytest

from app.runner import CodeRunner, detect_js_packages, detect_python_packages, uv_binary

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
needs_uv = pytest.mark.skipif(uv_binary() is None, reason="uv is not available")


def run(client, sid, path, content):
    client.put(f"/api/sessions/{sid}/files", json={"path": path, "content": content})
    response = client.post(f"/api/sessions/{sid}/run", json={"entry_path": path})
    assert response.status_code == 200
    return response.json()


def test_detect_python_packages_skips_stdlib_and_local_modules():
    sources = {
        "main.py": "import os, sys\nimport requests\nfrom yaml import safe_load\nimport helpers\nfrom . import sibling\n",
        "helpers.py": "import numpy as np\n",
    }
    assert detect_python_packages(sources) == ["numpy", "pyyaml", "requests"]


def test_detect_js_packages_skips_builtins_and_relative_imports():
    sources = {
        "index.js": "import fs from 'fs';\nimport _ from 'lodash';\nconst x = require('./x');\nconst y = require('node:path');\nimport { z } from '@scope/pkg/sub';\n",
    }
    assert detect_js_packages(sources) == ["@scope/pkg", "lodash"]


def test_python_plan_uses_uv_with_detected_packages(settings, tmp_path):
    if uv_binary() is None:
        pytest.skip("uv is not available")
    runner = CodeRunner(settings)
    plan = runner.plan("main.py", {"main.py": "import requests\n"}, tmp_path)
    argv = plan.steps[0].argv
    assert argv[1] == "run"
    assert ["--with", "requests"] == argv[argv.index("--with") : argv.index("--with") + 2]
    assert argv[-1] == "main.py"


def test_run_python_snippet(candidate, created_session):
    result = run(candidate, created_session["id"], "hello.py", "print(sum(range(5)))\n")
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "10"
    assert result["duration_ms"] >= 0


def test_run_reports_errors_with_relative_paths(candidate, created_session):
    result = run(candidate, created_session["id"], "boom.py", "raise SystemExit('nope')\n")
    assert result["exit_code"] == 1
    assert "nope" in result["stderr"]
    assert "watchmecode-runs" not in result["stderr"]


@needs_uv
def test_run_python_auto_installs_requests(candidate, created_session):
    result = run(candidate, created_session["id"], "net.py", "import requests\nprint(requests.__name__)\n")
    assert result["exit_code"] == 0, result["stderr"]
    assert result["stdout"].strip() == "requests"


@needs_node
def test_run_javascript_snippet(candidate, created_session):
    result = run(candidate, created_session["id"], "index.js", "console.log([1, 2, 3].map((n) => n * 2).join(','));\n")
    assert result["exit_code"] == 0, result["stderr"]
    assert result["stdout"].strip() == "2,4,6"


def test_run_timeout(settings, tmp_path):
    from dataclasses import replace

    runner = CodeRunner(replace(settings, run_timeout_seconds=1))
    result = runner.run("timeout-session", "loop.sh", {"loop.sh": "while true; do :; done\n"})
    assert result.exit_code == 124
    assert "Timed out" in result.stderr


def test_output_is_capped(settings):
    from dataclasses import replace

    runner = CodeRunner(replace(settings, max_output_bytes=100))
    result = runner.run("cap-session", "spam.py", {"spam.py": "print('x' * 10000)\n"})
    assert "truncated" in result.stdout
    assert len(result.stdout) < 200


def test_missing_toolchain_returns_clear_error(settings, monkeypatch):
    monkeypatch.setattr("app.runner._which", lambda *names: None)
    runner = CodeRunner(settings)
    result = runner.run("missing-session", "index.php", {"index.php": "<?php echo 1;"})
    assert result.exit_code == 127
    assert "PHP is not installed" in result.stderr


def test_unknown_extension(settings):
    result = CodeRunner(settings).run("x", "notes.txt", {"notes.txt": "hi"})
    assert result.exit_code == 127
    assert "supported extension" in result.stderr


def test_run_is_recorded_and_synced(interviewer, candidate, created_session):
    sid = created_session["id"]
    run(candidate, sid, "main.py", "print('synced')\n")
    detail = interviewer.get(f"/api/sessions/{sid}").json()
    assert detail["runs"][0]["stdout"].strip() == "synced"
    events = interviewer.get(f"/api/sessions/{sid}/changes", params={"since": 0}).json()["events"]
    assert events[-1]["kind"] == "run"
    assert events[-1]["data"]["stdout"].strip() == "synced"
