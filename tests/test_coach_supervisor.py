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
    GitBaseline,
    InstanceLock,
    PreconditionError,
    Supervisor,
    ValidationError,
    capture_git_baseline,
    classify_status_lines,
    diff_workspace_snapshots,
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
    return {
        "source": "SUPERVISOR_AUTHORITATIVE_TEST_RUN",
        "run_id": "test-run",
        "status": "completed",
        "all_passed": True,
        "justification": "",
        "results": [],
    }


def baseline(label="TEST_BASELINE", files=None, business_status=None, runtime_status=None):
    return GitBaseline(
        label=label,
        captured_at="2026-07-10T00:00:00+00:00",
        head="abc123",
        files=files or {},
        preexisting_business_status=business_status or [],
        runtime_status=runtime_status or [],
    )


def mock_git_evidence(supervisor):
    supervisor.capture_baseline = Mock(
        side_effect=lambda path, label: baseline(label=label)
    )
    supervisor.build_review_evidence = Mock(
        return_value={
            "cumulative_business_changed_paths": [],
            "attempt_business_changed_paths": [],
        }
    )


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
            mock_git_evidence(sup)
            sup._agent_text = Mock(return_value="engineered")
            reviews = iter([
                {"verdict": "REJECT", "issue_classification": "ENGINEER_FIXABLE", "summary": "bug", "issues": ["fix bug"]},
                {"verdict": "ACCEPT", "issue_classification": "NONE", "summary": "ok", "issues": []},
            ])
            sup._agent_json = Mock(side_effect=lambda role, *args: next(reviews))
            sup.run_tests = Mock(return_value=passed_tests())
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(sup._agent_text.call_count, 2)

    def test_scientist_insufficient_evidence_returns_to_engineer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(), state(), root / "run")
            mock_git_evidence(sup)
            sup._agent_text = Mock(return_value="engineered")
            science_count = 0

            def json_agent(role, *args):
                nonlocal science_count
                if role == "reviewer":
                    return {"verdict": "ACCEPT", "issue_classification": "NONE", "summary": "ok", "issues": []}
                science_count += 1
                if science_count == 1:
                    return {"verdict": "INSUFFICIENT_EVIDENCE", "summary": "weak", "issues": ["add evidence"]}
                return {"verdict": "ACCEPT", "summary": "ok", "issues": []}

            sup._agent_json = Mock(side_effect=json_agent)
            sup.run_tests = Mock(return_value=passed_tests())
            result = sup.execute_mission(mission(scientist=True), root / "cycle")
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["attempts"], 2)

    def test_retry_limit_blocks_mission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(max_retries_per_mission=3), state(), root / "run")
            mock_git_evidence(sup)
            sup._agent_text = Mock(return_value="engineered")
            sup._agent_json = Mock(return_value={"verdict": "REJECT", "issue_classification": "ENGINEER_FIXABLE", "summary": "bad", "issues": ["still bad"]})
            sup.run_tests = Mock(return_value=passed_tests())
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["attempts"], 3)
            self.assertEqual(sup._agent_text.call_count, 3)

    def test_distinct_engineer_and_authoritative_test_timings_are_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(root, config(), state(), root / "run")
            mock_git_evidence(sup)
            sup._agent_text = Mock(
                return_value="ENGINEER_SELF_TEST_RUN: Ran in 0.631 s"
            )
            authoritative = passed_tests()
            authoritative["results"] = [{"duration_seconds": 0.764}]
            sup.run_tests = Mock(return_value=authoritative)
            reviewer_prompts = []

            def reviewer(role, prompt, *args):
                reviewer_prompts.append(prompt)
                return {
                    "verdict": "ACCEPT",
                    "issue_classification": "NONE",
                    "summary": "Distinct runs are correctly attributed.",
                    "issues": [],
                }

            sup._agent_json = Mock(side_effect=reviewer)
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["attempts"], 1)
            self.assertIn("ENGINEER_SELF_TEST_RUN", reviewer_prompts[0])
            self.assertIn("0.631", reviewer_prompts[0])
            self.assertIn("SUPERVISOR_AUTHORITATIVE_TEST_RUN", reviewer_prompts[0])
            self.assertIn("0.764", reviewer_prompts[0])
            self.assertIn("tests.json", reviewer_prompts[0])

    def test_runtime_state_change_is_excluded_from_business_diff(self):
        patch_text, changed = diff_workspace_snapshots(
            {
                "src/app.py": b"unchanged\n",
                "coach_system/state.json": b'{"active_mission": null}\n',
            },
            {
                "src/app.py": b"unchanged\n",
                "coach_system/state.json": b'{"active_mission": {"id": "M-001"}}\n',
            },
        )
        self.assertEqual(changed, [])
        self.assertEqual(patch_text, "")

    def test_real_out_of_scope_engineer_change_remains_visible(self):
        patch_text, changed = diff_workspace_snapshots(
            {".coach/CODEX_REPORT.md": b"audit\n", "src/unrelated.py": b"safe\n"},
            {".coach/CODEX_REPORT.md": b"audit updated\n", "src/unrelated.py": b"unsafe\n"},
        )
        self.assertEqual(
            changed, [".coach/CODEX_REPORT.md", "src/unrelated.py"]
        )
        self.assertIn("a/src/unrelated.py", patch_text)
        self.assertIn("+unsafe", patch_text)

    def test_preexisting_business_and_runtime_changes_are_distinguished(self):
        business, runtime = classify_status_lines(
            [
                " M .coach/CODEX_REPORT.md",
                " M coach_system/state.json",
                "?? coach_system/logs/run/tests.json",
            ]
        )
        self.assertEqual(business, [" M .coach/CODEX_REPORT.md"])
        self.assertEqual(
            runtime,
            [
                " M coach_system/state.json",
                "?? coach_system/logs/run/tests.json",
            ],
        )

    def test_git_baseline_records_hashes_and_excludes_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".coach").mkdir()
            (root / "coach_system").mkdir()
            (root / ".coach" / "CODEX_REPORT.md").write_text(
                "audit\n", encoding="utf-8"
            )
            (root / "coach_system" / "state.json").write_text(
                "{}\n", encoding="utf-8"
            )

            def mocked_git(argv, **kwargs):
                if argv[1:3] == ["rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(argv, 0, "abc123\n", "")
                if argv[1:3] == ["ls-files", "-z"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        ".coach/CODEX_REPORT.md\0coach_system/state.json\0",
                        "",
                    )
                if argv[1:3] == ["status", "--porcelain"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        " M .coach/CODEX_REPORT.md\n M coach_system/state.json\n",
                        "",
                    )
                self.fail(f"Unexpected command: {argv}")

            captured = capture_git_baseline(root, "ATTEMPT_START", runner=mocked_git)
            self.assertEqual(captured.head, "abc123")
            self.assertEqual(
                list(captured.files), [".coach/CODEX_REPORT.md"]
            )
            self.assertEqual(
                captured.preexisting_business_status,
                [" M .coach/CODEX_REPORT.md"],
            )
            self.assertEqual(
                captured.runtime_status, [" M coach_system/state.json"]
            )

    def test_supervisor_infrastructure_issue_does_not_consume_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(
                root, config(max_retries_per_mission=3), state(), root / "run"
            )
            mock_git_evidence(sup)
            sup._agent_text = Mock(return_value="engineered")
            sup.run_tests = Mock(return_value=passed_tests())
            sup._agent_json = Mock(
                return_value={
                    "verdict": "REJECT",
                    "issue_classification": "SUPERVISOR_INFRASTRUCTURE",
                    "summary": "Runtime artifact polluted review.",
                    "issues": ["Fix the supervisor, not the mission."],
                }
            )
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["attempts"], 1)
            self.assertEqual(
                result["issue_classification"], "SUPERVISOR_INFRASTRUCTURE"
            )
            self.assertEqual(sup._agent_text.call_count, 1)

    def test_failed_authoritative_tests_still_block_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sup = Supervisor(
                root, config(max_retries_per_mission=2), state(), root / "run"
            )
            mock_git_evidence(sup)
            sup._agent_text = Mock(return_value="engineered")
            failed = passed_tests()
            failed["all_passed"] = False
            failed["results"] = [{"exit_code": 1, "duration_seconds": 0.764}]
            sup.run_tests = Mock(return_value=failed)
            sup._agent_json = Mock(
                return_value={
                    "verdict": "ACCEPT",
                    "issue_classification": "NONE",
                    "summary": "Code review passed.",
                    "issues": [],
                }
            )
            result = sup.execute_mission(mission(), root / "cycle")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(sup._agent_text.call_count, 2)

    def test_tests_json_declares_authoritative_supervisor_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cycle = root / "cycle"
            sup = Supervisor(root, config(), state(), root / "run", dry_run=True)
            result = sup.run_tests(cycle)
            saved = json.loads((cycle / "tests.json").read_text(encoding="utf-8"))
            self.assertEqual(
                result["source"], "SUPERVISOR_AUTHORITATIVE_TEST_RUN"
            )
            self.assertEqual(saved["source"], result["source"])

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
