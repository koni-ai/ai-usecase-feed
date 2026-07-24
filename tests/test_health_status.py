from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import source_manager


class HealthStatusTests(unittest.TestCase):
    def test_first_failure_is_failing_not_healthy(self) -> None:
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=temp_root,
            ignore_cleanup_errors=True,
        ) as directory:
            root = Path(directory)
            registry = root / "registry.json"
            health = root / "health.json"
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": None,
                        "sources": [],
                        "retired_hosts": [],
                        "discovery_runs": [],
                    }
                ),
                encoding="utf-8",
            )
            health.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": None,
                        "sources": {},
                    }
                ),
                encoding="utf-8",
            )
            source_manager.update_health(
                health,
                registry,
                [
                    {
                        "name": "Broken",
                        "status": "error",
                        "fetched": 0,
                        "selected": 0,
                        "duplicate": 0,
                        "error": "forced",
                    }
                ],
                datetime(2026, 7, 24, tzinfo=source_manager.SEOUL),
            )
            state = source_manager.load_health(health)["sources"]["Broken"]
            self.assertEqual("failing", state["status"])
            self.assertEqual(1, state["consecutive_failures"])


if __name__ == "__main__":
    unittest.main()
