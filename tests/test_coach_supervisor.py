import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from coach_system import supervisor as module
from coach_system.supervisor import (
    AgentExecutionError,
    AlreadyRunningError,
    InstanceLock,
    PreconditionError,
    Supervisor,
    ValidationError,
    parse_agent_json,
    run_checked,
    verify_preconditions,
)


def config(**overrides):
    value = {
        "max_cycles": 5,
        "max_retries_per_mission": 3,
        "stop_on_test_failure": False,
        "auto_commit": False,
        "require_clean_git_start": False,
        "sleep_between_cycles_seconds": 0,
        "agent_timeout_seconds": 1,
        "test_timeout_seconds": 1,
    }
    value.update(overrides)
    return value


def state(**overrides):
    value = {
        "first_real_cycle_completed": True,
        "completed_missions": [],
        "blocked_missions": [],
        "active_mission": None,
        "last_run": None,
        "interrupted": False,
    }
    value.update(overrides)
    return value


def mission(scientist=False):
    return {
        "id": "M-002",
        "title": "Maintenance locale",
        "type": "FIX",
        "objective": "Corriger une erreur locale reproductible.",
        "acceptance_criteria": ["Le test de régression passe."],
        "test_focus": ["Régression"],
        "scientist_required": scientist,
        "commit_description": "corrige une erreur locale",
    }


def passed_tests():
    return {"status": "completed", "all_passed": True, "justification": "", "results": []}


