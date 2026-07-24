from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import yaml

from source_manager import (
    MAX_FEED_BYTES,
    SourceManagerError,
    make_safe_fetcher,
    runtime_sources,
    update_health,
)


SUPPORTED_SOURCE_TYPES = {"rss", "hn_algolia", "reddit_json"}
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}
PREFERRED_TITLE_TERMS = (
    "built",
    "made",
    "automated",
    "automation",
    "workflow",
    "how i",
    "with claude",
    "with gpt",
    "using claude",
    "using gpt",
    "만들었",
    "자동화",
    "활용",
    "구축",
)
EXCLUDED_TITLE_TERMS = (
    "funding",
    "fundraise",
    "raises $",
    "series a",
    "series b",
    "series c",
    "investment round",
    "hiring",
    "we are hiring",
    "joins as",
    "appointed",
    "regulation",
    "regulatory",
    "politics",
    "political",
    "election",
    "funding announced",
    "model released",
    "model launch",
    "announces new model",
    "투자 유치",
    "펀딩",
    "채용",
    "인사",
    "취임",
    "규제",
    "정치",
    "선거",
    "모델 출시",
)
DEFAULT_USER_AGENT = (
    "Caring-AI-Usecase-Feed/1.0 "
    "(personal research collector; contact: local-owner)"
)
SEOUL = ZoneInfo("Asia/Seoul")


class CollectorError(RuntimeError):
    """Base error for deterministic collector failures."""


class ConfigError(CollectorError):
    """Raised when sources.yaml is invalid."""


class DataIntegrityError(CollectorError):
    """Raised when persistent JSON state is missing required structure."""


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        return " ".join(self.parts)


@dataclass
class RateLimiter:
    interval_seconds: float
    last_request_started: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last_request_started is not None:
            remaining = self.interval_seconds - (now - self.last_request_started)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request_started = time.monotonic()


