"""Run session code in Vercel Sandbox microVMs (SANDBOX_BACKEND=sandbox).

One persistent sandbox per session, named `wmc-<workspace>-<session>`: files are synced in before each
run, toolchains are installed the first time a language is used, and installs stay cached for the rest
of the session. Browser labs (web, React, whiteboard) and the SQL lab keep using the local runner.
"""

from __future__ import annotations

import logging
import shlex
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any

from app.config import Settings
from app.languages import LanguageId, language_for_path
from app.runner import CodeRunner, RunResult, detect_js_packages, detect_python_packages
from app.tenancy import current_tenant

logger = logging.getLogger(__name__)

ROOT = "/vercel/sandbox"
WORKDIR = f"{ROOT}/work"
MARKERS = f"{ROOT}/.wmc"
LOCAL_LANGUAGES = {LanguageId.web, LanguageId.react, LanguageId.whiteboard, LanguageId.sql, LanguageId.prompt}
PRESERVED = ("node_modules", ".build", ".wmc")
TOOLCHAINS: dict[LanguageId, tuple[str, ...]] = {
    LanguageId.java: ("default-jdk-headless",),
    LanguageId.csharp: ("dotnet-sdk-10.0",),
    LanguageId.cpp: ("g++",),
    LanguageId.c: ("gcc",),
    LanguageId.go: ("golang-go",),
    LanguageId.php: ("php-cli",),
}

SandboxFactory = Callable[[str], Any]


def sandbox_name(session_id: str) -> str:
    tenant = current_tenant.get()
    workspace = tenant["slug"] if tenant else "app"
    return f"wmc-{workspace}-{session_id}"[:63]


def run_script(language: LanguageId, entry: str, files: dict[str, str]) -> tuple[str, list[str]]:
    """The bash script that runs `entry` inside the sandbox, plus the notes shown in the console."""
    q = shlex.quote
    notes: list[str] = []
    sources = lambda *exts: " ".join(q(p) for p in files if p.endswith(exts))  # noqa: E731
    if language == LanguageId.python:
        packages = detect_python_packages(files)
        script = ""
        if packages:
            notes.append(f"Installing {', '.join(packages)}…")
            script += (
                "python3 -m pip install --user --break-system-packages --quiet --disable-pip-version-check "
                f"{' '.join(q(p) for p in packages)} >/dev/null 2>&1 || true\n"
            )
        return script + f"exec python3 {q(entry)}", notes
    if language in (LanguageId.javascript, LanguageId.typescript):
        packages = detect_js_packages(files)
        script = "[ -f package.json ] || echo '{\"private\":true}' > package.json\n"
        if packages:
            notes.append(f"Installing {', '.join(packages)}…")
            script += f"npm install --no-save --silent {' '.join(q(p) for p in packages)} >/dev/null 2>&1\n"
        runner = "npx --yes tsx" if language == LanguageId.typescript else "node"
        return script + f"exec {runner} {q(entry)}", notes
    if language == LanguageId.java:
        main_class = PurePosixPath(entry).stem
        return f"mkdir -p .build/java && javac -d .build/java {sources('.java')} && exec java -cp .build/java {q(main_class)}", notes
    if language == LanguageId.csharp:
        return f"export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1\nexec dotnet run {q(entry)}", notes
    if language == LanguageId.cpp:
        return f"mkdir -p .build && g++ -std=c++20 -O1 -o .build/app {sources('.cpp', '.cc', '.cxx')} && exec ./.build/app", notes
    if language == LanguageId.c:
        return f"mkdir -p .build && gcc -std=c17 -O1 -o .build/app {sources('.c')} -lm && exec ./.build/app", notes
    if language == LanguageId.go:
        return "[ -f go.mod ] || go mod init session >/dev/null 2>&1\ngo mod tidy >/dev/null 2>&1\nexec go run .", notes
    if language == LanguageId.php:
        return f"exec php {q(entry)}", notes
    if language == LanguageId.shell:
        return f"exec bash {q(entry)}", notes
    raise ValueError(f"No sandbox recipe for {language}")


def cleanup_script(files: dict[str, str]) -> str:
    """Delete files the session no longer has, keeping caches and build output."""
    keep = " ".join(f"-not -path './{p}/*'" for p in PRESERVED)
    return (
        f"cd {WORKDIR} && find . -type f {keep} | sed 's|^\\./||' | grep -vxF -f {MARKERS}/files.txt "
        "| while IFS= read -r f; do rm -f -- \"$f\"; done; find . -type d -empty -delete 2>/dev/null; true"
    )