class CoachSupervisorTests(unittest.TestCase):
    def assert_agent_streams_are_safe(self, stdout_bytes, stderr_bytes, expected_events):
        captured = {}

        def mocked_codex(argv, **kwargs):
            captured.update(kwargs)
            output_index = argv.index("--output-last-message") + 1
            Path(argv[output_index]).write_text("agent continued", encoding="utf-8")

            def decode(value):
                if value is None:
                    return None
                return value.decode(kwargs["encoding"], errors=kwargs["errors"])

            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=decode(stdout_bytes),
                stderr=decode(stderr_bytes),
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_path = root / "agent_output.txt"
            sup = Supervisor(root, config(), state(), root / "logs", runner=mocked_codex)
            with patch("coach_system.supervisor.shutil.which", return_value="codex"):
                result = sup._run_agent("coach", "prompt", output_path)
            self.assertEqual(result, "agent continued")
            self.assertEqual(
                output_path.with_suffix(".events.txt").read_text(encoding="utf-8"),
                expected_events,
            )
            self.assertEqual(captured["encoding"], "utf-8")
            self.assertEqual(captured["errors"], "replace")
            self.assertTrue(captured["text"])

    def test_codex_utf8_bytes_not_decodable_as_cp1252(self):
        text = "Réponse — coach → prêt 📝"
        raw = text.encode("utf-8")
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("cp1252")
        self.assert_agent_streams_are_safe(raw, b"", text + "\n")

    def test_codex_french_accents_are_preserved(self):
        text = "Réponse déjà prête"
        self.assert_agent_streams_are_safe(text.encode("utf-8"), b"", text + "\n")

    def test_codex_em_dash_is_preserved(self):
        text = "phase — suivante"
        self.assert_agent_streams_are_safe(text.encode("utf-8"), b"", text + "\n")

    def test_codex_unicode_arrow_is_preserved(self):
        text = "coach → engineer"
        self.assert_agent_streams_are_safe(text.encode("utf-8"), b"", text + "\n")

    def test_codex_emoji_is_preserved(self):
        text = "succès 📝"
        self.assert_agent_streams_are_safe(text.encode("utf-8"), b"", text + "\n")

    def test_codex_stdout_and_stderr_none_are_empty(self):
        self.assert_agent_streams_are_safe(None, None, "\n")

    def test_codex_valid_stdout_and_stderr_none_continue(self):
        self.assert_agent_streams_are_safe(b"stdout valid", None, "stdout valid\n")

    def test_codex_stdout_none_and_valid_stderr_continue(self):
        self.assert_agent_streams_are_safe(None, b"stderr valid", "\nstderr valid")

    def test_run_checked_uses_utf8_replace_and_normalizes_none(self):
        captured = {}

        def mocked_command(argv, **kwargs):
            captured.update(kwargs)
            invalid_utf8 = b"before:\xff:after"
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=invalid_utf8.decode(kwargs["encoding"], errors=kwargs["errors"]),
                stderr=None,
            )

        result = run_checked(
            ["mock-command"], cwd=Path.cwd(), timeout=1, runner=mocked_command
        )
        self.assertEqual(result.stdout, "before:�:after")
        self.assertEqual(result.stderr, "")
        self.assertEqual(captured["encoding"], "utf-8")
        self.assertEqual(captured["errors"], "replace")

    def test_codex_absent(self):
        with patch("coach_system.supervisor.shutil.which", return_value=None):
            with self.assertRaises(PreconditionError):
                verify_preconditions(Path.cwd(), config())

    def test_repository_not_git(self):
        completed = [
            subprocess.CompletedProcess([], 0, "Logged in", ""),
            subprocess.CompletedProcess([], 1, "", "not a repository"),
        ]
        with patch("coach_system.supervisor.shutil.which", return_value="tool"):
            with self.assertRaises(PreconditionError):
                verify_preconditions(Path.cwd(), config(), runner=Mock(side_effect=completed))

    def test_invalid_json(self):
        with self.assertRaises(ValidationError):
            parse_agent_json("not-json", "review.schema.json")

    def test_agent_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(), state(), root / "logs", runner=Mock(side_effect=subprocess.TimeoutExpired("codex", 1)))
            with patch("coach_system.supervisor.shutil.which", return_value="codex"):
                with self.assertRaises(AgentExecutionError):
                    sup._run_agent("reviewer", "prompt", root / "out.json")

    def test_failing_test_command_is_captured(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_sample.py").write_text("import unittest\n", encoding="utf-8")
            runner = Mock(return_value=subprocess.CompletedProcess([], 1, "out", "failure"))
            sup = Supervisor(root, config(), state(), root / "run", runner=runner)
            result = sup.run_tests(root / "cycle")
            self.assertFalse(result["all_passed"])
            self.assertEqual(result["results"][0]["exit_code"], 1)
            self.assertEqual(result["results"][0]["stderr"], "failure")

    def test_reviewer_reject_returns_to_engineer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(), state(), root / "run")
            sup._agent_text = Mock(return_value="engineered")
            reviews = iter([
                {"verdict": "REJECT", "summary": "bug", "issues": ["fix bug"]},
                {"verdict": "ACCEPT", "summary": "ok", "issues": []},
            ])
            sup._agent_json = Mock(side_effect=lambda role, *args: next(reviews))
            sup.run_tests = Mock(return_value=passed_tests())
            sup.capture_diff = Mock(return_value="diff")
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(sup._agent_text.call_count, 2)

    def test_scientist_insufficient_evidence_returns_to_engineer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(), state(), root / "run")
            sup._agent_text = Mock(return_value="engineered")
            science_count = 0

            def json_agent(role, *args):
                nonlocal science_count
                if role == "reviewer":
                    return {"verdict": "ACCEPT", "summary": "ok", "issues": []}
                science_count += 1
                if science_count == 1:
                    return {"verdict": "INSUFFICIENT_EVIDENCE", "summary": "weak", "issues": ["add evidence"]}
                return {"verdict": "ACCEPT", "summary": "ok", "issues": []}

            sup._agent_json = Mock(side_effect=json_agent)
            sup.run_tests = Mock(return_value=passed_tests())
            sup.capture_diff = Mock(return_value="diff")
            result = sup.execute_mission(mission(scientist=True), root / "cycle")
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["attempts"], 2)

    def test_retry_limit_blocks_mission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(max_retries_per_mission=3), state(), root / "run")
            sup._agent_text = Mock(return_value="engineered")
            sup._agent_json = Mock(return_value={"verdict": "REJECT", "summary": "bad", "issues": ["still bad"]})
            sup.run_tests = Mock(return_value=passed_tests())
            sup.capture_diff = Mock(return_value="diff")
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["attempts"], 3)
            self.assertEqual(sup._agent_text.call_count, 3)

    def test_max_cycles_is_respected_in_dry_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stop = root / "stop"
            with patch.object(module, "STOP_PATH", stop):
                sup = Supervisor(root, config(), state(first_real_cycle_completed=False), root / "run", dry_run=True)
                self.assertEqual(sup.run(2), 0)
            self.assertTrue((root / "run" / "cycle_001" / "cycle_summary.json").is_file())
            self.assertTrue((root / "run" / "cycle_002" / "cycle_summary.json").is_file())
            self.assertFalse((root / "run" / "cycle_003").exists())

    def test_stop_file_prevents_agent_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stop = root / "STOP_REQUESTED"
            stop.write_text("", encoding="utf-8")
            with patch.object(module, "STOP_PATH", stop):
                sup = Supervisor(root, config(), state(), root / "run", dry_run=True)
                self.assertEqual(sup.run(1), 0)
            self.assertFalse((root / "run" / "cycle_001").exists())

    def test_interrupted_active_mission_is_resumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cycle = root / "cycle"
            cycle.mkdir()
            sup = Supervisor(root, config(), state(active_mission=mission()), root / "run")
            resumed = sup.choose_mission(cycle)
            self.assertEqual(resumed["id"], "M-002")
            self.assertTrue((cycle / "coach_output.json").is_file())

    def test_lock_prevents_double_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "supervisor.lock"
            with InstanceLock(lock_path):
                with self.assertRaises(AlreadyRunningError):
                    with InstanceLock(lock_path):
                        pass
            self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