FetchFunction = Callable[[dict[str, Any], datetime], bytes]


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html.unescape(value))
        parser.close()
        return re.sub(r"\s+", " ", parser.text()).strip()
    except Exception:
        return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with temporary.open("r", encoding="utf-8") as handle:
            json.load(handle)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_sources(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"소스 설정을 읽을 수 없습니다: {path}: {exc}") from exc

    if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
        raise ConfigError("sources.yaml 최상위에는 sources 목록이 필요합니다.")

    enabled_sources: list[dict[str, Any]] = []
    known_names: set[str] = set()
    for index, source in enumerate(document["sources"], start=1):
        if not isinstance(source, dict):
            raise ConfigError(f"sources[{index}]는 객체여야 합니다.")
        missing = [
            field
            for field in ("name", "type", "endpoint")
            if not isinstance(source.get(field), str) or not source[field].strip()
        ]
        if missing:
            raise ConfigError(f"sources[{index}] 필수 필드 누락: {', '.join(missing)}")
        if source["name"] in known_names:
            raise ConfigError(f"중복 소스 이름: {source['name']}")
        known_names.add(source["name"])
        if source["type"] not in SUPPORTED_SOURCE_TYPES:
            raise ConfigError(
                f"지원하지 않는 소스 타입: {source['type']} "
                f"(지원: {', '.join(sorted(SUPPORTED_SOURCE_TYPES))})"
            )
        parsed_endpoint = urlsplit(source["endpoint"])
        if parsed_endpoint.scheme.lower() != "https" or not parsed_endpoint.netloc:
            raise ConfigError(f"HTTPS 엔드포인트만 허용됩니다: {source['endpoint']}")
        fallback = source.get("fallback")
        if fallback is not None:
            if not isinstance(fallback, dict):
                raise ConfigError(f"{source['name']} fallback은 객체여야 합니다.")
            fallback_type = fallback.get("type")
            fallback_endpoint = fallback.get("endpoint")
            if fallback_type not in SUPPORTED_SOURCE_TYPES:
                raise ConfigError(f"{source['name']} fallback 타입이 올바르지 않습니다.")
            if not isinstance(fallback_endpoint, str):
                raise ConfigError(f"{source['name']} fallback endpoint가 필요합니다.")
            parsed_fallback = urlsplit(fallback_endpoint)
            if parsed_fallback.scheme.lower() != "https" or not parsed_fallback.netloc:
                raise ConfigError(
                    f"fallback도 HTTPS 엔드포인트만 허용됩니다: {fallback_endpoint}"
                )
        if source.get("enabled", True):
            enabled_sources.append(dict(source))

    if not enabled_sources:
        raise ConfigError("활성화된 소스가 없습니다.")
    return enabled_sources


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataIntegrityError(
            f"seen.json이 손상되었습니다. 자동 초기화하지 않습니다: {path}: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise DataIntegrityError("seen.json은 SHA-256 문자열 목록이어야 합니다.")
    valid_hash = re.compile(r"^[0-9a-f]{64}$")
    if any(not isinstance(value, str) or not valid_hash.fullmatch(value) for value in payload):
        raise DataIntegrityError("seen.json에 유효하지 않은 SHA-256 값이 있습니다.")
    return set(payload)


def canonicalize_url(url: str, fallback_url: str | None = None) -> str:
    candidate = (url or "").strip() or (fallback_url or "").strip()
    if not candidate:
        raise ValueError("URL과 fallback URL이 모두 비어 있습니다.")
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError(f"유효한 HTTP(S) URL이 아닙니다: {candidate}")

    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname
    if port and not default_port:
        netloc = f"{hostname}:{port}"

    path = re.sub(r"/{2,}", "/", parsed.path or "")
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        query_pairs.append((key, value))
    query_pairs.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    query = urlencode(query_pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def url_sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def title_passes_first_filter(title: str) -> tuple[bool, str]:
    normalized = re.sub(r"\s+", " ", title).strip().lower()
    if any(term in normalized for term in PREFERRED_TITLE_TERMS):
        return True, "preferred_keyword"
    if any(term in normalized for term in EXCLUDED_TITLE_TERMS):
        return False, "excluded_category"
    return True, "ambiguous_pass"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _direct_child_text(element: ElementTree.Element, names: Iterable[str]) -> str:
    expected = {name.lower() for name in names}
    for child in list(element):
        if _local_name(child.tag) in expected:
            return "".join(child.itertext()).strip()
    return ""


def _feed_link(element: ElementTree.Element) -> str:
    for child in list(element):
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "alternate").lower()
        if href and rel == "alternate":
            return href
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def parse_rss(payload: bytes, source: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    upper_payload = payload.upper()
    if b"<!DOCTYPE" in upper_payload or b"<!ENTITY" in upper_payload:
        raise ValueError("DOCTYPE/ENTITY가 포함된 XML은 거부됩니다.")
    root = ElementTree.fromstring(payload)
    entries = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    items: list[dict[str, Any]] = []
    for entry in entries:
        title = strip_html(_direct_child_text(entry, ("title",)))
        link = _feed_link(entry)
        if not title or not link:
            continue
        text = _direct_child_text(entry, ("description", "summary", "content"))
        published = _direct_child_text(
            entry, ("pubdate", "published", "updated", "dc:date", "date")
        )
        items.append(
            {
                "title": title,
                "url": link,
                "text": strip_html(text),
                "published_at": published,
                "source_name": source["name"],
                "source_type": source["type"],
                "collected_at": now.isoformat(),
            }
        )
    return items


def parse_hn_algolia(
    payload: bytes, source: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    document = json.loads(payload.decode("utf-8-sig"))
    hits = document.get("hits")
    if not isinstance(hits, list):
        raise ValueError("Algolia 응답에 hits 목록이 없습니다.")
    cutoff = now.astimezone(timezone.utc) - timedelta(
        hours=int(source.get("recent_hours", 24))
    )
    items: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        object_id = str(hit.get("objectID") or "").strip()
        title = strip_html(str(hit.get("title") or hit.get("story_title") or ""))
        if not object_id or not title:
            continue
        created_epoch = hit.get("created_at_i")
        if isinstance(created_epoch, (int, float)):
            created_at = datetime.fromtimestamp(created_epoch, tz=timezone.utc)
            if created_at < cutoff:
                continue
            published = created_at.isoformat()
        else:
            published = str(hit.get("created_at") or "")
        fallback = f"https://news.ycombinator.com/item?id={object_id}"
        items.append(
            {
                "title": title,
                "url": str(hit.get("url") or fallback),
                "fallback_url": fallback,
                "text": strip_html(str(hit.get("story_text") or "")),
                "published_at": published,
                "source_name": source["name"],
                "source_type": source["type"],
                "collected_at": now.isoformat(),
            }
        )
    return items


def parse_reddit_json(
    payload: bytes, source: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    document = json.loads(payload.decode("utf-8-sig"))
    children = document.get("data", {}).get("children")
    if not isinstance(children, list):
        raise ValueError("Reddit 응답에 data.children 목록이 없습니다.")
    items: list[dict[str, Any]] = []
    for child in children:
        data = child.get("data") if isinstance(child, dict) else None
        if not isinstance(data, dict):
            continue
        reddit_id = str(data.get("id") or "").strip()
        title = strip_html(str(data.get("title") or ""))
        if not reddit_id or not title:
            continue
        permalink = str(data.get("permalink") or f"/comments/{reddit_id}")
        fallback = f"https://www.reddit.com{permalink}"
        external_url = str(data.get("url_overridden_by_dest") or data.get("url") or "")
        if data.get("is_self"):
            external_url = fallback
        created_epoch = data.get("created_utc")
        published = (
            datetime.fromtimestamp(created_epoch, tz=timezone.utc).isoformat()
            if isinstance(created_epoch, (int, float))
            else ""
        )
        items.append(
            {
                "title": title,
                "url": external_url or fallback,
                "fallback_url": fallback,
                "text": strip_html(str(data.get("selftext") or "")),
                "published_at": published,
                "source_name": source["name"],
                "source_type": source["type"],
                "collected_at": now.isoformat(),
            }
        )
    return items


PARSERS: dict[str, Callable[[bytes, dict[str, Any], datetime], list[dict[str, Any]]]] = {
    "rss": parse_rss,
    "hn_algolia": parse_hn_algolia,
    "reddit_json": parse_reddit_json,
}


def _network_url(source: dict[str, Any], now: datetime) -> str:
    if source["type"] != "hn_algolia":
        return source["endpoint"]
    parsed = urlsplit(source["endpoint"])
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    recent_hours = int(source.get("recent_hours", 24))
    cutoff = int((now.astimezone(timezone.utc) - timedelta(hours=recent_hours)).timestamp())
    query["numericFilters"] = f"created_at_i>{cutoff}"
    query["hitsPerPage"] = "100"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def make_network_fetcher(
    timeout_seconds: float,
    request_interval_seconds: float,
    user_agent: str,
) -> FetchFunction:
    limiter = RateLimiter(request_interval_seconds)
    dynamic_fetch = make_safe_fetcher(
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
    )

    def fetch(source: dict[str, Any], now: datetime) -> bytes:
        url = _network_url(source, now)
        if source.get("dynamic"):
            limiter.wait()
            payload, _, _ = dynamic_fetch(
                url,
                "application/rss+xml, application/atom+xml, "
                "application/xml, text/xml",
                MAX_FEED_BYTES,
            )
            return payload
        for attempt in range(2):
            limiter.wait()
            request = Request(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/json, application/atom+xml, "
                    "application/rss+xml, application/xml, text/xml;q=0.9, "
                    "*/*;q=0.5",
                    "Accept-Encoding": "identity",
                },
            )
            try:
                with urlopen(request, timeout=timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                if exc.code != 429 or attempt:
                    raise
                retry_after = str(exc.headers.get("Retry-After") or "").strip()
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = 2.0
                time.sleep(max(1.0, min(delay, 15.0)))
        raise RuntimeError("429 재시도 상태가 올바르지 않습니다.")

    return fetch


def _published_sort_key(item: dict[str, Any]) -> float:
    value = str(item.get("published_at") or "").strip()
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _load_existing_raw(path: Path, date_text: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "date": date_text,
            "updated_at": None,
            "runs": [],
            "items": [],
        }
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataIntegrityError(
            f"일자별 raw JSON이 손상되었습니다. 자동 덮어쓰기하지 않습니다: {path}: {exc}"
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("date") != date_text
        or not isinstance(document.get("runs"), list)
        or not isinstance(document.get("items"), list)
    ):
        raise DataIntegrityError(f"일자별 raw JSON 구조가 올바르지 않습니다: {path}")
    return document


def collect_once(
    sources: list[dict[str, Any]],
    data_dir: Path,
    fetch: FetchFunction,
    now: datetime,
    max_candidates: int = 30,
) -> dict[str, Any]:
    if max_candidates < 1 or max_candidates > 30:
        raise ValueError("max_candidates는 1~30 범위여야 합니다.")

    started_at = now.isoformat()
    seen_path = data_dir / "seen.json"
    seen_hashes = load_seen(seen_path)
    date_text = now.astimezone(SEOUL).date().isoformat()
    raw_path = data_dir / "raw" / f"{date_text}.json"
    raw_document = _load_existing_raw(raw_path, date_text)
    daily_remaining = max(0, max_candidates - len(raw_document["items"]))
    source_stats: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    current_run_hashes: set[str] = set()
    success_count = 0

    for source_order, source in enumerate(sources):
        stat = {
            "name": source["name"],
            "type": source["type"],
            "status": "success",
            "fetched": 0,
            "filtered_out": 0,
            "duplicate": 0,
            "invalid": 0,
            "deferred_by_limit": 0,
            "selected": 0,
            "fallback_used": False,
            "fallback_type": None,
            "error": None,
        }
        try:
            parser_source = source
            try:
                payload = fetch(source, now)
            except HTTPError as exc:
                fallback = source.get("fallback")
                if exc.code not in {403, 429} or not isinstance(fallback, dict):
                    raise
                parser_source = dict(source)
                parser_source["type"] = fallback["type"]
                parser_source["endpoint"] = fallback["endpoint"]
                payload = fetch(parser_source, now)
                stat["fallback_used"] = True
                stat["fallback_type"] = fallback["type"]
            parsed_items = PARSERS[parser_source["type"]](payload, parser_source, now)
            stat["fetched"] = len(parsed_items)
            for item in parsed_items:
                try:
                    canonical_url = canonicalize_url(
                        str(item.get("url") or ""),
                        str(item.get("fallback_url") or ""),
                    )
                except (TypeError, ValueError):
                    stat["invalid"] += 1
                    continue
                digest = url_sha256(canonical_url)
                if digest in seen_hashes or digest in current_run_hashes:
                    stat["duplicate"] += 1
                    continue
                passed, filter_reason = title_passes_first_filter(str(item["title"]))
                if not passed:
                    stat["filtered_out"] += 1
                    continue
                current_run_hashes.add(digest)
                normalized_item = {
                    "title": item["title"],
                    "url": canonical_url,
                    "url_hash": digest,
                    "text": item.get("text", ""),
                    "published_at": item.get("published_at", ""),
                    "source_name": item["source_name"],
                    "source_type": item["source_type"],
                    "collected_at": item["collected_at"],
                    "first_filter": filter_reason,
                    "_source_order": source_order,
                }
                candidates.append(normalized_item)
            success_count += 1
        except Exception as exc:
            stat["status"] = "error"
            stat["error"] = f"{type(exc).__name__}: {exc}"
        source_stats.append(stat)

    candidates.sort(
        key=lambda item: (
            -_published_sort_key(item),
            item["_source_order"],
            item["url_hash"],
        )
    )
    source_ranks: dict[str, int] = {}
    for item in candidates:
        source_name = item["source_name"]
        item["_source_rank"] = source_ranks.get(source_name, 0)
        source_ranks[source_name] = item["_source_rank"] + 1
    balanced_candidates = sorted(
        candidates,
        key=lambda item: (
            item["_source_rank"],
            -_published_sort_key(item),
            item["_source_order"],
            item["url_hash"],
        ),
    )
    selected = balanced_candidates[:daily_remaining]
    selected_hashes = {item["url_hash"] for item in selected}
    for stat in source_stats:
        stat["selected"] = sum(
            1 for item in selected if item["source_name"] == stat["name"]
        )
        stat["deferred_by_limit"] = sum(
            1
            for item in balanced_candidates[daily_remaining:]
            if item["source_name"] == stat["name"]
        )
    for item in selected:
        item.pop("_source_order", None)
        item.pop("_source_rank", None)

    finished_at = datetime.now(SEOUL).isoformat()
    max_source_selected = max(
        (stat["selected"] for stat in source_stats),
        default=0,
    )
    run_record = {
        "started_at": started_at,
        "finished_at": finished_at,
        "sources": source_stats,
        "successful_sources": success_count,
        "failed_sources": len(sources) - success_count,
        "candidate_count_before_limit": len(candidates),
        "selected_count": len(selected),
        "max_candidates": max_candidates,
        "daily_capacity_before_run": daily_remaining,
        "partial_failure": 0 < success_count < len(sources),
        "selection_strategy": "freshness_round_robin",
        "source_share_warning": (
            bool(selected)
            and max_source_selected / len(selected) > 0.40
        ),
    }

    existing_item_hashes = {
        item.get("url_hash")
        for item in raw_document["items"]
        if isinstance(item, dict)
    }
    raw_document["items"].extend(
        item for item in selected if item["url_hash"] not in existing_item_hashes
    )
    raw_document["runs"].append(run_record)
    raw_document["updated_at"] = finished_at

    _atomic_write_json(raw_path, raw_document)
    if selected_hashes:
        _atomic_write_json(seen_path, sorted(seen_hashes | selected_hashes))
    elif not seen_path.exists():
        _atomic_write_json(seen_path, sorted(seen_hashes))

    return {
        "status": "success" if success_count else "failed",
        "date": date_text,
        "raw_path": str(raw_path),
        "selected_count": len(selected),
        "candidate_count_before_limit": len(candidates),
        "successful_sources": success_count,
        "failed_sources": len(sources) - success_count,
        "sources": source_stats,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="AI 활용사례 Phase 1 수집기")
    parser.add_argument(
        "--sources",
        type=Path,
        default=project_dir / "sources.yaml",
        help="소스 YAML 파일",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=project_dir / "data",
        help="seen.json과 raw/가 위치할 데이터 디렉토리",
    )
    parser.add_argument(
        "--source-registry",
        type=Path,
        default=project_dir / "data" / "source_registry.json",
    )
    parser.add_argument(
        "--source-health",
        type=Path,
        default=project_dir / "data" / "source_health.json",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.timeout <= 0 or args.timeout > 60:
            raise ConfigError("timeout은 0초 초과 60초 이하이어야 합니다.")
        if args.request_delay < 1.0:
            raise ConfigError("외부 요청 간격은 최소 1초이어야 합니다.")
        if not args.user_agent.strip():
            raise ConfigError("User-Agent는 비어 있을 수 없습니다.")
        static_sources = load_sources(args.sources.resolve())
        sources = runtime_sources(
            static_sources,
            args.source_registry.resolve(),
            args.source_health.resolve(),
        )
        fetch = make_network_fetcher(
            timeout_seconds=args.timeout,
            request_interval_seconds=args.request_delay,
            user_agent=args.user_agent,
        )
        result = collect_once(
            sources=sources,
            data_dir=args.data_dir.resolve(),
            fetch=fetch,
            now=datetime.now(SEOUL),
            max_candidates=args.max_candidates,
        )
        result["health"] = update_health(
            args.source_health.resolve(),
            args.source_registry.resolve(),
            result["sources"],
            datetime.now(SEOUL),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "success" else 2
    except (CollectorError, SourceManagerError) as exc:
        print(f"수집기 오류: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"예상하지 못한 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
