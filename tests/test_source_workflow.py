from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DAILY = PROJECT_DIR / ".github" / "workflows" / "daily-feed.yml"
DISCOVERY = PROJECT_DIR / ".github" / "workflows" / "source-discovery.yml"


class SourceWorkflowTests(unittest.TestCase):
    def test_weekly_discovery_is_backend_only_and_zero_incremental_cost(self) -> None:
        text = DISCOVERY.read_text(encoding="utf-8")
        self.assertIn('cron: "15 8 * * 0"', text)
        self.assertIn('timezone: "Asia/Seoul"', text)
        self.assertIn("python -B source_manager.py discover", text)
        self.assertIn("python -B -m unittest discover -s tests -q", text)
        self.assertIn(
            "git add data/source_registry.json data/source_health.json",
            text,
        )
        self.assertNotIn("ANTHROPIC_API_KEY", text)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", text)
        self.assertNotIn("site/index.html", text)
        self.assertNotIn("deploy-pages", text)

    def test_discovery_and_daily_jobs_cannot_race(self) -> None:
        daily = DAILY.read_text(encoding="utf-8")
        discovery = DISCOVERY.read_text(encoding="utf-8")
        expected = "group: ai-usecase-feed-production"
        self.assertIn(expected, daily)
        self.assertIn(expected, discovery)
        self.assertIn("cancel-in-progress: false", discovery)

    def test_daily_persists_health_and_registry_only_after_pipeline(self) -> None:
        text = DAILY.read_text(encoding="utf-8")
        pipeline = text.index("Collect, process, and build")
        state_add = text.index(
            "git add data/source_registry.json data/source_health.json"
        )
        self.assertLess(pipeline, state_add)


if __name__ == "__main__":
    unittest.main()
