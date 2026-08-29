from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA_DIR = ROOT / "data"
FEED_PATH = DATA_DIR / "feed.json"
QUEUE_PATH = DATA_DIR / "seed_queue.json"
CURRICULUM_PATH = DATA_DIR / "curriculum.json"
CATALOG_PATH = DATA_DIR / "source_catalog.json"
RULES_PATH = ROOT / "AUTOMATION_RULES.md"
PUBLIC_FEED_PATH = REPO / "site" / "samgukji" / "feed.json"
PUBLIC_IMAGE_DIR = REPO / "site" / "samgukji" / "images"
PUBLIC_BASE = "https://koni-ai.github.io/ai-usecase-feed/samgukji"
START_DATE = dt.date(2026, 9, 1)
SEOUL = ZoneInfo("Asia/Seoul")
MODEL = "claude-sonnet-5"
PAID_ENV = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}
ALLOWED_SOURCE_HOSTS = {
    "ctext.org",
    "zh.wikisource.org",
    "en.wikisource.org",
    "www.gutenberg.org",
    "gutenberg.org",
    "commons.wikimedia.org",
    "en.wikipedia.org",
    "zh.wikipedia.org",
    "plato.stanford.edu",
    "www.nobelprize.org",
    "nobelprize.org",
    "openresearch-repository.anu.edu.au",
    "www.britannica.com",
}


def string(minimum: int = 1) -> dict:
    return {"type": "string", "minLength": minimum}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or list(properties),
    }


def arr(items: dict, minimum: int = 1, maximum: int | None = None) -> dict:
    value = {"type": "array", "items": items, "minItems": minimum}
    if maximum is not None:
        value["maxItems"] = maximum
    return value


nullable_string = {"anyOf": [string(), {"type": "null"}]}
recall_schema = obj({"from_day": {"type": "integer"}, "prompt": string(15)})
anchor_schema = obj({"type": string(), "name": string(), "plain": string(10)})
story_schema = obj(
    {
        "title": string(2),
        "body": string(150),
        "claim_tags": arr(
            {"type": "string", "enum": ["HISTORY", "ROMANCE", "INTERPRETATION", "APPLICATION", "CAVEAT", "RECONSTRUCTION"]},
            1,
            2,
        ),
    }
)
option_schema = obj({"id": {"type": "string", "enum": ["a", "b"]}, "label": string(4), "temptation": string(8), "risk": string(8)})
record_schema = obj(
    {
        "label": {"type": "string", "enum": ["HISTORY", "ROMANCE"]},
        "text": string(25),
        "source_ids": arr(string(), 1, 5),
    }
)
evidence_schema = obj(
    {
        "role": string(2),
        "question": string(10),
        "use": string(20),
        "source_ids": arr(string(), 1, 5),
    }
)
item_schema = obj(
    {
        "schema_version": {"type": "integer", "enum": [1]},
        "format_version": {"type": "integer", "enum": [2]},
        "day": {"type": "integer", "minimum": 15, "maximum": 365},
        "event_id": string(),
        "published_at": string(10),
        "read_minutes": {"type": "integer", "minimum": 8, "maximum": 12},
        "balance": obj({"story_minutes": {"type": "integer", "enum": [5]}, "life_minutes": {"type": "integer", "enum": [5]}}),
        "narrative_anchor": obj(
            {
                "work": {"type": "string", "enum": ["삼국연의"]},
                "chapter": {"type": "integer", "minimum": 1, "maximum": 120},
                "chapter_title": string(2),
                "url": string(12),
                "chronological": {"type": "boolean", "enum": [True]},
            }
        ),
        "title": string(5),
        "cold_open": string(45),
        "yesterday_recall": recall_schema,
        "spaced_recall": {"anyOf": [recall_schema, {"type": "null"}]},
        "situation_board": obj(
            {
                "era": string(),
                "places": arr(string(), 1, 3),
                "factions": arr(string(), 2, 4),
                "one_line": string(25),
                "image_id": string(),
            }
        ),
        "orientation": obj(
            {
                "year": string(),
                "era": string(),
                "state": string(),
                "state_plain": string(10),
                "capital": string(),
                "current_marker": string(5),
                "full_path": arr(string(), 4, 7),
            }
        ),
        "memory_lock": obj({"anchors": arr(anchor_schema, 3, 3), "speak_prompt": string(15)}),
        "story_sections": arr(story_schema, 5, 5),
        "decision": obj({"prompt": string(20), "options": arr(option_schema, 2, 2), "historical_choice": string(35)}),
        "consequences": obj({"gained": string(8), "lost": string(8), "returned_later": string(12)}),
        "hidden_board": obj(
            {
                "primary_lens": {"type": "string", "enum": ["BIZ", "DARK", "DRIVE", "BEHAVIOR", "SYSTEM"]},
                "secondary_lens": nullable_string,
                "visible_event": string(20),
                "hidden_desire": string(10),
                "hidden_mechanism": string(15),
                "modern_signal": string(15),
                "defense": string(15),
                "caveat": string(35),
            }
        ),
        "record_check": arr(record_schema, 2, 3),
        "evidence_chain": arr(evidence_schema, 3, 5),
        "strategy_card_id": string(),
        "strategy_card": obj({"id": string(), "name": string(4), "plain_rule": string(18)}),
        "life_application": obj({"prompt": string(20), "difference_warning": string(20)}),
        "reward": obj({"insight_points": {"type": "integer", "enum": [20]}, "card_updates": arr(string(), 0, 3)}),
        "tomorrow": obj({"day": {"type": "integer", "minimum": 16, "maximum": 366}, "question": string(20)}),
        "new_terms": arr(string(), 1, 5),
        "source_ids": arr(string(), 2, 8),
    }
)
source_schema = obj(
    {
        "id": string(4),
        "tier": {"type": "string", "enum": ["H1", "H2", "N1", "P1", "V1"]},
        "kind": string(2),
        "title": string(3),
        "author_or_holder": string(2),
        "language": string(2),
        "location": string(5),
        "url": string(12),
        "license_or_use": string(8),
    }
)
image_candidate_schema = obj(
    {
        "file_title": string(8),
        "file_page_url": string(20),
        "alt": string(20),
        "relevance_reason": string(20),
    }
)
OUTPUT_SCHEMA = obj(
    {
        "item": item_schema,
        "new_sources": arr(source_schema, 0, 5),
        "image_candidates": arr(image_candidate_schema, 2, 3),
    }
)


