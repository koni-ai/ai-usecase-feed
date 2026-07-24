from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import collect
import source_manager


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=source_manager.SEOUL)


def public_resolver(host: str, port: int, **_kwargs):
    return [
        (
            source_manager.socket.AF_INET,
            source_manager.socket.SOCK_STREAM,
            6,
            "",
            ("93.184.216.34", port),
        )
    ]


def rss_payload(
    count: int = 10,
    *,
    host: str = "example.com",
    title_prefix: str = "How I built an AI workflow",
) -> bytes:
    rows = []
    for index in range(count):
        published = (
            NOW.astimezone(timezone.utc) - timedelta(days=index)
        ).isoformat()
        rows.append(
            "<item>"
            f"<title>{title_prefix} {index}</title>"
            f"<link>https://{host}/post/{index}</link>"
            "<description>Using Claude automation in production.</description>"
            f"<pubDate>{published}</pubDate>"
            "</item>"
        )
    return (
        "<?xml version='1.0'?><rss><channel>"
        f"<title>{host} AI cases</title>{''.join(rows)}"
        "</channel></rss>"
    ).encode("utf-8")


def empty_registry() -> dict:
    return {
        "schema_version": 1,
        "updated_at": None,
        "sources": [],
        "discovery_runs": [],
    }


def empty_health() -> dict:
    return {"schema_version": 1, "updated_at": None, "sources": {}}


class SourceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=temp_root,
            ignore_cleanup_errors=True,
        )
        self.root = Path(self.temp_dir.name)
        self.registry_path = self.root / "source_registry.json"
        self.health_path = self.root / "source_health.json"
        self.registry_path.write_text(
            json.dumps(empty_registry()),
            encoding="utf-8",
        )
        self.health_path.write_text(
            json.dumps(empty_health()),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_registry(self, sources: list[dict]) -> None:
        registry = empty_registry()
        registry["sources"] = sources
        self.registry_path.write_text(
            json.dumps(registry),
            encoding="utf-8",
        )

    def read_registry(self) -> dict:
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def test_public_https_validation_rejects_ssrf_shapes_and_private_dns(self) -> None:
        self.assertEqual(
            "https://example.com/feed",
            source_manager.validate_public_https_url(
                "https://Example.com/feed",
                resolver=public_resolver,
            ),
        )
        bad_urls = (
            "http://example.com/feed",
            "https://user:pass@example.com/feed",
            "https://example.com:8443/feed",
            "https://127.0.0.1/feed",
        )
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(
                source_manager.UnsafeURLError
            ):
                source_manager.validate_public_https_url(
                    url,
                    resolver=public_resolver,
                )
        with self.assertRaises(source_manager.UnsafeURLError):
            source_manager.validate_public_https_url(
                "https://example.com/feed",
                resolver=lambda *_args, **_kwargs: [
                    (2, 1, 6, "", ("10.0.0.5", 443))
                ],
            )

    def test_html_autodiscovery_accepts_only_declared_rss_or_atom(self) -> None:
        html = b"""
        <html><head><title>Example Lab</title>
        <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        <link rel="stylesheet" href="/fake.xml">
        <link rel="alternate" type="application/json" href="/feed.json">
        </head></html>
        """
        links, title = source_manager.discover_feed_links(
            html, "https://example.com/article"
        )
        self.assertEqual(["https://example.com/feed.xml"], links)
        self.assertEqual("Example Lab", title)

    def test_feed_inspection_enforces_xml_defense_freshness_and_signal(self) -> None:
        result = source_manager.inspect_feed(rss_payload(), NOW)
        self.assertEqual(10, result["sample_item_count"])
        self.assertEqual(10, result["recent_item_count"])
        self.assertEqual(10, result["signal_item_count"])
        self.assertEqual(1.0, result["signal_ratio"])
        with self.assertRaises(source_manager.FetchError):
            source_manager.inspect_feed(
                b"<!DOCTYPE rss [<!ENTITY x 'boom'>]><rss>&x;</rss>",
                NOW,
            )

    def test_corrupt_registry_fails_closed_without_overwriting(self) -> None:
        self.registry_path.write_text("{broken", encoding="utf-8")
        before = self.registry_path.read_bytes()
        with self.assertRaises(source_manager.StateIntegrityError):
            source_manager.update_health(
                self.health_path,
                self.registry_path,
                [{"name": "A", "status": "success"}],
                NOW,
            )
        self.assertEqual(before, self.registry_path.read_bytes())

    def test_health_warns_at_three_pauses_at_five_and_bounds_history(self) -> None:
        stat = {
            "name": "Static",
            "status": "error",
            "fetched": 0,
            "selected": 0,
            "duplicate": 0,
            "error": "forced",
        }
        for offset in range(3):
            source_manager.update_health(
                self.health_path,
                self.registry_path,
                [stat],
                NOW + timedelta(days=offset),
            )
        health = source_manager.load_health(self.health_path)
        self.assertEqual("warning", health["sources"]["Static"]["status"])
        for offset in range(3, 35):
            source_manager.update_health(
                self.health_path,
                self.registry_path,
                [stat],
                NOW + timedelta(days=offset),
            )
        health = source_manager.load_health(self.health_path)
        state = health["sources"]["Static"]
        self.assertEqual("paused", state["status"])
        self.assertEqual(30, len(state["history"]))

    def test_dynamic_source_is_paused_in_registry_after_five_failures(self) -> None:
        self.write_registry(
            [
                {
                    "id": "dynamic-1",
                    "name": "Dynamic",
                    "type": "rss",
                    "endpoint": "https://dynamic.example/feed",
                    "homepage": "https://dynamic.example/",
                    "site_host": "dynamic.example",
                    "status": "active",
                }
            ]
        )
        stat = {
            "name": "Dynamic",
            "status": "error",
            "fetched": 0,
            "selected": 0,
            "duplicate": 0,
            "error": "forced",
        }
        for offset in range(5):
            source_manager.update_health(
                self.health_path,
                self.registry_path,
                [stat],
                NOW + timedelta(days=offset),
            )
        self.assertEqual(
            "paused",
            self.read_registry()["sources"][0]["status"],
        )

    def test_runtime_sources_merges_active_dynamic_and_skips_paused_static(self) -> None:
        self.write_registry(
            [
                {
                    "id": "dynamic-1",
                    "name": "Dynamic",
                    "type": "rss",
                    "endpoint": "https://dynamic.example/feed",
                    "homepage": "https://dynamic.example/",
                    "site_host": "dynamic.example",
                    "status": "active",
                }
            ]
        )
        self.health_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": None,
                    "sources": {
                        "Paused": {"status": "paused", "history": []},
                    },
                }
            ),
            encoding="utf-8",
        )
        result = source_manager.runtime_sources(
            [
                {
                    "name": "Healthy",
                    "type": "rss",
                    "endpoint": "https://healthy.example/feed",
                },
                {
                    "name": "Paused",
                    "type": "rss",
                    "endpoint": "https://paused.example/feed",
                },
            ],
            self.registry_path,
            self.health_path,
        )
        self.assertEqual(["Healthy", "Dynamic"], [row["name"] for row in result])
        self.assertTrue(result[1]["dynamic"])

    def test_probation_requires_two_checks_at_least_seven_days_apart(self) -> None:
        self.write_registry(
            [
                {
                    "id": "candidate-1",
                    "name": "Candidate",
                    "type": "rss",
                    "endpoint": "https://candidate.example/feed",
                    "homepage": "https://candidate.example/",
                    "site_host": "candidate.example",
                    "status": "probation",
                    "successful_checks": 1,
                    "consecutive_failures": 0,
                    "success_dates": ["2026-07-17"],
                }
            ]
        )

        def fetch(url: str, _accept: str, _limit: int):
            if "api.example" in url:
                return b'{"hits":[]}', url, "application/json"
            return rss_payload(host="candidate.example"), url, "application/rss+xml"

        result = source_manager.discover_once(
            indexes=[
                {
                    "name": "Fixture HN",
                    "type": "hn_algolia",
                    "endpoint": "https://api.example/search",
                }
            ],
            static_sources=[],
            registry_path=self.registry_path,
            health_path=self.health_path,
            fetch=fetch,
            now=NOW,
            max_pages=1,
        )
        self.assertEqual(["Candidate"], result["promoted"])
        self.assertEqual("active", self.read_registry()["sources"][0]["status"])

    def test_new_candidate_stays_probation_and_does_not_enter_runtime_sources(self) -> None:
        index_payload = json.dumps(
            {"hits": [{"url": "https://new.example/story"}]}
        ).encode()
        html = (
            b'<html><head><title>New Example</title>'
            b'<link rel="alternate" type="application/rss+xml" href="/feed">'
            b"</head></html>"
        )

        def fetch(url: str, _accept: str, _limit: int):
            if "api.example" in url:
                return index_payload, url, "application/json"
            if url.endswith("/story"):
                return html, url, "text/html"
            if url.endswith("/feed"):
                return rss_payload(host="new.example"), url, "application/rss+xml"
            raise AssertionError(url)

        result = source_manager.discover_once(
            indexes=[
                {
                    "name": "Fixture HN",
                    "type": "hn_algolia",
                    "endpoint": "https://api.example/search",
                }
            ],
            static_sources=[
                {
                    "name": "Static",
                    "type": "rss",
                    "endpoint": "https://static.example/feed",
                }
            ],
            registry_path=self.registry_path,
            health_path=self.health_path,
            fetch=fetch,
            now=NOW,
            max_pages=1,
        )
        self.assertEqual(1, len(result["discovered"]))
        self.assertEqual("probation", self.read_registry()["sources"][0]["status"])
        runtime = source_manager.runtime_sources(
            [
                {
                    "name": "Static",
                    "type": "rss",
                    "endpoint": "https://static.example/feed",
                }
            ],
            self.registry_path,
            self.health_path,
        )
        self.assertEqual(["Static"], [source["name"] for source in runtime])

    def test_active_capacity_keeps_fourth_qualified_source_waiting(self) -> None:
        sources = []
        for index in range(3):
            sources.append(
                {
                    "id": f"active-{index}",
                    "name": f"Active {index}",
                    "type": "rss",
                    "endpoint": f"https://active{index}.example/feed",
                    "homepage": f"https://active{index}.example/",
                    "site_host": f"active{index}.example",
                    "status": "active",
                }
            )
        sources.append(
            {
                "id": "waiting",
                "name": "Waiting",
                "type": "rss",
                "endpoint": "https://waiting.example/feed",
                "homepage": "https://waiting.example/",
                "site_host": "waiting.example",
                "status": "probation",
                "success_dates": ["2026-07-17"],
            }
        )
        self.write_registry(sources)

        def fetch(url: str, _accept: str, _limit: int):
            if "api.example" in url:
                return b'{"hits":[]}', url, "application/json"
            return rss_payload(host="waiting.example"), url, "application/rss+xml"

        source_manager.discover_once(
            indexes=[
                {
                    "name": "Fixture HN",
                    "type": "hn_algolia",
                    "endpoint": "https://api.example/search",
                }
            ],
            static_sources=[],
            registry_path=self.registry_path,
            health_path=self.health_path,
            fetch=fetch,
            now=NOW,
            max_pages=1,
        )
        waiting = self.read_registry()["sources"][-1]
        self.assertEqual("probation", waiting["status"])
        self.assertEqual(
            "qualified_waiting_for_active_slot",
            waiting["status_reason"],
        )

    def test_collector_uses_freshness_round_robin_without_reducing_capacity(self) -> None:
        data_dir = self.root / "collector-data"
        sources = [
            {
                "name": "A",
                "type": "rss",
                "endpoint": "https://a.example/feed",
            },
            {
                "name": "B",
                "type": "rss",
                "endpoint": "https://b.example/feed",
            },
            {
                "name": "C",
                "type": "rss",
                "endpoint": "https://c.example/feed",
            },
        ]

        def fetch(source: dict, _now: datetime) -> bytes:
            host = f"{source['name'].lower()}.example"
            counts = {"A": 40, "B": 4, "C": 2}
            return rss_payload(counts[source["name"]], host=host)

        result = collect.collect_once(sources, data_dir, fetch, NOW, 30)
        self.assertEqual(30, result["selected_count"])
        selected = {row["name"]: row["selected"] for row in result["sources"]}
        self.assertEqual(4, selected["B"])
        self.assertEqual(2, selected["C"])
        self.assertEqual(24, selected["A"])
        raw = json.loads(
            (data_dir / "raw" / "2026-07-24.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "freshness_round_robin",
            raw["runs"][0]["selection_strategy"],
        )


if __name__ == "__main__":
    unittest.main()
