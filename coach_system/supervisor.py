"""Safe local multi-agent supervisor around ``codex exec``.

Real Codex calls are never made in ``--dry-run`` mode. The module intentionally
uses only the Python standard library so it does not alter the TikTok runtime.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "config.json"
DEFAULT_STATE_PATH = PACKAGE_DIR / "state.json"
LOCK_PATH = PACKAGE_DIR / ".supervisor.lock"
STOP_PATH = PACKAGE_DIR / "STOP_REQUESTED"
SCHEMA_DIR = PACKAGE_DIR / "schemas"
PROMPT_DIR = PACKAGE_DIR / "prompts"
LOGS_DIR = PACKAGE_DIR / "logs"

LOGGER = logging.getLogger("coach_supervisor")
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class SupervisorError(RuntimeError):
    """Base error for safe, expected supervisor failures."""


class PreconditionError(SupervisorError):
    """A required executable, login, or repository condition is missing."""


class ValidationError(SupervisorError):
    """An agent returned invalid or disallowed structured output."""


class AgentExecutionError(SupervisorError):
    """A Codex agent failed, timed out, or did not produce its output file."""


class AlreadyRunningError(SupervisorError):
    """The anti-double-instance lock already exists."""


@dataclass(frozen=True)
class TestCommand:
    name: str
    argv: list[str]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot load valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"Expected a JSON object in {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


_SECRET_ASSIGNMENT = re.compile(
    r"(?im)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY)[A-Z0-9_]*)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def redact_text(value: str) -> str:
    """Best-effort defense in depth; agents are also forbidden to read secrets."""
    value = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    return _BEARER.sub("Bearer [REDACTED]", value)


def safe_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact_text(value), encoding="utf-8")


def normalize_process_output(
    result: subprocess.CompletedProcess[str],
) -> tuple[str, str]:
    """Return safe text streams and normalize mocked/missing subprocess output."""
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""
    result.stdout = stdout_text
    result.stderr = stderr_text
    return stdout_text, stderr_text


def run_checked(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    runner: ProcessRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentExecutionError(f"Command timed out after {timeout}s: {argv[0]}") from exc
    except OSError as exc:
        raise AgentExecutionError(f"Cannot execute {argv[0]}: {exc}") from exc
    normalize_process_output(result)
    return result


def verify_preconditions(
    repo_root: Path,
    config: dict[str, Any],
    *,
    runner: ProcessRunner = subprocess.run,
    require_codex: bool = True,
) -> None:
    if require_codex:
        codex = shutil.which("codex")
        if not codex:
            raise PreconditionError("codex command not found on PATH")
        login = run_checked([codex, "login", "status"], cwd=repo_root, timeout=30, runner=runner)
        if login.returncode != 0:
            raise PreconditionError("Codex is not logged in; run 'codex login' interactively")

    git = shutil.which("git")
    if not git:
        raise PreconditionError("git command not found on PATH")
    inside = run_checked(
        [git, "rev-parse", "--is-inside-work-tree"], cwd=repo_root, timeout=30, runner=runner
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise PreconditionError(f"Not a Git work tree: {repo_root}")
    if config.get("require_clean_git_start", True):
        status = run_checked([git, "status", "--porcelain"], cwd=repo_root, timeout=30, runner=runner)
        if status.returncode != 0:
            raise PreconditionError("Unable to inspect Git working tree")
        dirty_lines = [
            line for line in status.stdout.splitlines()
            if not line.rstrip().replace("\\", "/").endswith("coach_system/state.json")
        ]
        if dirty_lines:
            raise PreconditionError("Git working tree must be clean before a real run")


class InstanceLock:
    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AlreadyRunningError(
                f"Supervisor lock exists at {self.path}; another instance may be running"
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": utc_now()}))
        self.acquired = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


def validate_agent_payload(payload: Any, schema_name: str) -> dict[str, Any]:
    """Validate the strict subset used by the three checked-in JSON schemas."""
    if not isinstance(payload, dict):
        raise ValidationError(f"{schema_name} output must be a JSON object")
    schema = load_json(SCHEMA_DIR / schema_name)
    properties = schema["properties"]
    required = set(schema["required"])
    keys = set(payload)
    if missing := required - keys:
        raise ValidationError(f"{schema_name} missing fields: {sorted(missing)}")
    if schema.get("additionalProperties") is False and (extra := keys - set(properties)):
        raise ValidationError(f"{schema_name} unexpected fields: {sorted(extra)}")

    for key, rules in properties.items():
        if key not in payload:
            continue
        value = payload[key]
        expected = rules.get("type")
        valid_type = {
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
        }.get(expected, True)
        if not valid_type:
            raise ValidationError(f"{schema_name}.{key} has invalid type")
        if "enum" in rules and value not in rules["enum"]:
            raise ValidationError(f"{schema_name}.{key} has disallowed value: {value}")
        if isinstance(value, str):
            if len(value) < rules.get("minLength", 0) or len(value) > rules.get("maxLength", 10**9):
                raise ValidationError(f"{schema_name}.{key} has invalid length")
            if "pattern" in rules and not re.fullmatch(rules["pattern"], value):
                raise ValidationError(f"{schema_name}.{key} has invalid format")
        if isinstance(value, list):
            if len(value) < rules.get("minItems", 0):
                raise ValidationError(f"{schema_name}.{key} has too few items")
            item_rules = rules.get("items", {})
            if item_rules.get("type") == "string" and not all(isinstance(item, str) for item in value):
                raise ValidationError(f"{schema_name}.{key} must contain strings")
            if item_rules.get("minLength") and any(not item for item in value):
                raise ValidationError(f"{schema_name}.{key} contains an empty item")
    return payload


def parse_agent_json(raw: str, schema_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Agent returned invalid JSON for {schema_name}: {exc}") from exc
    return validate_agent_payload(payload, schema_name)


def detect_test_commands(repo_root: Path) -> list[TestCommand]:
    commands: list[TestCommand] = []
    unittest_files = list((repo_root / "tests").glob("test_*.py")) if (repo_root / "tests").is_dir() else []
    if unittest_files and any("unittest" in path.read_text(encoding="utf-8", errors="ignore") for path in unittest_files):
        commands.append(
            TestCommand("python-unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        )

    package_path = repo_root / "package.json"
    if package_path.is_file():
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            package = {}
        if isinstance(package.get("scripts"), dict) and package["scripts"].get("test"):
            npm = shutil.which("npm")
            if npm:
                commands.append(TestCommand("npm-test", [npm, "test"]))
    return commands


def mission_needs_scientist(mission: dict[str, Any]) -> bool:
    if mission.get("scientist_required"):
        return True
    text = " ".join(
        [str(mission.get(key, "")) for key in ("title", "type", "objective")]
        + [str(item) for item in mission.get("acceptance_criteria", [])]
    ).casefold()
    keywords = (
        "tiktok", "metric", "métrique", "scoring", "statistic", "statistique",
        "machine learning", "prediction", "prédiction", "viral", "experiment",
        "expériment", "clip selection", "sélection", "passage vidéo",
    )
    return any(keyword in text for keyword in keywords)


def enforce_first_real_mission(mission: dict[str, Any], state: dict[str, Any]) -> None:
    if state.get("first_real_cycle_completed", False):
        return
    if mission.get("id") != "M-001" or mission.get("type") != "AUDIT_ONLY":
        raise ValidationError("The first real mission must be M-001 with type AUDIT_ONLY")


class Supervisor:
    def __init__(
        self,
        repo_root: Path,
        config: dict[str, Any],
        state: dict[str, Any],
        run_dir: Path,
        *,
        dry_run: bool = False,
        runner: ProcessRunner = subprocess.run,
        state_path: Path = DEFAULT_STATE_PATH,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.config = config
        self.state = state
        self.run_dir = run_dir
        self.dry_run = dry_run
        self.runner = runner
        self.state_path = state_path
        self.interrupted = False

    def request_stop(self, signum: int | None = None, frame: Any = None) -> None:
        del signum, frame
        self.interrupted = True
        LOGGER.warning("Graceful stop requested; current subprocess will finish first")

    def stop_requested(self) -> bool:
        return self.interrupted or STOP_PATH.exists()

    def _context(self, **values: Any) -> str:
        return "\n\nSUPERVISOR CONTEXT (JSON):\n" + json.dumps(
            values, ensure_ascii=False, indent=2
        )

    def _run_agent(
        self,
        role: str,
        prompt: str,
        output_path: Path,
        *,
        schema_name: str | None = None,
    ) -> str:
        if self.stop_requested():
            raise SupervisorError("Stop requested before agent launch")
        if self.dry_run:
            raise AssertionError("Dry-run agents must use deterministic simulations")
        codex = shutil.which("codex")
        if not codex:
            raise PreconditionError("codex command not found on PATH")
        sandbox = "workspace-write" if role == "engineer" else "read-only"
        argv = [
            codex, "exec", "-C", str(self.repo_root), "--sandbox", sandbox,
            "--output-last-message", str(output_path), "-",
        ]
        if schema_name:
            argv[6:6] = ["--output-schema", str(SCHEMA_DIR / schema_name)]
        started = time.monotonic()
        try:
            result = self.runner(
                argv,
                cwd=self.repo_root,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(self.config["agent_timeout_seconds"]),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentExecutionError(f"{role} timed out") from exc
        except OSError as exc:
            raise AgentExecutionError(f"Cannot launch {role}: {exc}") from exc
        duration = time.monotonic() - started
        stdout_text, stderr_text = normalize_process_output(result)
        safe_write_text(
            output_path.with_suffix(".events.txt"),
            stdout_text + "\n" + stderr_text,
        )
        if result.returncode != 0:
            raise AgentExecutionError(f"{role} failed with exit code {result.returncode} after {duration:.2f}s")
        if not output_path.is_file():
            raise AgentExecutionError(f"{role} did not create its final output")
        return output_path.read_text(encoding="utf-8")

    def _agent_json(
        self, role: str, prompt: str, output_path: Path, schema_name: str
    ) -> dict[str, Any]:
        raw = self._run_agent(role, prompt, output_path, schema_name=schema_name)
        payload = parse_agent_json(raw, schema_name)
        write_json(output_path, payload)
        return payload

    def _agent_text(self, role: str, prompt: str, output_path: Path) -> str:
        raw = self._run_agent(role, prompt, output_path)
        safe_write_text(output_path, raw)
        return redact_text(raw)

    def _dry_mission(self) -> dict[str, Any]:
        return {
            "id": "M-001",
            "title": "Audit complet du pipeline existant",
            "type": "AUDIT_ONLY",
            "objective": "Cartographier le dépôt sans modifier le pipeline TikTok.",
            "acceptance_criteria": ["Un rapport d'audit factuel est produit."],
            "test_focus": ["Aucune régression du dépôt existant."],
            "scientist_required": True,
            "commit_description": "audit du pipeline existant",
        }

    def choose_mission(self, cycle_dir: Path) -> dict[str, Any]:
        active = self.state.get("active_mission")
        if active and not self.dry_run:
            mission = validate_agent_payload(active, "mission.schema.json")
            write_json(cycle_dir / "coach_output.json", mission)
            LOGGER.info("Resuming interrupted mission %s", mission["id"])
        elif self.dry_run:
            mission = self._dry_mission()
            write_json(cycle_dir / "coach_output.json", mission)
        else:
            prompt = (PROMPT_DIR / "coach.md").read_text(encoding="utf-8") + self._context(
                state=self.state,
                previous_report_path=".coach/CODEX_REPORT.md",
                dry_run=False,
            )
            mission = self._agent_json(
                "coach", prompt, cycle_dir / "coach_output.json", "mission.schema.json"
            )
        enforce_first_real_mission(mission, self.state)
        return mission

    def run_tests(self, cycle_dir: Path) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        commands = detect_test_commands(self.repo_root)
        if self.dry_run:
            payload = {
                "status": "simulated",
                "all_passed": True,
                "justification": "Dry-run does not execute repository commands.",
                "commands_detected": [{"name": item.name, "argv": item.argv} for item in commands],
                "results": [],
            }
            write_json(cycle_dir / "tests.json", payload)
            return payload
        for command in commands:
            started = time.monotonic()
            result = run_checked(
                command.argv,
                cwd=self.repo_root,
                timeout=int(self.config["test_timeout_seconds"]),
                runner=self.runner,
            )
            results.append(
                {
                    "name": command.name,
                    "argv": command.argv,
                    "exit_code": result.returncode,
                    "stdout": redact_text(result.stdout),
                    "stderr": redact_text(result.stderr),
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
        payload = {
            "status": "completed" if commands else "not_configured",
            "all_passed": bool(commands) and all(item["exit_code"] == 0 for item in results),
            "justification": "" if commands else "No supported, genuinely configured test command was detected.",
            "results": results,
        }
        write_json(cycle_dir / "tests.json", payload)
        return payload

    def capture_diff(self, cycle_dir: Path) -> str:
        if self.dry_run:
            diff = "# dry-run: no workspace changes\n"
        else:
            result = run_checked(
                ["git", "diff", "--no-ext-diff", "--binary", "--", ".", ":(exclude)coach_system/state.json"],
                cwd=self.repo_root,
                timeout=60,
                runner=self.runner,
            )
            if result.returncode != 0:
                raise SupervisorError("Unable to capture Git diff")
            diff = result.stdout
        safe_write_text(cycle_dir / "git_diff.patch", diff)
        return redact_text(diff)

    def commit(self, mission: dict[str, Any]) -> None:
        if self.dry_run or not self.config.get("auto_commit", True):
            return
        message = f"coach({mission['id']}): {mission['commit_description']}"
        add = run_checked(["git", "add", "-A"], cwd=self.repo_root, timeout=60, runner=self.runner)
        if add.returncode != 0:
            raise SupervisorError("git add failed")
        staged = run_checked(["git", "diff", "--cached", "--quiet"], cwd=self.repo_root, timeout=60, runner=self.runner)
        if staged.returncode == 0:
            raise SupervisorError("Accepted mission produced no committable change")
        if staged.returncode != 1:
            raise SupervisorError("Unable to inspect staged Git diff")
        committed = run_checked(["git", "commit", "-m", message], cwd=self.repo_root, timeout=120, runner=self.runner)
        if committed.returncode != 0:
            raise SupervisorError("git commit failed")

    def execute_mission(self, mission: dict[str, Any], cycle_dir: Path) -> dict[str, Any]:
        scientist_required = mission_needs_scientist(mission)
        objections: list[str] = []
        maximum = int(self.config["max_retries_per_mission"])
        final_tests: dict[str, Any] = {}
        final_review: dict[str, Any] = {}
        final_science: dict[str, Any] | None = None

        for attempt in range(1, maximum + 1):
            if self.stop_requested():
                return {"status": "INTERRUPTED", "attempts": attempt - 1}
            attempt_dir = cycle_dir / f"attempt_{attempt:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            if self.dry_run:
                engineering = "DRY-RUN: Engineer invocation simulated; no files modified."
                safe_write_text(attempt_dir / "engineer_output.txt", engineering)
            else:
                engineer_prompt = (PROMPT_DIR / "engineer.md").read_text(encoding="utf-8") + self._context(
                    mission=mission, attempt=attempt, previous_objections=objections
                )
                engineering = self._agent_text(
                    "engineer", engineer_prompt, attempt_dir / "engineer_output.txt"
                )

            final_tests = self.run_tests(attempt_dir)
            diff = self.capture_diff(attempt_dir)
            if self.config.get("stop_on_test_failure") and not final_tests["all_passed"]:
                objections = ["Configured test command failed and stop_on_test_failure is enabled."]
                continue

            if self.dry_run:
                final_review = {"verdict": "ACCEPT", "summary": "Dry-run reviewer simulation.", "issues": []}
                write_json(attempt_dir / "reviewer_output.json", final_review)
            else:
                reviewer_prompt = (PROMPT_DIR / "reviewer.md").read_text(encoding="utf-8") + self._context(
                    mission=mission, engineering_report=engineering, tests=final_tests, git_diff=diff
                )
                final_review = self._agent_json(
                    "reviewer", reviewer_prompt, attempt_dir / "reviewer_output.json", "review.schema.json"
                )

            if final_review["verdict"] != "ACCEPT":
                if final_review["verdict"] == "BLOCKED":
                    break
                objections = list(final_review["issues"]) or [final_review["summary"]]
                continue

            if scientist_required:
                if self.dry_run:
                    final_science = {"verdict": "ACCEPT", "summary": "Dry-run scientist simulation.", "issues": []}
                    write_json(attempt_dir / "scientist_output.json", final_science)
                else:
                    scientist_prompt = (PROMPT_DIR / "scientist.md").read_text(encoding="utf-8") + self._context(
                        mission=mission, engineering_report=engineering, tests=final_tests, git_diff=diff
                    )
                    final_science = self._agent_json(
                        "scientist", scientist_prompt, attempt_dir / "scientist_output.json", "scientist.schema.json"
                    )
                if final_science["verdict"] != "ACCEPT":
                    if final_science["verdict"] == "BLOCKED":
                        break
                    objections = list(final_science["issues"]) or [final_science["summary"]]
                    continue

            tests_acceptable = final_tests["all_passed"] or (
                final_tests["status"] == "not_configured" and bool(final_tests["justification"])
            ) or final_tests["status"] == "simulated"
            if not tests_acceptable:
                objections = ["At least one configured test command failed."]
                continue

            return {
                "status": "ACCEPTED",
                "attempts": attempt,
                "scientist_required": scientist_required,
                "tests": final_tests["status"],
                "reviewer": final_review["verdict"],
                "scientist": final_science["verdict"] if final_science else None,
            }

        return {
            "status": "BLOCKED",
            "attempts": min(maximum, attempt),
            "scientist_required": scientist_required,
            "reason": objections or final_review.get("issues") or ["Agent returned BLOCKED."],
        }

    def run(self, max_cycles: int) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=False)
        completed = 0
        for cycle in range(1, max_cycles + 1):
            if self.stop_requested():
                break
            cycle_dir = self.run_dir / f"cycle_{cycle:03d}"
            cycle_dir.mkdir(parents=True)
            mission = self.choose_mission(cycle_dir)
            if not self.dry_run:
                self.state["active_mission"] = mission
                self.state["interrupted"] = False
                write_json(self.state_path, self.state)
            summary = self.execute_mission(mission, cycle_dir)
            summary.update({"cycle": cycle, "mission_id": mission["id"], "finished_at": utc_now(), "dry_run": self.dry_run})
            write_json(cycle_dir / "cycle_summary.json", summary)
            if summary["status"] != "ACCEPTED":
                if not self.dry_run:
                    interrupted = summary["status"] == "INTERRUPTED"
                    if not interrupted:
                        self.state["blocked_missions"].append({"mission": mission, "summary": summary})
                        self.state["active_mission"] = None
                    self.state["interrupted"] = interrupted
                    self.state["last_run"] = utc_now()
                    write_json(self.state_path, self.state)
                break
            completed += 1
            if not self.dry_run:
                self.state["completed_missions"].append({"mission": mission, "summary": summary})
                self.state["active_mission"] = None
                self.state["first_real_cycle_completed"] = True
                self.state["last_run"] = utc_now()
                write_json(self.state_path, self.state)
                self.commit(mission)
            if self.stop_requested() or cycle == max_cycles:
                break
            time.sleep(float(self.config["sleep_between_cycles_seconds"]))
        if not self.dry_run and self.stop_requested():
            self.state["interrupted"] = True
            self.state["last_run"] = utc_now()
            write_json(self.state_path, self.state)
        return 0 if completed > 0 or self.stop_requested() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local safe Codex multi-agent supervisor")
    parser.add_argument("--dry-run", action="store_true", help="simulate agents, tests, writes, and commits")
    parser.add_argument("--max-cycles", type=int, help="override configured cycle limit")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO_ROOT)
    return parser


def configure_logging(run_name: str) -> None:
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {run_name} %(levelname)s %(message)s")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_json(args.config)
    state = load_json(args.state)
    max_cycles = args.max_cycles if args.max_cycles is not None else int(config["max_cycles"])
    if max_cycles < 1:
        raise SystemExit("--max-cycles must be at least 1")
    run_name = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    configure_logging(run_name)
    run_dir = LOGS_DIR / run_name
    supervisor = Supervisor(
        args.repo, config, state, run_dir, dry_run=args.dry_run, state_path=args.state
    )
    try:
        with InstanceLock():
            preflight_config = dict(config)
            if args.dry_run or state.get("active_mission"):
                preflight_config["require_clean_git_start"] = False
            verify_preconditions(
                args.repo.resolve(), preflight_config, require_codex=not args.dry_run
            )
            signal.signal(signal.SIGINT, supervisor.request_stop)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, supervisor.request_stop)
            LOGGER.info("Starting %s cycle(s); dry_run=%s", max_cycles, args.dry_run)
            return supervisor.run(max_cycles)
    except (SupervisorError, ValidationError) as exc:
        if not args.dry_run and supervisor.interrupted:
            state["interrupted"] = True
            state["last_run"] = utc_now()
            write_json(args.state, state)
        LOGGER.error("%s", redact_text(str(exc)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
