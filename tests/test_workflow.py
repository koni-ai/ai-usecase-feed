from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_DIR / ".github" / "workflows" / "daily-feed.yml"


class HostedWorkflowTests(unittest.TestCase):
    def test_zero_incremental_cost_auth_and_schedule(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "0 7 * * *"', text)
        self.assertIn('timezone: "Asia/Seoul"', text)
        self.assertIn(
            "CLAUDE_CODE_OAUTH_TOKEN: "
            "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
            text,
        )
        self.assertNotIn("secrets.ANTHROPIC_API_KEY", text)
        self.assertIn("Reject metered API routing", text)
        self.assertIn('@anthropic-ai/claude-code@2.1.218', text)
        self.assertIn('case "${CLAUDE_CODE_OAUTH_TOKEN}" in', text)
        self.assertIn("sk-ant-oat01-*", text)
        self.assertNotIn('d.get("authMethod")', text)
        self.assertNotIn('d.get("subscriptionType")', text)

    def test_failure_cannot_replace_last_successful_pages_version(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        pipeline = text.index("Collect, process, and build")
        persist = text.index("Persist successful state")
        deploy = text.index("Deploy GitHub Pages")
        self.assertLess(pipeline, persist)
        self.assertLess(persist, deploy)
        self.assertNotIn("continue-on-error: true", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_only_intended_runtime_state_is_committed(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "git add data/seen.json data/process_state.json "
            "data/cases.json data/raw site/index.html",
            text,
        )
        self.assertNotIn("git add .", text)
        self.assertNotIn("logs/", text)

    def test_pages_uses_official_artifact_deployment(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("actions/configure-pages@v5", text)
        self.assertIn("actions/upload-pages-artifact@v3", text)
        self.assertIn("actions/deploy-pages@v4", text)
        self.assertIn("pages: write", text)
        self.assertIn("id-token: write", text)


if __name__ == "__main__":
    unittest.main()
