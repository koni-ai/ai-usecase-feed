from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import collect
import source_manager


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=source_manager.SEOUL)


def qualifying_feed() -> bytes:
    items = []
    for index in range(10):
        items.append(
            "<item>"
            f"<title>How I built an AI workflow {index}</title>"
            f"<link>https://dynamic.example/{index}</link>"
            "<description>Using Claude automation in production.</description>"
            "<pubDate>2026-07-24T00:00:00+00:00</pubDate>"
            "</item>"
        )
    return (
        "<?xml version='1.0'?><rss><channel><title>Dynamic Cases</title>"
        + "".join(items)
        + "</channel></rss>"
    ).encode()


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=temp_root,
            ignore_cleanup_errors=True,
        )
        root = Path(self.temp_dir.name)
        self.registry_path = root / "registry.json"
        self.health_path = root / "health.json"
        self.registry_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": None,
                    "sources": [
                        {
                            "id": "dynamic",
                            "name": "Dynamic",
                            "type": "rss",
                            "endpoint": "https://dynamic.example/feed",
                            "homepage": "https://dynamic.example/",
                            "site_host": "dynamic.example",
                            "status": "paused",
                            "status_reason": "daily_fetch_failed_5_times",
                            "success_dates": ["2026-07-17"],
                        }
                    ],
                    "retired_hosts": [],
                    "discovery_runs": [],
                }
            ),
            encoding="utf-8",
        )
        self.health_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": None,
                    "sources": {
                        "Dynamic": {
                            "kind": "dynamic",
                            "status": "paused",
                            "consecutive_failures": 5,
                            "history": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_paused_dynamic_probe_recovers_health_and_runtime_membership(self) -> None:
        def fetch(url: str, _accept: str, _limit: int):
            if url == "https://index.example/search":
                return b'{"hits":[]}', url, "application/json"
            return qualifying_feed(), url, "application/rss+xml"

        result = source_manager.discover_once(
            indexes=[
                {
                    "name": "Fixture",
                    "type": "hn_algolia",
                    "endpoint": "https://index.example/search",
                }
            ],
            static_sources=[],
            registry_path=self.registry_path,
            health_path=self.health_path,
            fetch=fetch,
            now=NOW,
            max_pages=1,
        )
        self.assertEqual(["Dynamic"], result["promoted"])
        registry = source_manager.load_registry(self.registry_path)
        health = source_manager.load_health(self.health_path)
        self.assertEqual("active", registry["sources"][0]["status"])
        self.assertEqual("healthy", health["sources"]["Dynamic"]["status"])
        self.assertEqual(0, health["sources"]["Dynamic"]["consecutive_failures"])
        runtime = source_manager.runtime_sources(
            [],
            self.registry_path,
            self.health_path,
        )
        self.assertEqual(["Dynamic"], [source["name"] for source in runtime])

    def test_candidate_name_falls_back_when_feed_title_is_empty(self) -> None:
        candidate = source_manager._new_candidate(
            "https://new.example/feed",
            "https://new.example/story",
            "Page Title",
            "Fixture",
            {
                "name": "",
                "sample_item_count": 10,
                "recent_item_count": 10,
                "signal_item_count": 10,
                "signal_ratio": 1.0,
            },
            NOW,
        )
        self.assertEqual("Page Title", candidate["name"])

    def test_daily_rss_parser_rejects_padded_doctype(self) -> None:
        payload = (
            b" " * 5000
            + b"<!DOCTYPE rss [<!ENTITY x 'boom'>]>"
            + b"<rss><channel><item><title>&x;</title></item></channel></rss>"
        )
        with self.assertRaisesRegex(ValueError, "DOCTYPE/ENTITY"):
            collect.parse_rss(
                payload,
                {
                    "name": "Dynamic",
                    "type": "rss",
                    "endpoint": "https://dynamic.example/feed",
                },
                NOW,
            )


if __name__ == "__main__":
    unittest.main()
