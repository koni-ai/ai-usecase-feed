from __future__ import annotations

import unittest

import source_manager


class RegistryBoundTests(unittest.TestCase):
    def test_retired_sources_are_compacted_but_hosts_remain_blocked(self) -> None:
        registry = {
            "schema_version": 1,
            "updated_at": None,
            "sources": [
                {
                    "id": f"retired-{index}",
                    "name": f"Retired {index}",
                    "type": "rss",
                    "endpoint": f"https://retired{index}.example/feed",
                    "homepage": f"https://retired{index}.example/",
                    "site_host": f"retired{index}.example",
                    "status": "retired",
                    "last_checked_at": f"2026-01-{(index % 28) + 1:02d}",
                }
                for index in range(101)
            ],
            "retired_hosts": [],
            "discovery_runs": [],
        }
        source_manager._compact_registry(registry)
        self.assertEqual(source_manager.MAX_REGISTRY_SOURCES, len(registry["sources"]))
        self.assertEqual(1, len(registry["retired_hosts"]))
        self.assertTrue(registry["retired_hosts"][0].endswith(".example"))


if __name__ == "__main__":
    unittest.main()
