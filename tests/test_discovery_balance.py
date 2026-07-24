from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import source_manager


class DiscoveryBalanceTests(unittest.TestCase):
    def test_page_budget_is_round_robin_across_discovery_indexes(self) -> None:
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=temp_root,
            ignore_cleanup_errors=True,
        ) as directory:
            root = Path(directory)
            registry_path = root / "registry.json"
            health_path = root / "health.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": None,
                        "sources": [],
                        "discovery_runs": [],
                    }
                ),
                encoding="utf-8",
            )
            health_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": None,
                        "sources": {},
                    }
                ),
                encoding="utf-8",
            )
            visited_pages: list[str] = []

            def fetch(url: str, _accept: str, _limit: int):
                if url == "https://index-a.example/search":
                    payload = {
                        "hits": [
                            {"url": f"https://a{index}.example/story"}
                            for index in range(5)
                        ]
                    }
                    return json.dumps(payload).encode(), url, "application/json"
                if url == "https://index-b.example/search":
                    payload = {
                        "hits": [
                            {"url": f"https://b{index}.example/story"}
                            for index in range(5)
                        ]
                    }
                    return json.dumps(payload).encode(), url, "application/json"
                visited_pages.append(url)
                return b"<html><head><title>No feed</title></head></html>", url, "text/html"

            result = source_manager.discover_once(
                indexes=[
                    {
                        "name": "A",
                        "type": "hn_algolia",
                        "endpoint": "https://index-a.example/search",
                    },
                    {
                        "name": "B",
                        "type": "hn_algolia",
                        "endpoint": "https://index-b.example/search",
                    },
                ],
                static_sources=[],
                registry_path=registry_path,
                health_path=health_path,
                fetch=fetch,
                now=datetime(2026, 7, 24, tzinfo=source_manager.SEOUL),
                max_pages=4,
            )
            self.assertEqual("success", result["status"])
            self.assertEqual(
                [
                    "https://a0.example/story",
                    "https://b0.example/story",
                    "https://a1.example/story",
                    "https://b1.example/story",
                ],
                visited_pages,
            )


if __name__ == "__main__":
    unittest.main()
