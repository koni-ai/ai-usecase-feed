from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
FEED_PATH = ROOT / "data" / "feed.json"
PUBLIC_FEED_PATH = REPO / "site" / "habitus" / "feed.json"
CANDIDATES_PATH = ROOT / "candidates.json"
SEOUL = ZoneInfo("Asia/Seoul")
MODEL = "claude-sonnet-5"
PAID_ENV = {"ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"}

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_ko", "hook", "intro", "look_points", "story_sections", "why_it_matters", "not_for_everyone", "takeaway", "taste_tags"],
    "properties": {
        "title_ko": {"type": "string", "minLength": 2},
        "hook": {"type": "string", "minLength": 50},
        "intro": {"type": "string", "minLength": 80},
        "look_points": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "object", "additionalProperties": False, "required": ["label", "text"], "properties": {"label": {"type": "string"}, "text": {"type": "string", "minLength": 35}}}},
        "story_sections": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "object", "additionalProperties": False, "required": ["title", "body"], "properties": {"title": {"type": "string"}, "body": {"type": "string", "minLength": 170}}}},
        "why_it_matters": {"type": "string", "minLength": 60},
        "not_for_everyone": {"type": "string", "minLength": 45},
        "takeaway": {"type": "string", "minLength": 25},
        "taste_tags": {"type": "array", "minItems": 5, "maxItems": 5, "items": {"type": "string"}}
    }
}

SYSTEM_PROMPT = """당신은 모바일 개인 미술관의 한국어 도슨트다. 제공된 공식 미술관 메타데이터만 사실의 뼈대로 사용한다. 확인되지 않은 일화, 가격, 인용문은 만들지 않는다. 독자가 무엇을 먼저 볼지, 왜 좋은지, 왜 취향이 아닐 수도 있는지를 흥미롭게 설명한다. 전체는 한국어 5분 분량이며 전문용어는 바로 풀어쓴다. 작품을 무조건 칭찬하지 않는다."""


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


def fetch_met(object_id: int) -> dict:
    url = f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
    request = urllib.request.Request(url, headers={"User-Agent": "HabitusDaily/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.load(response)
    if not data.get("isPublicDomain") or not data.get("primaryImageSmall"):
        raise ValueError(f"Met object {object_id} is not public-domain with image")
    return data


def call_claude(metadata: dict) -> dict:
    if any(os.environ.get(key) for key in PAID_ENV):
        raise RuntimeError("metered Anthropic routing is forbidden")
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if token and not token.startswith("sk-ant-oat01-"):
        raise RuntimeError("invalid Claude subscription OAuth token")
    executable = shutil.which("claude")
    if not executable:
        raise RuntimeError("Claude Code CLI not found")
    command = [
        executable, "-p", "--model", MODEL, "--effort", "medium",
        "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "manual",
        "--no-session-persistence", "--output-format", "json",
        "--json-schema", json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(",", ":")),
        "--system-prompt", SYSTEM_PROMPT
    ]
    prompt = json.dumps({"official_museum_metadata": metadata}, ensure_ascii=False)
    result = subprocess.run(command, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Claude failed: {result.stderr[-1000:]}")
    outer = json.loads(result.stdout)
    structured = outer.get("structured_output")
    if not isinstance(structured, dict):
        raise RuntimeError("Claude structured output missing")
    return structured


def build_item(data: dict, story: dict, published_at: str) -> dict:
    object_id = data["objectID"]
    artist = data.get("artistDisplayName") or data.get("culture") or "작자 미상"
    return {
        "id": f"met-{object_id}",
        "published_at": published_at,
        "category": "회화" if "paint" in (data.get("objectName") or "").lower() else "조각",
        "title_ko": story["title_ko"],
        "title_original": data.get("title") or "Untitled",
        "creator": artist,
        "year": data.get("objectDate") or "연대 미상",
        "location": data.get("culture") or data.get("artistNationality") or "The Metropolitan Museum of Art",
        "read_minutes": 5,
        "hook": story["hook"],
        "intro": story["intro"],
        "look_points": story["look_points"],
        "story_sections": story["story_sections"],
        "why_it_matters": story["why_it_matters"],
        "not_for_everyone": story["not_for_everyone"],
        "takeaway": story["takeaway"],
        "taste_tags": story["taste_tags"],
        "image": {
            "url": data["primaryImageSmall"],
            "alt": f"{artist}의 {data.get('title', '작품')}",
            "creator": artist,
            "source_name": "The Metropolitan Museum of Art",
            "source_url": data["objectURL"],
            "license": "Public Domain · Open Access",
            "license_url": "https://www.metmuseum.org/about-the-met/policies-and-documents/open-access"
        },
        "sources": [data["objectURL"], "https://www.metmuseum.org/about-the-met/policies-and-documents/open-access"]
    }


def validate_feed(feed: dict) -> None:
    assert feed.get("schema_version") == 1
    items = feed.get("items")
    assert isinstance(items, list) and len(items) >= 7
    assert len({item["id"] for item in items}) == len(items)
    for item in items:
        assert len(item["look_points"]) == 3
        assert len(item["story_sections"]) == 4
        assert item["image"]["url"].startswith("https://")
        assert item["image"]["license"]


def run(target_date: str, validate_only: bool = False) -> dict:
    feed = load_json(FEED_PATH)
    validate_feed(feed)
    if validate_only:
        return {"status": "validated", "items": len(feed["items"])}
    if any(item["published_at"] == target_date for item in feed["items"]):
        shutil.copyfile(FEED_PATH, PUBLIC_FEED_PATH)
        return {"status": "unchanged", "date": target_date, "items": len(feed["items"])}
    seen = {item["id"] for item in feed["items"]}
    candidate_ids = load_json(CANDIDATES_PATH)["met_object_ids"]
    candidate = next((object_id for object_id in candidate_ids if f"met-{object_id}" not in seen), None)
    if candidate is None:
        raise RuntimeError("curated candidate queue exhausted")
    metadata = fetch_met(candidate)
    story = call_claude(metadata)
    item = build_item(metadata, story, target_date)
    feed["items"].insert(0, item)
    feed["generated_at"] = dt.datetime.now(SEOUL).isoformat(timespec="seconds")
    validate_feed(feed)
    write_json_atomic(FEED_PATH, feed)
    write_json_atomic(PUBLIC_FEED_PATH, feed)
    return {"status": "published", "date": target_date, "id": item["id"], "items": len(feed["items"]), "model": MODEL}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.datetime.now(SEOUL).date().isoformat())
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.date, args.validate_only), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