class SandboxRunner:
    def __init__(self, settings: Settings, local: CodeRunner, factory: SandboxFactory | None = None) -> None:
        self.settings = settings
        self.local = local
        self.factory = factory or self._vercel_sandbox

    def _vercel_sandbox(self, name: str) -> Any:
        from vercel.sandbox import SandboxResources
        from vercel.sandbox.sync import get_or_create_sandbox

        sandbox, created = get_or_create_sandbox(
            name=name,
            persistent=True,
            resources=SandboxResources(vcpus=2),
            execution_time_limit=timedelta(minutes=self.settings.sandbox_session_minutes),
            snapshot_expiration=timedelta(days=7),
            tags={"app": "watchmecode"},
        )
        if created:
            logger.info("Created sandbox %s", name)
        return sandbox

    def _sync_files(self, box: Any, files: dict[str, str]) -> None:
        dirs = sorted({str(PurePosixPath(WORKDIR, p).parent) for p in files} | {WORKDIR, MARKERS})
        box.run_command("mkdir", ["-p", *dirs])
        for path, content in files.items():
            box.fs.write_text(f"{WORKDIR}/{path}", content)
        box.fs.write_text(f"{MARKERS}/files.txt", "\n".join(files) + "\n")
        box.run_command("bash", ["-c", cleanup_script(files)])

    def _ensure_toolchain(self, box: Any, language: LanguageId) -> str | None:
        packages = TOOLCHAINS.get(language)
        if not packages:
            return None
        marker = f"{MARKERS}/{language.value}.ok"
        check = box.run_command("test", ["-f", marker])
        if check.returncode == 0:
            return None
        install = box.run_command(
            "bash",
            ["-c", f"apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {' '.join(packages)} && touch {marker}"],
            sudo=True,
            capture_output=True,
            kill_after=self.settings.install_timeout_seconds * 3,
        )
        if install.returncode != 0:
            raise RuntimeError(f"Installing {' '.join(packages)} failed: {(install.stderr or '')[-400:]}")
        return f"Installed {' '.join(packages)} (first run in this session)."

    def run(self, session_id: str, entry_path: str, files: dict[str, str]) -> RunResult:
        language = language_for_path(entry_path)
        if entry_path not in files or language is None or language.id in LOCAL_LANGUAGES:
            return self.local.run(session_id, entry_path, files)
        started = time.perf_counter()
        name = sandbox_name(session_id)
        try:
            box = self.factory(name)
            self._sync_files(box, files)
            installed = self._ensure_toolchain(box, language.id)
            script, notes = run_script(language.id, entry_path, files)
            if installed:
                notes.insert(0, installed)
            result = box.run_command(
                "bash",
                ["-lc", script],
                cwd=WORKDIR,
                capture_output=True,
                kill_after=self.settings.run_timeout_seconds + self.settings.install_timeout_seconds,
            )
        except Exception as exc:  # the SDK raises several error types; fall back so the interview continues
            logger.warning("Sandbox run failed for %s (%s); using the local runner", name, exc)
            fallback = self.local.run(session_id, entry_path, files)
            fallback.stderr = f"[Sandbox unavailable, ran on the shared runner instead.]\n{fallback.stderr}"
            return fallback
        cap = self.settings.max_output_bytes
        stdout, stderr = (result.stdout or "")[:cap], (result.stderr or "")[:cap]
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("Sandbox %s ran %s -> exit %s (%d ms)", name, entry_path, result.returncode, duration_ms)
        return RunResult(
            entry_path=entry_path,
            command=f"sandbox {name}: {script.splitlines()[-1]}",
            stdout=stdout,
            stderr="".join(f"[{note}]\n" for note in notes) + stderr,
            exit_code=result.returncode,
            duration_ms=duration_ms,
        )

    def release(self, session_id: str) -> None:
        """Stop the session's sandbox (its filesystem snapshot is kept for review)."""
        try:
            from vercel.sandbox.sync import get_sandbox

            get_sandbox(name=sandbox_name(session_id)).stop()
        except Exception as exc:
            logger.info("No sandbox to stop for %s (%s)", session_id, exc)
