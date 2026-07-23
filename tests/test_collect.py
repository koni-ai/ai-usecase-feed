from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import collect


NOW = datetime(2026, 7, 23, 7, 0, tzinfo=collect.SEOUL)


def rss_payload(count: int, prefix: str = "Built workflow") -> bytes:
    items = []
    for index in range(count):
        published = (NOW.astimezone(timezone.utc) - timedelta(minutes=index)).isoformat()
        items.append(
            "<item>"
            f"<title>{prefix} {index}</title>"
            f"<link>https://example.com/post/{index}?utm_source=test</link>"
            f"<description>Example {index}</description>"
            f"<pubDate>{published}</pubDate>"
            "</item>"
        )
    return ("<?xml version='1.0'?><rss><channel>" + "".join(items) + "</channel></rss>").encode(
        "utf-8"
    )


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=temp_root,
            ignore_cleanup_errors=True,
        )
        self.data_dir = Path(self.temp_dir.name) / "data"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sources_yaml_has_expected_five_sources(self) -> None:
        sources = collect.load_sources(Path(__file__).parents[1] / "sources.yaml")
        self.assertEqual(5, len(sources))
        self.assertEqual(
            {"rss", "hn_algolia", "reddit_json"},
            {source["type"] for source in sources},
        )

    def test_config_rejects_unknown_type_and_non_https(self) -> None:
        bad_config = Path(self.temp_dir.name) / "bad.yaml"
        bad_config.write_text(
            "sources:\n"
            "  - name: bad\n"
            "    type: scraper\n"
            "    endpoint: http://example.com/feed\n",
            encoding="utf-8",
        )
        with self.assertRaises(collect.ConfigError):
            collect.load_sources(bad_config)

    def test_url_canonicalization_and_fallback(self) -> None:
        self.assertEqual(
            "https://example.com/Path?a=1&b=2",
            collect.canonicalize_url(
                "HTTPS://Example.COM:443/Path/?utm_source=x&b=2&a=1#section"
            ),
        )
        self.assertEqual(
            "https://news.ycombinator.com/item?id=1",
            collect.canonicalize_url(
                "", "https://news.ycombinator.com/item?id=1&utm_medium=test"
            ),
        )

    def test_title_filter_prefers_use_case_and_rejects_plain_news(self) -> None:
        self.assertEqual(
            (True, "preferred_keyword"),
            collect.title_passes_first_filter("How I automated research with Claude"),
        )
        self.assertEqual(
            (False, "excluded_category"),
            collect.title_passes_first_filter("Company raises $30M Series B funding"),
        )
        self.assertEqual(
            (True, "ambiguous_pass"),
            collect.title_passes_first_filter("A practical field report"),
        )

    def test_hard_limit_is_deterministic_and_second_run_is_duplicate_free(self) -> None:
        sources = [
            {
                "name": "Fixture RSS",
                "type": "rss",
                "endpoint": "https://example.com/feed",
            }
        ]

        def fetch(_source: dict, _now: datetime) -> bytes:
            return rss_payload(40)

        first = collect.collect_once(sources, self.data_dir, fetch, NOW, 30)
        second = collect.collect_once(sources, self.data_dir, fetch, NOW, 30)
        self.assertEqual(30, first["selected_count"])
        self.assertEqual(0, second["selected_count"])

        raw_path = self.data_dir / "raw" / "2026-07-23.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        seen = json.loads((self.data_dir / "seen.json").read_text(encoding="utf-8"))
        self.assertEqual(30, len(raw["items"]))
        self.assertEqual(2, len(raw["runs"]))
        self.assertEqual(30, len(seen))
        self.assertEqual(
            list(range(30)),
            [
                int(item["url"].rsplit("/", 1)[-1])
                for item in raw["items"]
            ],
        )

    def test_fetch_parse_normalize_failure_is_isolated(self) -> None:
        sources = [
            {
                "name": "Broken",
                "type": "rss",
                "endpoint": "https://invalid.invalid/feed",
            },
            {
                "name": "Working",
                "type": "rss",
                "endpoint": "https://example.com/feed",
            },
        ]

        def fetch(source: dict, _now: datetime) -> bytes:
            if source["name"] == "Broken":
                raise TimeoutError("forced failure")
            return rss_payload(1)

        result = collect.collect_once(sources, self.data_dir, fetch, NOW, 30)
        self.assertEqual("success", result["status"])
        self.assertEqual(1, result["successful_sources"])
        self.assertEqual(1, result["failed_sources"])
        self.assertEqual("error", result["sources"][0]["status"])
        self.assertEqual(1, result["selected_count"])

    def test_malformed_payload_is_isolated_like_request_failure(self) -> None:
        sources = [
            {
                "name": "Malformed",
                "type": "rss",
                "endpoint": "https://example.com/bad",
            },
            {
                "name": "Working",
                "type": "rss",
                "endpoint": "https://example.com/good",
            },
        ]

        def fetch(source: dict, _now: datetime) -> bytes:
            return b"<not-xml" if source["name"] == "Malformed" else rss_payload(1)

        result = collect.collect_once(sources, self.data_dir, fetch, NOW, 30)
        self.assertEqual(1, result["selected_count"])
        self.assertEqual("error", result["sources"][0]["status"])
        self.assertEqual("success", result["sources"][1]["status"])

    def test_reddit_403_uses_explicit_rss_fallback(self) -> None:
        sources = [
            {
                "name": "Reddit Fixture",
                "type": "reddit_json",
                "endpoint": "https://www.reddit.com/r/test/top.json?t=day&limit=25",
                "fallback": {
                    "type": "rss",
                    "endpoint": "https://www.reddit.com/r/test/top/.rss?t=day",
                },
            }
        ]

        def fetch(source: dict, _now: datetime) -> bytes:
            if source["type"] == "reddit_json":
                error = HTTPError(source["endpoint"], 403, "Blocked", None, None)
                error.close()
                raise error
            return rss_payload(1)

        result = collect.collect_once(sources, self.data_dir, fetch, NOW, 30)
        self.assertEqual(1, result["selected_count"])
        self.assertTrue(result["sources"][0]["fallback_used"])
        self.assertEqual("rss", result["sources"][0]["fallback_type"])

    def test_corrupt_seen_stops_without_modifying_original(self) -> None:
        self.data_dir.mkdir(parents=True)
        seen_path = self.data_dir / "seen.json"
        seen_path.write_text("{broken", encoding="utf-8")
        before = seen_path.read_bytes()
        sources = [
            {
                "name": "Fixture",
                "type": "rss",
                "endpoint": "https://example.com/feed",
            }
        ]
        with self.assertRaises(collect.DataIntegrityError):
            collect.collect_once(
                sources,
                self.data_dir,
                lambda _source, _now: rss_payload(1),
                NOW,
                30,
            )
        self.assertEqual(before, seen_path.read_bytes())

    def test_all_sources_failed_returns_failed_but_writes_diagnostics(self) -> None:
        sources = [
            {
                "name": "Broken",
                "type": "rss",
                "endpoint": "https://invalid.invalid/feed",
            }
        ]

        def fetch(_source: dict, _now: datetime) -> bytes:
            raise ConnectionError("forced failure")

        with patch("collect.datetime") as datetime_mock:
            datetime_mock.now.return_value = NOW
            result = collect.collect_once(sources, self.data_dir, fetch, NOW, 30)
        self.assertEqual("failed", result["status"])
        raw = json.loads(
            (self.data_dir / "raw" / "2026-07-23.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, raw["runs"][0]["failed_sources"])
        self.assertEqual([], raw["items"])


if __name__ == "__main__":
    unittest.main()
