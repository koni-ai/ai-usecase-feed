from __future__ import annotations

import datetime as dt
import importlib.util
import json
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_DIR / ".github" / "workflows" / "samgukji-daily.yml"
GENERATOR = PROJECT_DIR / "samgukji" / "generate_daily.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("samgukji_generate_daily", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class SamgukjiDailyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_generator()
        cls.feed = json.loads((PROJECT_DIR / "samgukji" / "data" / "feed.json").read_text(encoding="utf-8"))
        cls.queue = json.loads((PROJECT_DIR / "samgukji" / "data" / "seed_queue.json").read_text(encoding="utf-8"))
        cls.curriculum = json.loads((PROJECT_DIR / "samgukji" / "data" / "curriculum.json").read_text(encoding="utf-8"))

    def test_schedule_replaces_habitus_at_0800_kst(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "0 23 * * *"', text)
        self.assertIn("Daily Samgukji 365", text)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", text)
        self.assertNotIn("secrets.ANTHROPIC_API_KEY", text)
        self.assertIn("Reject metered API routing", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_failure_cannot_replace_last_good_feed(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        generate = text.index("Publish exactly one due chapter")
        validate = text.index("Validate publication artifact")
        persist = text.index("Persist successful state")
        deploy = text.index("Deploy GitHub Pages content endpoint")
        self.assertLess(generate, validate)
        self.assertLess(validate, persist)
        self.assertLess(persist, deploy)
        self.assertNotIn("continue-on-error: true", text)
        self.assertNotIn("git add .", text)

    def test_seed_and_curriculum_are_complete(self) -> None:
        self.assertEqual([item["day"] for item in self.queue["items"]], list(range(1, 15)))
        self.assertEqual([item["day"] for item in self.curriculum["days"]], list(range(1, 366)))
        self.assertEqual(len(self.feed["items"]), 1)
        self.assertEqual(self.feed["items"][0]["day"], 1)

    def test_one_due_day_only(self) -> None:
        next_day = self.module.next_day_for_date
        self.assertIsNone(next_day(self.feed, dt.date(2026, 8, 31)))
        self.assertIsNone(next_day(self.feed, dt.date(2026, 9, 1)))
        self.assertEqual(next_day(self.feed, dt.date(2026, 9, 2)), 2)
        self.assertEqual(next_day(self.feed, dt.date(2026, 9, 30)), 2)

    def test_feed_and_public_copy_validate(self) -> None:
        self.module.validate_feed(self.feed)
        public = (PROJECT_DIR / "site" / "samgukji" / "feed.json").read_bytes()
        private = (PROJECT_DIR / "samgukji" / "data" / "feed.json").read_bytes()
        self.assertEqual(public, private)


if __name__ == "__main__":
    unittest.main()
