from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
SENSITIVE_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}


@dataclass(frozen=True)
class PipelineStep:
    name: str
    script: str
    timeout_seconds: int


DEFAULT_STEPS = (
    PipelineStep("collect", "collect.py", 300),
    PipelineStep("process", "process.py", 1_800),
    PipelineStep("build", "build.py", 120),
)


def _safe_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in SENSITIVE_ENV_KEYS:
        environment.pop(key, None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _new_log_path(logs_dir: Path, now: datetime) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stem = f"pipeline-{now.strftime('%Y%m%d-%H%M%S')}"
    candidate = logs_dir / f"{stem}.log"
    suffix = 1
    while candidate.exists():
        candidate = logs_dir / f"{stem}-{suffix}.log"
        suffix += 1
    return candidate


def _write_log(handle, text: str) -> None:
    handle.write(text)
    if not text.endswith("\n"):
        handle.write("\n")
    handle.flush()


def run_pipeline(
    project_dir: Path,
    logs_dir: Path,
    *,
    steps: Sequence[PipelineStep] = DEFAULT_STEPS,
    now: datetime | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    project_dir = project_dir.resolve()
    now = now or datetime.now(SEOUL)
    log_path = _new_log_path(logs_dir.resolve(), now)
    results: list[dict[str, object]] = []
    status = "success"
    failed_step: str | None = None

    creation_flags = (
        subprocess.CREATE_NO_WINDOW
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        _write_log(log, f"[pipeline] started_at={now.isoformat()}")
        _write_log(log, f"[pipeline] project_dir={project_dir}")
        _write_log(log, "[pipeline] billing_mode=existing_claude_subscription")
        for step in steps:
            script_path = project_dir / step.script
            command = [sys.executable, "-B", str(script_path)]
            started = datetime.now(SEOUL)
            _write_log(log, f"\n[{step.name}] started_at={started.isoformat()}")
            _write_log(log, f"[{step.name}] command={script_path.name}")
            try:
                completed = runner(
                    command,
                    cwd=project_dir,
                    env=_safe_environment(),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=step.timeout_seconds,
                    creationflags=creation_flags,
                    check=False,
                )
                return_code = int(completed.returncode)
                stdout = completed.stdout or ""
                stderr = completed.stderr or ""
                _write_log(log, f"[{step.name}] return_code={return_code}")
                if stdout:
                    _write_log(log, f"[{step.name}] stdout:\n{stdout.rstrip()}")
                if stderr:
                    _write_log(log, f"[{step.name}] stderr:\n{stderr.rstrip()}")
                results.append(
                    {
                        "step": step.name,
                        "return_code": return_code,
                        "duration_seconds": round(
                            (datetime.now(SEOUL) - started).total_seconds(), 3
                        ),
                    }
                )
                if return_code != 0:
                    status = "failed"
                    failed_step = step.name
                    break
            except subprocess.TimeoutExpired as exc:
                status = "failed"
                failed_step = step.name
                _write_log(
                    log,
                    f"[{step.name}] timeout_seconds={step.timeout_seconds}",
                )
                results.append(
                    {
                        "step": step.name,
                        "return_code": None,
                        "timeout": True,
                    }
                )
                if exc.stdout:
                    _write_log(log, f"[{step.name}] stdout:\n{exc.stdout}")
                if exc.stderr:
                    _write_log(log, f"[{step.name}] stderr:\n{exc.stderr}")
                break
            except OSError as exc:
                status = "failed"
                failed_step = step.name
                _write_log(log, f"[{step.name}] os_error={type(exc).__name__}: {exc}")
                results.append(
                    {
                        "step": step.name,
                        "return_code": None,
                        "os_error": type(exc).__name__,
                    }
                )
                break

        finished = datetime.now(SEOUL)
        _write_log(log, f"\n[pipeline] status={status}")
        _write_log(log, f"[pipeline] finished_at={finished.isoformat()}")
        if failed_step:
            _write_log(log, f"[pipeline] failed_step={failed_step}")

    return {
        "status": status,
        "failed_step": failed_step,
        "steps": results,
        "log_path": str(log_path),
        "billing_mode": "existing_claude_subscription",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="AI 활용사례 피드 수집·가공·빌드 통합 실행기"
    )
    parser.add_argument("--project-dir", type=Path, default=project_dir)
    parser.add_argument("--logs-dir", type=Path, default=project_dir / "logs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_pipeline(args.project_dir, args.logs_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "success" else 2
    except Exception as exc:
        print(
            f"통합 실행기 오류: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