SYSTEM_PROMPT = """당신은 삼국지 전문 역사 편집자이자 연속극 작가, 비즈니스·권력 심리 해설자다. WebSearch와 WebFetch로 실제 원문과 신뢰 가능한 자료를 확인한 뒤 구조화 JSON만 반환한다. 연의의 재미와 정사의 사실을 분리하고, 확인하지 못한 숫자·대사·내면·출처를 만들지 않는다. 사용자는 쉬운 한국어와 비즈니스, 사람의 본능, 어두운 심리, 행동경제학, 보이지 않는 구조에 관심이 있다. 조종법에는 반드시 방어법을 붙인다. 이미지 후보는 오늘 사건과 직접 관련된 Wikimedia Commons의 실제 File: 페이지로 2~3개 제시하고 공개 라이선스를 확인한다."""


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


def source_host_allowed(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in ALLOWED_SOURCE_HOSTS
    except ValueError:
        return False


def validate_source_url(url: str) -> None:
    if not source_host_allowed(url):
        raise ValueError(f"source host is not approved: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Samgukji365/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status >= 400:
            raise ValueError(f"source unavailable: {url}")


def validate_item(item: dict, source_ids: set[str], expected_day: int) -> None:
    if item.get("day") != expected_day:
        raise ValueError(f"wrong day: expected {expected_day}, got {item.get('day')}")
    if item.get("event_id") != f"w{((expected_day - 1) // 7) + 1:02d}-d{((expected_day - 1) % 7) + 1}":
        raise ValueError("event_id does not match day")
    if item.get("published_at") != (START_DATE + dt.timedelta(days=expected_day - 1)).isoformat():
        raise ValueError("published_at does not match the daily sequence")
    if len(item.get("story_sections", [])) != 5:
        raise ValueError("exactly five story sections are required")
    total_body = sum(len(section.get("body", "")) for section in item["story_sections"])
    if not 1400 <= total_body <= 3000:
        raise ValueError(f"story body length out of range: {total_body}")
    if item["tomorrow"]["day"] != expected_day + 1:
        raise ValueError("tomorrow.day must be the next day")
    if item["strategy_card_id"] != item["strategy_card"]["id"]:
        raise ValueError("strategy card ids differ")
    if len(item["memory_lock"]["anchors"]) != 3 or len(item["new_terms"]) > 5:
        raise ValueError("memory limits violated")
    referenced = set(item["source_ids"])
    for block in item["record_check"] + item["evidence_chain"]:
        referenced.update(block["source_ids"])
    missing = referenced - source_ids
    if missing:
        raise ValueError(f"unknown source ids: {sorted(missing)}")
    if not any(source_id.startswith(("n1-", "ws-romance", "romance")) for source_id in referenced):
        raise ValueError("a narrative source is required")
    if not any(source_id.startswith(("h1-", "hhs-", "ws-houhanshu", "ctext-")) for source_id in referenced):
        raise ValueError("a historical source is required")


def validate_feed(feed: dict) -> None:
    if feed.get("schema_version") != 1 or feed.get("project") != "삼국지 365":
        raise ValueError("invalid feed identity")
    items = feed.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("feed needs at least one item")
    days = [item.get("day") for item in items]
    if days != list(range(1, len(items) + 1)) or len(items) > 365:
        raise ValueError("feed days must be consecutive from 1")
    sources = feed.get("sources")
    if not isinstance(sources, list):
        raise ValueError("feed sources missing")
    source_ids = {source.get("id") for source in sources}
    if None in source_ids or len(source_ids) != len(sources):
        raise ValueError("source ids must be unique")
    for item in items:
        if item.get("schema_version") != 1 or not item.get("image", {}).get("url"):
            raise ValueError(f"invalid item or image on day {item.get('day')}")
        missing = set(item.get("source_ids", [])) - source_ids
        if missing:
            raise ValueError(f"day {item['day']} has missing sources: {sorted(missing)}")


def compact_catalog(catalog: dict) -> list[dict]:
    allowed_status = {"approved_core", "approved_support", "approved_visual"}
    return [
        {
            "id": resource["id"],
            "tier": resource["tier"],
            "title": resource["title"],
            "url": resource["url"],
            "best_for": resource.get("best_for", []),
            "limits": resource.get("limits", ""),
        }
        for resource in catalog["resources"]
        if resource.get("status") in allowed_status and source_host_allowed(resource["url"])
    ]


def catalog_source(resource: dict, checked_at: str) -> dict:
    return {
        "id": resource["id"],
        "registry_id": resource["id"],
        "tier": resource["tier"],
        "kind": resource["category"],
        "title": resource["title"],
        "author_or_holder": resource["holder"],
        "language": ", ".join(resource.get("language", [])) or "unknown",
        "location": "; ".join(resource.get("best_for", [])),
        "url": resource["url"],
        "license_or_use": resource["reuse"],
        "checked_at": checked_at,
    }


def call_claude(day_plan: dict, previous_item: dict, catalog: dict) -> dict:
    if any(os.environ.get(key) for key in PAID_ENV):
        raise RuntimeError("metered Anthropic routing is forbidden")
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not token.startswith("sk-ant-oat01-"):
        raise RuntimeError("Claude subscription OAuth token is required")
    executable = shutil.which("claude")
    if not executable:
        raise RuntimeError("Claude Code CLI not found")
    command = [
        executable,
        "-p",
        "--model",
        MODEL,
        "--effort",
        "high",
        "--tools",
        "WebSearch,WebFetch",
        "--allowedTools",
        "WebSearch,WebFetch",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--permission-mode",
        "auto",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(",", ":")),
        "--system-prompt",
        SYSTEM_PROMPT,
    ]
    prompt = {
        "task": "다음 하루 한 편을 조사하고 작성한다. 반드시 웹에서 실제 출처와 Commons 파일 페이지를 확인한다.",
        "rules": RULES_PATH.read_text(encoding="utf-8"),
        "day_plan": day_plan,
        "previous_day": {
            "day": previous_item["day"],
            "title": previous_item["title"],
            "consequences": previous_item["consequences"],
            "tomorrow": previous_item["tomorrow"],
            "narrative_anchor": previous_item["narrative_anchor"],
        },
        "approved_source_catalog": compact_catalog(catalog),
        "hard_values": {
            "day": day_plan["day"],
            "event_id": day_plan["event_id"],
            "published_at": (START_DATE + dt.timedelta(days=day_plan["day"] - 1)).isoformat(),
            "tomorrow_day": day_plan["day"] + 1,
        },
    }
    result = subprocess.run(
        command,
        input=json.dumps(prompt, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude failed: {result.stderr[-1500:]}")
    outer = json.loads(result.stdout)
    structured = outer.get("structured_output")
    if not isinstance(structured, dict):
        raise RuntimeError("Claude structured output missing")
    return structured


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


def commons_info(file_title: str) -> dict:
    title = file_title if file_title.startswith("File:") else f"File:{file_title}"
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "titles": title,
        }
    )
    request = urllib.request.Request(
        f"https://commons.wikimedia.org/w/api.php?{params}",
        headers={"User-Agent": "Samgukji365/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    page = data["query"]["pages"][0]
    if page.get("missing") or not page.get("imageinfo"):
        raise ValueError(f"Commons file missing: {title}")
    info = page["imageinfo"][0]
    metadata = info.get("extmetadata", {})
    license_name = plain_text(metadata.get("LicenseShortName", {}).get("value", ""))
    allowed = ("public domain", "cc0", "cc by", "cc-by")
    if not any(marker in license_name.lower() for marker in allowed):
        raise ValueError(f"non-free Commons license: {license_name}")
    if info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError(f"unsupported image type: {info.get('mime')}")
    if info.get("width", 0) < 900 or info.get("height", 0) < 600:
        raise ValueError("Commons image is too small")
    if info.get("size", 0) > 25_000_000:
        raise ValueError("Commons image is too large")
    return {
        "title": title,
        "url": info["url"],
        "page_url": info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
        "license": license_name,
        "license_url": plain_text(metadata.get("LicenseUrl", {}).get("value", "")),
        "artist": plain_text(metadata.get("Artist", {}).get("value", "")) or "Wikimedia Commons contributor",
    }


def download_editorial_image(candidates: list[dict], day: int) -> dict:
    from PIL import Image

    PUBLIC_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    for candidate in candidates:
        try:
            info = commons_info(candidate["file_title"])
            request = urllib.request.Request(info["url"], headers={"User-Agent": "Samgukji365/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read(25_000_001)
            if len(raw) > 25_000_000:
                raise ValueError("downloaded image is too large")
            temp = PUBLIC_IMAGE_DIR / f"day-{day:03d}.source"
            temp.write_bytes(raw)
            output = PUBLIC_IMAGE_DIR / f"day-{day:03d}.jpg"
            with Image.open(temp) as source:
                image = source.convert("RGB")
                target_ratio = 3 / 2
                ratio = image.width / image.height
                if ratio > target_ratio:
                    new_width = round(image.height * target_ratio)
                    left = (image.width - new_width) // 2
                    image = image.crop((left, 0, left + new_width, image.height))
                else:
                    new_height = round(image.width / target_ratio)
                    top = (image.height - new_height) // 2
                    image = image.crop((0, top, image.width, top + new_height))
                image.thumbnail((1800, 1200), Image.Resampling.LANCZOS)
                image.save(output, "JPEG", quality=88, optimize=True, progressive=True)
            temp.unlink(missing_ok=True)
            return {
                "kind": "editorial-hero",
                "url": f"{PUBLIC_BASE}/images/{output.name}",
                "alt": candidate["alt"],
                "creator": info["artist"][:240],
                "source_name": f"Wikimedia Commons · {info['title'].removeprefix('File:')}",
                "source_url": info["page_url"],
                "license": info["license"],
                "license_url": info["license_url"] or None,
                "visual_reference_source_ids": [],
                "provenance_note": "공개 라이선스 원본에서 오늘 장면에 맞는 3:2 영역을 잘라 만든 대표 이미지다.",
            }
        except Exception as exc:
            errors.append(f"{candidate.get('file_title')}: {exc}")
    raise RuntimeError("no usable Commons image; " + " | ".join(errors))


def merge_sources(feed: dict, bundle: dict, catalog: dict, checked_at: str) -> set[str]:
    existing = {source["id"]: source for source in feed["sources"]}
    resources = {resource["id"]: resource for resource in catalog["resources"]}
    requested = set(bundle["item"]["source_ids"])
    for block in bundle["item"]["record_check"] + bundle["item"]["evidence_chain"]:
        requested.update(block["source_ids"])
    for source_id in requested:
        if source_id not in existing and source_id in resources:
            existing[source_id] = catalog_source(resources[source_id], checked_at)
    for source in bundle["new_sources"]:
        if not source_host_allowed(source["url"]):
            raise ValueError(f"new source host rejected: {source['url']}")
        source = {**source, "checked_at": checked_at}
        if source["id"] in existing and existing[source["id"]]["url"] != source["url"]:
            raise ValueError(f"source id collision: {source['id']}")
        existing[source["id"]] = source
    unresolved = requested - set(existing)
    if unresolved:
        raise ValueError(f"unresolved sources: {sorted(unresolved)}")
    for source_id in requested:
        validate_source_url(existing[source_id]["url"])
    feed["sources"] = sorted(existing.values(), key=lambda source: source["id"])
    return set(existing)


def next_day_for_date(feed: dict, target_date: dt.date) -> int | None:
    target_day = (target_date - START_DATE).days + 1
    if target_day < 1:
        return None
    current_day = max(item["day"] for item in feed["items"])
    if current_day >= min(target_day, 365) or current_day >= 365:
        return None
    return current_day + 1


def seed_item(queue: dict, day: int) -> dict | None:
    for item in queue.get("items", []):
        if item.get("day") == day:
            item = json.loads(json.dumps(item))
            image_url = item.get("image", {}).get("url", "")
            if image_url.startswith("/images/"):
                item["image"]["url"] = PUBLIC_BASE + image_url
            return item
    return None


def run(target_date: dt.date, validate_only: bool = False, dry_run: bool = False) -> dict:
    feed = load_json(FEED_PATH)
    validate_feed(feed)
    if validate_only:
        return {"status": "validated", "items": len(feed["items"]), "latest_day": feed["items"][-1]["day"]}
    day = next_day_for_date(feed, target_date)
    if day is None:
        if not dry_run:
            write_json_atomic(PUBLIC_FEED_PATH, feed)
        return {"status": "unchanged", "date": target_date.isoformat(), "latest_day": feed["items"][-1]["day"]}
    if dry_run:
        return {"status": "ready", "date": target_date.isoformat(), "next_day": day, "seeded": day <= 14}

    checked_at = dt.datetime.now(SEOUL).date().isoformat()
    queue = load_json(QUEUE_PATH)
    item = seed_item(queue, day)
    if item is None:
        curriculum = load_json(CURRICULUM_PATH)
        day_plan = next(entry for entry in curriculum["days"] if entry["day"] == day)
        catalog = load_json(CATALOG_PATH)
        bundle = call_claude(day_plan, feed["items"][-1], catalog)
        source_ids = merge_sources(feed, bundle, catalog, checked_at)
        item = bundle["item"]
        validate_item(item, source_ids, day)
        item["image"] = download_editorial_image(bundle["image_candidates"], day)
    else:
        source_ids = {source["id"] for source in feed["sources"]}
        missing = set(item["source_ids"]) - source_ids
        if missing:
            raise ValueError(f"seed item sources missing: {sorted(missing)}")

    feed["items"].append(item)
    feed["items"].sort(key=lambda entry: entry["day"])
    feed["generated_at"] = dt.datetime.now(SEOUL).isoformat(timespec="seconds")
    validate_feed(feed)
    write_json_atomic(FEED_PATH, feed)
    write_json_atomic(PUBLIC_FEED_PATH, feed)
    return {"status": "published", "date": target_date.isoformat(), "day": day, "items": len(feed["items"]), "model": "seed" if day <= 14 else MODEL}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.datetime.now(SEOUL).date().isoformat())
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = run(dt.date.fromisoformat(args.date), args.validate_only, args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
