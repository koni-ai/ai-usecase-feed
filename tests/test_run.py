from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import run


SEOUL = ZoneInfo("Asia/Seoul")


class PipelineTests(unittest.TestCase):
    def test_success_runs_all_steps_and_writes_log(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "ok\n", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run.run_pipeline(
                PROJECT_DIR,
                root / "logs",
                now=datetime(2026, 7, 23, 7, 0, tzinfo=SEOUL),
                runner=fake_runner,
            )
            log_text = Path(result["log_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["status"], "success")
        self.assertEqual([item["step"] for item in result["steps"]], [
            "collect",
            "process",
            "build",
        ])
        self.assertEqual(len(calls), 3)
        self.assertIn("billing_mode=existing_claude_subscription", log_text)
        self.assertIn("[pipeline] status=success", log_text)

    def test_failure_stops_before_later_steps(self) -> None:
        return_codes = iter((0, 7, 0))
        called_scripts: list[str] = []

        def fake_runner(command, **kwargs):
            called_scripts.append(Path(command[-1]).name)
            return subprocess.CompletedProcess(
                command,
                next(return_codes),
                "",
                "forced failure",
            )

        with tempfile.TemporaryDirectory() as directory:
            result = run.run_pipeline(
                PROJECT_DIR,
                Path(directory) / "logs",
                now=datetime(2026, 7, 23, 7, 0, tzinfo=SEOUL),
                runner=fake_runner,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_step"], "process")
        self.assertEqual(called_scripts, ["collect.py", "process.py"])

    def test_api_key_and_cloud_routing_are_removed_from_children(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_runner(command, **kwargs):
            captured_env.update(kwargs["env"])
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "ANTHROPIC_API_KEY": "must-not-leak",
                    "CLAUDE_CODE_USE_BEDROCK": "1",
                    "CLAUDE_CODE_USE_VERTEX": "1",
                    "CLAUDE_CODE_USE_FOUNDRY": "1",
                },
            ):
                run.run_pipeline(
                    PROJECT_DIR,
                    Path(directory) / "logs",
                    steps=(run.PipelineStep("build", "build.py", 60),),
                    runner=fake_runner,
                )

        for key in run.SENSITIVE_ENV_KEYS:
            self.assertNotIn(key, captured_env)
        self.assertEqual(captured_env["PYTHONUTF8"], "1")
        self.assertEqual(captured_env["PYTHONIOENCODING"], "utf-8")

    def test_registration_script_is_interactive_limited_and_supports_whatif(self) -> None:
        script = (PROJECT_DIR / "register_task.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", script)
        self.assertIn('New-ScheduledTaskTrigger -Daily -At $Time', script)
        self.assertIn("-LogonType Interactive", script)
        self.assertIn("-RunLevel Limited", script)
        self.assertIn("claude.ai", script)
        self.assertNotIn("ANTHROPIC_API_KEY", script)


if __name__ == "__main__":
    unittest.main()
