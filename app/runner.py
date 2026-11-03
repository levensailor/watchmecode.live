"""Execute session files in a per-session temp directory with timeouts and output caps."""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.languages import LanguageId, language_for_path

logger = logging.getLogger(__name__)

PRESERVED_ENTRIES = {"node_modules", ".build", "go.sum"}
ENV_PASSTHROUGH = (
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "NVM_DIR", "JAVA_HOME",
    "DOTNET_ROOT", "GOPATH", "GOROOT", "GOCACHE", "GOMODCACHE", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
    "npm_config_cache", "SSL_CERT_FILE",
)
PYTHON_IMPORT_TO_PACKAGE = {
    "attr": "attrs", "bs4": "beautifulsoup4", "cv2": "opencv-python", "Crypto": "pycryptodome",
    "dateutil": "python-dateutil", "docx": "python-docx", "dotenv": "python-dotenv", "fitz": "pymupdf",
    "jwt": "pyjwt", "magic": "python-magic", "OpenSSL": "pyopenssl", "PIL": "pillow", "pptx": "python-pptx",
    "serial": "pyserial", "skimage": "scikit-image", "sklearn": "scikit-learn", "yaml": "pyyaml",
    "google": "google-api-python-client", "Levenshtein": "python-Levenshtein",
}
JS_IMPORT_PATTERNS = (
    re.compile(r"""\bimport\s+(?:[\w*{}\s,$]+\s+from\s+)?['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bexport\s+[\w*{}\s,$]+\s+from\s+['"]([^'"]+)['"]"""),
)
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto",
    "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector",
    "module", "net", "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl",
    "stream", "string_decoder", "sys", "test", "timers", "tls", "trace_events", "tty", "url", "util", "v8",
    "vm", "wasi", "worker_threads", "zlib",
}
SAFE_PACKAGE = re.compile(r"^(@[a-z0-9][\w.\-]*/)?[a-zA-Z0-9][\w.\-]*$")


class ToolchainMissing(Exception):
    pass


@dataclass
class Step:
    argv: list[str]
    timeout: int
    label: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    entry_path: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def uv_binary() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    try:
        from uv import find_uv_bin

        return find_uv_bin()
    except Exception:
        return None


def detect_python_packages(sources: dict[str, str]) -> list[str]:
    local = {Path(p).stem for p in sources if p.endswith(".py")} | {p.split("/")[0] for p in sources if "/" in p}
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    packages: set[str] = set()
    for path, code in sources.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in stdlib or top in local:
                    continue
                packages.add(PYTHON_IMPORT_TO_PACKAGE.get(top, top))
    return sorted(p for p in packages if SAFE_PACKAGE.match(p))


def _normalize_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def missing_pip_packages(target: Path, packages: list[str]) -> list[str]:
    installed = {_normalize_dist(p.name.split("-")[0]) for p in target.glob("*.dist-info")} if target.exists() else set()
    return [pkg for pkg in packages if _normalize_dist(pkg) not in installed]


def detect_js_packages(sources: dict[str, str]) -> list[str]:
    packages: set[str] = set()
    for path, code in sources.items():
        if not path.endswith((".js", ".mjs", ".cjs", ".ts", ".mts", ".tsx", ".jsx")):
            continue
        for pattern in JS_IMPORT_PATTERNS:
            for spec in pattern.findall(code):
                if spec.startswith((".", "/", "node:", "http:", "https:")):
                    continue
                parts = spec.split("/")
                name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
                if name in NODE_BUILTINS:
                    continue
                packages.add(name)
    return sorted(p for p in packages if SAFE_PACKAGE.match(p))


class CodeRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = Path(settings.runs_dir)
        self.tools = self.base / "_tools"

    # Workspace ---------------------------------------------------------------

    def workspace(self, session_id: str) -> Path:
        return self.base / session_id

    def sync_files(self, workdir: Path, files: dict[str, str]) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        wanted = {Path(p) for p in files}
        wanted_dirs = {parent for p in wanted for parent in p.parents}
        for item in sorted(workdir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            rel = item.relative_to(workdir)
            if rel.parts[0] in PRESERVED_ENTRIES or rel.parts[0] == "package-lock.json":
                continue
            if item.is_dir():
                if rel not in wanted_dirs:
                    shutil.rmtree(item, ignore_errors=True)
            elif rel not in wanted:
                item.unlink(missing_ok=True)
        for path, content in files.items():
            target = (workdir / path).resolve()
            if workdir.resolve() not in target.parents:
                raise ValueError(f"Refusing to write outside the workspace: {path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def child_env(self, workdir: Path) -> dict[str, str]:
        env = {key: os.environ[key] for key in ENV_PASSTHROUGH if key in os.environ}
        env.setdefault("PATH", os.defpath)
        env["PYTHONUNBUFFERED"] = "1"
        env["NO_COLOR"] = "1"
        env["npm_config_update_notifier"] = "false"
        env["npm_config_fund"] = "false"
        env["npm_config_audit"] = "false"
        if self.settings.is_vercel or not os.access(env.get("HOME", "/"), os.W_OK):
            cache = Path(tempfile.gettempdir()) / "watchmecode-cache"
            env["HOME"] = str(cache / "home")
            env["UV_CACHE_DIR"] = str(cache / "uv")
            env["npm_config_cache"] = str(cache / "npm")
            env["GOPATH"] = str(cache / "go")
            env["GOCACHE"] = str(cache / "go-build")
            env["DOTNET_CLI_HOME"] = str(cache / "dotnet")
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        return env

    # Planning ----------------------------------------------------------------

    def plan(self, entry_path: str, files: dict[str, str], workdir: Path) -> Plan:
        language = language_for_path(entry_path)
        if language is None:
            raise ToolchainMissing(f"Don't know how to run '{entry_path}'. Pick a file with a supported extension.")
        timeout = self.settings.run_timeout_seconds
        install_timeout = self.settings.install_timeout_seconds
        plan = Plan()
        lang = language.id

        if lang == LanguageId.python:
            uv = uv_binary()
            packages = detect_python_packages(files)
            has_inline_metadata = "# /// script" in files.get(entry_path, "")
            if uv:
                argv = [uv, "run", "--no-project", "--quiet", "--python", sys.executable]
                for pkg in packages:
                    argv += ["--with", pkg]
                argv.append(entry_path)
                if packages:
                    plan.notes.append(f"uv: auto-installing {', '.join(packages)}")
                elif has_inline_metadata:
                    plan.notes.append("uv: using PEP 723 inline metadata")
                plan.steps.append(Step(argv, timeout + (install_timeout if packages or has_inline_metadata else 0)))
            elif packages:
                deps = self.tools / "pydeps"
                missing = missing_pip_packages(deps, packages)
                if missing:
                    plan.notes.append(f"pip: auto-installing {', '.join(missing)} (uv unavailable)")
                    plan.steps.append(
                        Step(
                            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                             "--target", str(deps), *missing],
                            install_timeout,
                            "install",
                        )
                    )
                plan.steps.append(Step([sys.executable, entry_path], timeout, env={"PYTHONPATH": str(deps)}))
            else:
                plan.steps.append(Step([sys.executable, entry_path], timeout))

        elif lang in (LanguageId.javascript, LanguageId.typescript):
            if not _which("node") and not self._nvm_script():
                raise ToolchainMissing("Node.js is not installed on this server, so JavaScript/TypeScript cannot run.")
            packages = [p for p in detect_js_packages(files) if not (workdir / "node_modules" / p).exists()]
            if packages:
                if "package.json" not in files and not (workdir / "package.json").exists():
                    (workdir / "package.json").write_text(json.dumps({"private": True}))
                plan.notes.append(f"npm: installing {', '.join(packages)}")
                plan.steps.append(Step(self._node(["npm", "install", "--no-save", "--silent", *packages]), install_timeout, "install"))
            if lang == LanguageId.typescript:
                tsx = self._ensure_tsx(plan, install_timeout)
                plan.steps.append(Step(self._node([tsx, entry_path]), timeout))
            else:
                plan.steps.append(Step(self._node(["node", entry_path]), timeout))

        elif lang == LanguageId.java:
            javac, java = self._require("javac", "Java (JDK)"), self._require("java", "Java (JDK)")
            sources = [p for p in files if p.endswith(".java")]
            plan.steps.append(Step([javac, "-d", ".build/java", *sources], timeout, "compile"))
            main_class = Path(entry_path).stem
            plan.steps.append(Step([java, "-cp", ".build/java", main_class], timeout))

        elif lang == LanguageId.csharp:
            dotnet = _which("dotnet")
            if dotnet:
                plan.steps.append(Step([dotnet, "run", entry_path], timeout + install_timeout))
            else:
                compiler = _which("mcs", "csc")
                mono = _which("mono")
                if not (compiler and mono):
                    raise ToolchainMissing("C# needs the .NET SDK (dotnet) or Mono, and neither is installed on this server.")
                sources = [p for p in files if p.endswith(".cs")]
                plan.steps.append(Step([compiler, "-out:.build/app.exe", *sources], timeout, "compile"))
                plan.steps.append(Step([mono, ".build/app.exe"], timeout))

        elif lang in (LanguageId.cpp, LanguageId.c):
            if lang == LanguageId.cpp:
                compiler = self._require_any(("g++", "clang++"), "a C++ compiler (g++ or clang++)")
                sources = [p for p in files if p.endswith((".cpp", ".cc", ".cxx"))]
                flags, libs = ["-std=c++20", "-O1"], []
            else:
                compiler = self._require_any(("gcc", "clang", "cc"), "a C compiler (gcc or clang)")
                sources = [p for p in files if p.endswith(".c")]
                flags, libs = ["-std=c17", "-O1"], ["-lm"]
            (workdir / ".build").mkdir(exist_ok=True)
            plan.steps.append(Step([compiler, *flags, "-o", ".build/app", *sources, *libs], timeout, "compile"))
            plan.steps.append(Step(["./.build/app"], timeout))

        elif lang == LanguageId.go:
            go = self._require("go", "Go")
            if "go.mod" not in files:
                (workdir / "go.mod").write_text("module session\n\ngo 1.21\n")
            plan.steps.append(Step([go, "mod", "tidy"], install_timeout, "install"))
            plan.steps.append(Step([go, "run", "."], timeout + 30))

        elif lang == LanguageId.php:
            plan.steps.append(Step([self._require("php", "PHP"), entry_path], timeout))

        elif lang == LanguageId.shell:
            plan.steps.append(Step([self._require_any(("bash", "sh"), "a POSIX shell"), entry_path], timeout))

        elif lang == LanguageId.web:
            plan.notes.append("HTML/CSS render in the Preview pane.")

        return plan

    def _require(self, binary: str, label: str) -> str:
        return self._require_any((binary,), label)

    @staticmethod
    def _require_any(binaries: tuple[str, ...], label: str) -> str:
        found = _which(*binaries)
        if not found:
            raise ToolchainMissing(f"{label} is not installed on this server, so this language cannot run here.")
        return found

    def _nvm_script(self) -> Path | None:
        nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
        script = nvm_dir / "nvm.sh"
        return script if script.exists() else None

    def _node(self, argv: list[str]) -> list[str]:
        """Wrap a Node command with `nvm use` when nvm is installed."""
        script = self._nvm_script()
        if not script:
            return argv
        wrapper = 'source "$1" >/dev/null 2>&1 && nvm use --silent "$2" >/dev/null 2>&1; shift 2; exec "$@"'
        return ["bash", "-c", wrapper, "nvm", str(script), self.settings.node_version, *argv]

    def _ensure_tsx(self, plan: Plan, install_timeout: int) -> str:
        found = _which("tsx")
        if found:
            return found
        local = self.tools / "node_modules" / ".bin" / "tsx"
        if not local.exists():
            self.tools.mkdir(parents=True, exist_ok=True)
            plan.notes.append("npm: installing the tsx TypeScript runner (first run only)")
            plan.steps.append(
                Step(self._node(["npm", "install", "--prefix", str(self.tools), "--silent", "tsx"]), install_timeout, "install")
            )
        return str(local)

    # Execution ---------------------------------------------------------------

    def _execute(self, step: Step, workdir: Path, env: dict[str, str]) -> tuple[int, str, str, bool]:
        cap = self.settings.max_output_bytes
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(
                step.argv,
                cwd=workdir,
                env={**env, **step.env},
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            timed_out = False
            try:
                proc.wait(timeout=step.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            out.seek(0)
            err.seek(0)
            stdout = self._read_capped(out, cap)
            stderr = self._read_capped(err, cap)
        return proc.returncode, stdout, stderr, timed_out

    @staticmethod
    def _scrub(text: str, workdir: Path) -> str:
        """Show workspace-relative paths instead of server temp directories."""
        for prefix in {str(workdir.resolve()), str(workdir)}:
            text = text.replace(prefix + os.sep, "").replace(prefix, ".")
        return text

    @staticmethod
    def _read_capped(handle, cap: int) -> str:
        data = handle.read(cap + 1)
        text = data[:cap].decode("utf-8", errors="replace")
        if len(data) > cap:
            text += f"\n… output truncated at {cap} bytes"
        return text

    def run(self, session_id: str, entry_path: str, files: dict[str, str]) -> RunResult:
        started = time.perf_counter()
        workdir = self.workspace(session_id)
        if entry_path not in files:
            return RunResult(entry_path, "", "", f"File not found: {entry_path}", 1, 0)
        self.sync_files(workdir, files)
        try:
            plan = self.plan(entry_path, files, workdir)
        except ToolchainMissing as exc:
            logger.info("Toolchain missing for %s: %s", entry_path, exc)
            return RunResult(entry_path, "", "", str(exc), 127, int((time.perf_counter() - started) * 1000))

        env = self.child_env(workdir)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = [f"[{note}]\n" for note in plan.notes]
        commands: list[str] = []
        exit_code = 0
        for step in plan.steps:
            commands.append(shlex.join(step.argv))
            try:
                code, out, err, timed_out = self._execute(step, workdir, env)
            except FileNotFoundError as exc:
                stderr_parts.append(f"Command not found: {exc.filename}\n")
                exit_code = 127
                break
            stdout_parts.append(out)
            stderr_parts.append(err)
            if timed_out:
                stderr_parts.append(f"\nTimed out after {step.timeout}s{f' during {step.label}' if step.label else ''}.\n")
                exit_code = 124
                break
            if code != 0:
                exit_code = code
                break

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("Ran %s in session %s -> exit %s (%d ms)", entry_path, session_id, exit_code, duration_ms)
        return RunResult(
            entry_path=entry_path,
            command=" && ".join(commands),
            stdout=self._scrub("".join(stdout_parts), workdir),
            stderr=self._scrub("".join(stderr_parts), workdir),
            exit_code=exit_code,
            duration_ms=duration_ms,
        )
