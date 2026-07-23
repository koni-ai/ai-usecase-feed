from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import build


def card(
    card_id: str = "2026-07-23-001",
    title: str = "AI로 보고서를 자동화한 사례",
    tool: str = "Claude",
    domain: str = "업무자동화",
) -> dict:
    return {
        "id": card_id,
        "title": title,
        "summary": "Claude로 보고서 작성을 자동화했다. 반복 시간이 줄었다.",
        "tool": [tool],
        "domain": domain,
        "difficulty": "쉬움",
        "actionable": True,
        "source_url": f"https://example.com/{card_id}",
        "source_name": "Fixture",
        "collected_at": card_id[:10],
    }


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=temp_root,
            ignore_cleanup_errors=True,
        )
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_site_embeds_cases_and_reports_counts(self) -> None:
        cases_path = self.root / "data" / "cases.json"
        output_path = self.root / "site" / "index.html"
        cases_path.parent.mkdir(parents=True)
        cases_path.write_text(
            json.dumps(
                [
                    card("2026-07-23-001", tool="Claude", domain="개발"),
                    card("2026-07-23-002", tool="Codex", domain="리서치"),
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = build.build_site(cases_path, output_path)
        html = output_path.read_text(encoding="utf-8")
        self.assertEqual(2, result["cases"])
        self.assertEqual(2, result["tools"])
        self.assertEqual(2, result["domains"])
        self.assertIn('<script id="caseData" type="application/json">', html)
        self.assertIn("2026-07-23-002", html)
        self.assertTrue(result["self_contained"])

    def test_html_is_self_contained_and_file_protocol_safe(self) -> None:
        html = build.render_html([card()])
        self.assertNotIn("fetch(", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertNotIn("http://", html)
        self.assertIn("localStorage", html)
        self.assertIn("ai-usecase-feed:bookmarks:v1", html)
        self.assertIn("ai-usecase-feed:read:v1", html)

    def test_inline_json_escapes_script_termination(self) -> None:
        hostile = card(title="AI 사례 </script><script>alert(1)</script>")
        html = build.render_html([hostile])
        data_start = html.index('<script id="caseData"')
        data_end = html.index("</script>", data_start)
        data_block = html[data_start:data_end]
        self.assertNotIn("</script>", data_block)
        self.assertIn("\\u003c/script\\u003e", data_block)

    def test_mobile_width_touch_target_filters_and_selftest_exist(self) -> None:
        html = build.render_html([card()])
        self.assertIn("width: min(640px, 100%)", html)
        self.assertIn("min-height: 44px", html)
        self.assertIn('id="toolFilter"', html)
        self.assertIn('id="domainFilter"', html)
        self.assertIn('id="actionableOnly"', html)
        self.assertIn('id="bookmarkedOnly"', html)
        self.assertIn('document.body.dataset.selftest = "PASS"', html)
        self.assertIn("window.__feedTestApi", html)

    def test_latest_case_is_embedded_first(self) -> None:
        older = card("2026-07-22-001")
        newer = card("2026-07-23-003")
        html = build.render_html([older, newer])
        data_text = html.split(
            '<script id="caseData" type="application/json">', 1
        )[1].split("</script>", 1)[0]
        embedded = json.loads(data_text)
        self.assertEqual("2026-07-23-003", embedded[0]["id"])


if __name__ == "__main__":
    unittest.main()
