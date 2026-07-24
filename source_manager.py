from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import yaml


SEOUL = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = 1
DEFAULT_USER_AGENT = (
    "Caring-AI-Usecase-Source-Scout/1.0 "
    "(personal research collector; contact: local-owner)"
)
MAX_HTML_BYTES = 1_000_000
MAX_FEED_BYTES = 3_000_000
MAX_INDEX_BYTES = 2_000_000
MAX_REDIRECTS = 3
MAX_DISCOVERY_PAGES = 12
MAX_DISCOVERY_LINKS_PER_PAGE = 2
MAX_ACTIVE_DYNAMIC = 3
MAX_REGISTRY_SOURCES = 100
MAX_DISCOVERY_RUNS = 20
MAX_HEALTH_HISTORY = 30
WARNING_FAILURES = 3
PAUSE_FAILURES = 5
PROMOTION_MIN_SUCCESS_DATES = 2
PROMOTION_MIN_SPAN_DAYS = 7
PROMOTION_MIN_ITEMS = 10
PROMOTION_MIN_RECENT_ITEMS = 3
PROMOTION_MIN_SIGNAL_RATIO = 0.30
RECENT_DAYS = 30

AI_TERMS = (
    " ai ",
    "artificial intelligence",
    "machine learning",
    "llm",
    "gpt",
    "chatgpt",
    "claude",
    "copilot",
    "agent",
    "generative",
    "인공지능",
    "에이전트",
    "생성형",
)
USE_CASE_TERMS = (
    "built",
    "made",
    "using",
    "with ",
    "automated",
    "automation",
    "workflow",
    "case study",
    "how i",
    "production",
    "deployed",
    "만들",
    "자동화",
    "활용",
    "구축",
    "적용",
    "사용",
)


class SourceManagerError(RuntimeError):
    """Base error for source lifecycle failures."""


class StateIntegrityError(SourceManagerError):
    """Raised when persistent source state is malformed."""


class UnsafeURLError(SourceManagerError):
    """Raised when an external URL is not safe for automated fetching."""


class FetchError(SourceManagerError):
    """Raised when bounded HTTPS fetching fails."""


Resolver = Callable[..., list[tuple[Any, ...]]]
Fetcher = Callable[[str, str, int], tuple[bytes, str, str]]


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


def _default_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "sources": [],
        "retired_hosts": [],
        "discovery_runs": [],
    }


def _default_health() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "sources": {},
    }


def _load_json_object(path: Path, default: dict[str, Any], label: str) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateIntegrityError(
            f"{label}이 손상되었습니다. 자동 초기화하지 않습니다: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise StateIntegrityError(f"{label} 최상위는 객체여야 합니다: {path}")
    return value


def load_registry(path: Path) -> dict[str, Any]:
    value = _load_json_object(path, _default_registry(), "source_registry.json")
    value.setdefault("retired_hosts", [])
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("sources"), list)
        or not isinstance(value.get("retired_hosts"), list)
        or any(not isinstance(host, str) for host in value["retired_hosts"])
        or not isinstance(value.get("discovery_runs"), list)
    ):
        raise StateIntegrityError("source_registry.json 구조가 올바르지 않습니다.")
    known_ids: set[str] = set()
    for source in value["sources"]:
        if not isinstance(source, dict):
            raise StateIntegrityError("source_registry.json source는 객체여야 합니다.")
        required = ("id", "name", "type", "endpoint", "status")
        if any(not isinstance(source.get(field), str) for field in required):
            raise StateIntegrityError("source_registry.json source 필드가 잘못되었습니다.")
        if source["id"] in known_ids:
            raise StateIntegrityError("source_registry.json source id가 중복됩니다.")
        if source["type"] != "rss":
            raise StateIntegrityError("동적 소스는 rss만 허용됩니다.")
        if source["status"] not in {"probation", "active", "paused", "retired"}:
            raise StateIntegrityError("동적 소스 상태가 올바르지 않습니다.")
        known_ids.add(source["id"])
    return value


def load_health(path: Path) -> dict[str, Any]:
    value = _load_json_object(path, _default_health(), "source_health.json")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("sources"), dict)
    ):
        raise StateIntegrityError("source_health.json 구조가 올바르지 않습니다.")
    for name, state in value["sources"].items():
        if not isinstance(name, str) or not isinstance(state, dict):
            raise StateIntegrityError("source_health.json source가 잘못되었습니다.")
        if not isinstance(state.get("history", []), list):
            raise StateIntegrityError("source_health.json history가 잘못되었습니다.")
    return value


def load_discovery_indexes(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise SourceManagerError(f"discovery 설정을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("indexes"), list):
        raise SourceManagerError("discovery.yaml 최상위에는 indexes 목록이 필요합니다.")
    indexes: list[dict[str, str]] = []
    names: set[str] = set()
    for index, entry in enumerate(value["indexes"], start=1):
        if not isinstance(entry, dict):
            raise SourceManagerError(f"indexes[{index}]는 객체여야 합니다.")
        if any(
            not isinstance(entry.get(field), str) or not entry[field].strip()
            for field in ("name", "type", "endpoint")
        ):
            raise SourceManagerError(f"indexes[{index}] 필수 필드가 누락되었습니다.")
        if entry["type"] not in {"hn_algolia", "devto"}:
            raise SourceManagerError(f"지원하지 않는 discovery type: {entry['type']}")
        if entry["name"] in names:
            raise SourceManagerError(f"중복 discovery index: {entry['name']}")
        names.add(entry["name"])
        _validate_https_shape(entry["endpoint"])
        indexes.append(
            {
                "name": entry["name"].strip(),
                "type": entry["type"],
                "endpoint": entry["endpoint"].strip(),
            }
        )
    if not indexes:
        raise SourceManagerError("discovery index가 하나 이상 필요합니다.")
    return indexes


def _normalized_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    hostname = (parsed.hostname or "").lower()
    netloc = hostname
    if parsed.port and parsed.port != 443:
        netloc = f"{hostname}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit(("https", netloc, path, parsed.query, ""))


def _validate_https_shape(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL이 비어 있습니다.")
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        raise UnsafeURLError("HTTPS URL만 허용됩니다.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("userinfo가 포함된 URL은 허용되지 않습니다.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise UnsafeURLError("URL hostname이 없습니다.")
    try:
        if parsed.port not in {None, 443}:
            raise UnsafeURLError("HTTPS 표준 포트 443만 허용됩니다.")
    except ValueError as exc:
        raise UnsafeURLError("URL 포트가 올바르지 않습니다.") from exc
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise UnsafeURLError("IP literal URL은 허용되지 않습니다.")
    return _normalized_url(url)


def validate_public_https_url(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    normalized = _validate_https_shape(url)
    hostname = urlsplit(normalized).hostname or ""
    try:
        resolved = resolver(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURLError(f"DNS 해석에 실패했습니다: {hostname}") from exc
    addresses = {
        str(entry[4][0])
        for entry in resolved
        if len(entry) >= 5 and isinstance(entry[4], tuple) and entry[4]
    }
    if not addresses:
        raise UnsafeURLError(f"DNS 결과가 없습니다: {hostname}")
    for address in addresses:
        try:
            parsed_ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeURLError("DNS 결과 IP가 올바르지 않습니다.") from exc
        if not parsed_ip.is_global:
            raise UnsafeURLError(f"공개 인터넷 IP가 아닌 주소는 거부됩니다: {address}")
    return normalized


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_safe_fetcher(
    *,
    timeout_seconds: float = 10.0,
    user_agent: str = DEFAULT_USER_AGENT,
    resolver: Resolver = socket.getaddrinfo,
) -> Fetcher:
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("timeout_seconds는 0초 초과 30초 이하이어야 합니다.")
    opener = build_opener(_NoRedirect)

    def fetch(url: str, accept: str, max_bytes: int) -> tuple[bytes, str, str]:
        if max_bytes < 1:
            raise ValueError("max_bytes는 1 이상이어야 합니다.")
        current = validate_public_https_url(url, resolver=resolver)
        for redirect_count in range(MAX_REDIRECTS + 1):
            request = Request(
                current,
                headers={
                    "User-Agent": user_agent,
                    "Accept": accept,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            try:
                response = opener.open(request, timeout=timeout_seconds)
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    exc.close()
                    if not location:
                        raise FetchError("redirect Location이 없습니다.")
                    if redirect_count >= MAX_REDIRECTS:
                        raise FetchError("redirect 최대 3회를 초과했습니다.")
                    current = validate_public_https_url(
                        urljoin(current, location),
                        resolver=resolver,
                    )
                    continue
                exc.close()
                raise FetchError(f"HTTP {exc.code}: {current}") from exc
            except OSError as exc:
                raise FetchError(f"HTTPS 요청 실패: {current}: {exc}") from exc
            with response:
                payload = response.read(max_bytes + 1)
                content_type = str(response.headers.get("Content-Type") or "")
                final_url = validate_public_https_url(
                    response.geturl(),
                    resolver=resolver,
                )
            if len(payload) > max_bytes:
                raise FetchError(f"응답이 {max_bytes}바이트 제한을 초과했습니다.")
            return payload, final_url, content_type
        raise FetchError("redirect 처리에 실패했습니다.")

    return fetch


class _FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() != "link":
            return
        rel = {part.lower() for part in attributes.get("rel", "").split()}
        content_type = attributes.get("type", "").lower().split(";", 1)[0].strip()
        href = attributes.get("href", "").strip()
        if (
            "alternate" in rel
            and content_type in {"application/rss+xml", "application/atom+xml"}
            and href
        ):
            self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.title_parts)).strip()


def discover_feed_links(html_payload: bytes, page_url: str) -> tuple[list[str], str]:
    if len(html_payload) > MAX_HTML_BYTES:
        raise FetchError("HTML 응답 크기 제한을 초과했습니다.")
    parser = _FeedLinkParser()
    try:
        parser.feed(html_payload.decode("utf-8-sig", errors="replace"))
        parser.close()
    except Exception as exc:
        raise FetchError(f"HTML feed autodiscovery 파싱 실패: {exc}") from exc
    links: list[str] = []
    for href in parser.links:
        absolute = urljoin(page_url, href)
        if absolute not in links:
            links.append(absolute)
    return links[:MAX_DISCOVERY_LINKS_PER_PAGE], parser.title


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml_child_text(element: ElementTree.Element, names: set[str]) -> str:
    for child in list(element):
        if _xml_local_name(child.tag) in names:
            text = " ".join(part.strip() for part in child.itertext() if part.strip())
            if text:
                return re.sub(r"\s+", " ", text).strip()
    return ""


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_signal(title: str, summary: str) -> bool:
    haystack = f" {title} {summary} ".lower()
    return any(term in haystack for term in AI_TERMS) and any(
        term in haystack for term in USE_CASE_TERMS
    )


def inspect_feed(payload: bytes, now: datetime) -> dict[str, Any]:
    if len(payload) > MAX_FEED_BYTES:
        raise FetchError("feed 응답 크기 제한을 초과했습니다.")
    upper_payload = payload.upper()
    if b"<!DOCTYPE" in upper_payload or b"<!ENTITY" in upper_payload:
        raise FetchError("DOCTYPE/ENTITY가 포함된 XML은 거부됩니다.")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise FetchError(f"RSS/Atom XML 파싱 실패: {exc}") from exc
    entries = [
        element
        for element in root.iter()
        if _xml_local_name(element.tag) in {"item", "entry"}
    ][:20]
    if not entries:
        raise FetchError("RSS/Atom 항목이 없습니다.")
    feed_title = ""
    for element in root.iter():
        if _xml_local_name(element.tag) in {"channel", "feed"}:
            feed_title = _xml_child_text(element, {"title"})
            if feed_title:
                break
    recent_cutoff = now.astimezone(timezone.utc) - timedelta(days=RECENT_DAYS)
    signal_items = 0
    recent_items = 0
    for entry in entries:
        title = _xml_child_text(entry, {"title"})
        summary = _xml_child_text(
            entry,
            {"description", "summary", "content", "content:encoded"},
        )
        published = _xml_child_text(
            entry,
            {"pubdate", "published", "updated", "date"},
        )
        parsed_date = _parse_datetime(published)
        if parsed_date is not None and parsed_date >= recent_cutoff:
            recent_items += 1
        if _is_signal(title, summary):
            signal_items += 1
    return {
        "name": feed_title[:120],
        "sample_item_count": len(entries),
        "recent_item_count": recent_items,
        "signal_item_count": signal_items,
        "signal_ratio": round(signal_items / len(entries), 4),
    }


def _extract_index_urls(index: dict[str, str], payload: bytes) -> list[str]:
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"{index['name']} JSON 파싱 실패: {exc}") from exc
    urls: list[str] = []
    if index["type"] == "hn_algolia":
        rows = document.get("hits") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise FetchError("HN Algolia hits 목록이 없습니다.")
        values = [
            row.get("url")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("url"), str)
        ]
    else:
        if not isinstance(document, list):
            raise FetchError("DEV API 결과가 목록이 아닙니다.")
        values = []
        for row in document:
            if not isinstance(row, dict):
                continue
            canonical = row.get("canonical_url")
            article_url = row.get("url")
            values.append(canonical or article_url)
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            continue
        normalized = urlunsplit(
            ("https", parsed.netloc, parsed.path or "/", parsed.query, "")
        )
        if normalized not in urls:
            urls.append(normalized)
    return urls


def _source_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:20]


def _site_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _success_span_days(success_dates: list[str]) -> int:
    parsed: list[datetime] = []
    for value in success_dates:
        try:
            parsed.append(datetime.fromisoformat(value))
        except ValueError:
            continue
    if len(parsed) < 2:
        return 0
    return (max(parsed).date() - min(parsed).date()).days


def _qualifies_for_promotion(source: dict[str, Any]) -> bool:
    success_dates = source.get("success_dates", [])
    return (
        isinstance(success_dates, list)
        and len(set(success_dates)) >= PROMOTION_MIN_SUCCESS_DATES
        and _success_span_days(success_dates) >= PROMOTION_MIN_SPAN_DAYS
        and int(source.get("sample_item_count", 0)) >= PROMOTION_MIN_ITEMS
        and int(source.get("recent_item_count", 0)) >= PROMOTION_MIN_RECENT_ITEMS
        and float(source.get("signal_ratio", 0.0)) >= PROMOTION_MIN_SIGNAL_RATIO
    )


def _active_count(registry: dict[str, Any]) -> int:
    return sum(1 for source in registry["sources"] if source["status"] == "active")


def _compact_registry(registry: dict[str, Any], reserve: int = 0) -> None:
    target = max(0, MAX_REGISTRY_SOURCES - max(0, reserve))
    remove_count = max(0, len(registry["sources"]) - target)
    if not remove_count:
        return
    retired = sorted(
        (
            source
            for source in registry["sources"]
            if source["status"] == "retired"
        ),
        key=lambda source: source.get("last_checked_at") or "",
    )
    removable = retired[:remove_count]
    if not removable:
        return
    removable_ids = {source["id"] for source in removable}
    retired_hosts = list(registry["retired_hosts"])
    for source in removable:
        host = source.get("site_host") or _site_host(source["homepage"])
        if host and host not in retired_hosts:
            retired_hosts.append(host)
    registry["retired_hosts"] = retired_hosts[-500:]
    registry["sources"] = [
        source
        for source in registry["sources"]
        if source["id"] not in removable_ids
    ]


def _record_probe_success(
    source: dict[str, Any],
    inspection: dict[str, Any],
    now: datetime,
) -> None:
    date_text = now.astimezone(SEOUL).date().isoformat()
    dates = [
        value
        for value in source.get("success_dates", [])
        if isinstance(value, str)
    ]
    if date_text not in dates:
        dates.append(date_text)
    source["success_dates"] = dates[-12:]
    source["successful_checks"] = int(source.get("successful_checks", 0)) + 1
    source["consecutive_failures"] = 0
    source["last_checked_at"] = now.isoformat()
    source["last_success_at"] = now.isoformat()
    source["last_error"] = None
    if not source.get("name") and inspection.get("name"):
        source["name"] = inspection["name"]
    source.update(
        {key: value for key, value in inspection.items() if key != "name"}
    )


def _record_probe_failure(source: dict[str, Any], exc: Exception, now: datetime) -> None:
    failures = int(source.get("consecutive_failures", 0)) + 1
    source["consecutive_failures"] = failures
    source["last_checked_at"] = now.isoformat()
    source["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
    if source["status"] == "probation" and failures >= 4:
        source["status"] = "retired"
        source["status_reason"] = "probation_probe_failed_4_times"


def _promote_qualified_sources(registry: dict[str, Any], now: datetime) -> list[str]:
    promoted: list[str] = []
    candidates = sorted(
        (
            source
            for source in registry["sources"]
            if source["status"] == "probation" and _qualifies_for_promotion(source)
        ),
        key=lambda source: (
            -float(source.get("signal_ratio", 0.0)),
            -int(source.get("recent_item_count", 0)),
            source["id"],
        ),
    )
    for source in candidates:
        if _active_count(registry) >= MAX_ACTIVE_DYNAMIC:
            source["status_reason"] = "qualified_waiting_for_active_slot"
            continue
        source["status"] = "active"
        source["status_reason"] = "promotion_thresholds_met"
        source["activated_at"] = now.isoformat()
        promoted.append(source["name"])
    return promoted


def _load_static_source_rows(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise SourceManagerError(f"sources.yaml을 읽을 수 없습니다: {exc}") from exc
    rows = value.get("sources") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise SourceManagerError("sources.yaml sources 목록이 없습니다.")
    return [
        dict(row)
        for row in rows
        if isinstance(row, dict) and row.get("enabled", True)
    ]


def _probe_paused_static_sources(
    static_sources: list[dict[str, Any]],
    health: dict[str, Any],
    fetch: Fetcher,
    now: datetime,
) -> list[str]:
    recovered: list[str] = []
    for source in static_sources:
        name = str(source.get("name") or "")
        health_state = health["sources"].get(name)
        if not isinstance(health_state, dict) or health_state.get("status") != "paused":
            continue
        try:
            payload, _, _ = fetch(
                str(source["endpoint"]),
                "application/json, application/rss+xml, application/atom+xml, "
                "application/xml",
                MAX_FEED_BYTES,
            )
            if source.get("type") == "rss":
                inspect_feed(payload, now)
            else:
                json.loads(payload.decode("utf-8-sig"))
            health_state["status"] = "healthy"
            health_state["consecutive_failures"] = 0
            health_state["last_recovered_at"] = now.isoformat()
            recovered.append(name)
        except Exception as exc:
            health_state["last_probe_error"] = f"{type(exc).__name__}: {exc}"[:500]
            health_state["last_probe_at"] = now.isoformat()
    return recovered


def _probe_registry_sources(
    registry: dict[str, Any],
    health: dict[str, Any],
    fetch: Fetcher,
    now: datetime,
) -> list[str]:
    probed: list[str] = []
    for source in registry["sources"]:
        if source["status"] not in {"probation", "paused"}:
            continue
        try:
            payload, final_url, _ = fetch(
                source["endpoint"],
                "application/rss+xml, application/atom+xml, application/xml, text/xml",
                MAX_FEED_BYTES,
            )
            source["endpoint"] = final_url
            inspection = inspect_feed(payload, now)
            _record_probe_success(source, inspection, now)
            if source["status"] == "paused":
                health_state = health["sources"].get(source["name"])
                if isinstance(health_state, dict):
                    health_state["status"] = "healthy"
                    health_state["consecutive_failures"] = 0
                    health_state["last_recovered_at"] = now.isoformat()
                    health_state["last_error"] = None
                source["status"] = "probation"
                source["status_reason"] = "paused_probe_recovered_pending_slot"
            probed.append(source["name"])
        except Exception as exc:
            _record_probe_failure(source, exc, now)
    return probed


def _new_candidate(
    endpoint: str,
    page_url: str,
    page_title: str,
    index_name: str,
    inspection: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    name = inspection.get("name") or page_title or _site_host(page_url)
    return {
        "id": _source_id(endpoint),
        "name": str(name)[:120],
        "type": "rss",
        "endpoint": endpoint,
        "homepage": page_url,
        "site_host": _site_host(page_url),
        "status": "probation",
        "status_reason": "newly_discovered",
        "discovered_by": index_name,
        "discovered_at": now.isoformat(),
        "last_checked_at": now.isoformat(),
        "last_success_at": now.isoformat(),
        "last_error": None,
        "successful_checks": 1,
        "consecutive_failures": 0,
        "success_dates": [now.astimezone(SEOUL).date().isoformat()],
        **{
            key: value
            for key, value in inspection.items()
            if key != "name"
        },
    }


def discover_once(
    *,
    indexes: list[dict[str, str]],
    static_sources: list[dict[str, Any]],
    registry_path: Path,
    health_path: Path,
    fetch: Fetcher,
    now: datetime,
    max_pages: int = MAX_DISCOVERY_PAGES,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not 1 <= max_pages <= MAX_DISCOVERY_PAGES:
        raise ValueError(f"max_pages는 1~{MAX_DISCOVERY_PAGES} 범위여야 합니다.")
    registry = load_registry(registry_path)
    health = load_health(health_path)
    _compact_registry(registry, reserve=max_pages)
    recovered_static = _probe_paused_static_sources(
        static_sources, health, fetch, now
    )
    probed = _probe_registry_sources(registry, health, fetch, now)

    static_hosts = {
        _site_host(str(source.get("endpoint") or ""))
        for source in static_sources
    }
    blocked_hosts = set(registry["retired_hosts"])
    blocked_hosts.update(
        source.get("site_host") or _site_host(source["homepage"])
        for source in registry["sources"]
        if source["status"] == "retired"
    )
    known_endpoints = {
        _normalized_url(source["endpoint"]): source for source in registry["sources"]
    }
    known_hosts = {
        source.get("site_host") or _site_host(source["homepage"])
        for source in registry["sources"]
        if source["status"] != "retired"
    }

    index_results: list[dict[str, Any]] = []
    index_url_lists: list[tuple[str, list[str]]] = []
    for index in indexes:
        record = {"name": index["name"], "status": "success", "urls": 0, "error": None}
        try:
            payload, _, _ = fetch(
                index["endpoint"],
                "application/json",
                MAX_INDEX_BYTES,
            )
            urls = _extract_index_urls(index, payload)
            record["urls"] = len(urls)
            index_url_lists.append((index["name"], urls))
        except Exception as exc:
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        index_results.append(record)

    page_candidates: list[tuple[str, str]] = []
    seen_page_urls: set[str] = set()
    max_index_urls = max(
        (len(urls) for _, urls in index_url_lists),
        default=0,
    )
    for url_rank in range(max_index_urls):
        for index_name, urls in index_url_lists:
            if len(page_candidates) >= max_pages:
                break
            if url_rank >= len(urls):
                continue
            url = urls[url_rank]
            if url in seen_page_urls:
                continue
            seen_page_urls.add(url)
            page_candidates.append((index_name, url))
        if len(page_candidates) >= max_pages:
            break

    discovered: list[str] = []
    rejected = 0
    page_errors = 0
    for index_name, page_url in page_candidates:
        try:
            page_payload, final_page_url, _ = fetch(
                page_url,
                "text/html, application/xhtml+xml",
                MAX_HTML_BYTES,
            )
            feed_links, page_title = discover_feed_links(
                page_payload, final_page_url
            )
        except Exception:
            page_errors += 1
            continue
        for feed_url in feed_links:
            try:
                safe_endpoint = _validate_https_shape(feed_url)
                endpoint_host = _site_host(safe_endpoint)
                if (
                    endpoint_host in static_hosts
                    or endpoint_host in blocked_hosts
                    or endpoint_host in known_hosts
                    or safe_endpoint in known_endpoints
                ):
                    rejected += 1
                    continue
                payload, final_endpoint, _ = fetch(
                    safe_endpoint,
                    "application/rss+xml, application/atom+xml, "
                    "application/xml, text/xml",
                    MAX_FEED_BYTES,
                )
                if _site_host(final_endpoint) in blocked_hosts:
                    rejected += 1
                    continue
                inspection = inspect_feed(payload, now)
                if (
                    inspection["sample_item_count"] < 3
                    or inspection["recent_item_count"] < 1
                    or inspection["signal_item_count"] < 1
                ):
                    rejected += 1
                    continue
                candidate = _new_candidate(
                    final_endpoint,
                    final_page_url,
                    page_title,
                    index_name,
                    inspection,
                    now,
                )
                used_names = {
                    str(source.get("name") or "")
                    for source in registry["sources"]
                }
                used_names.update(
                    str(source.get("name") or "")
                    for source in static_sources
                )
                if candidate["name"] in used_names:
                    candidate["name"] = (
                        f"{candidate['name']} ({candidate['site_host']})"
                    )[:120]
                if len(registry["sources"]) >= MAX_REGISTRY_SOURCES:
                    rejected += 1
                    continue
                registry["sources"].append(candidate)
                known_endpoints[final_endpoint] = candidate
                known_hosts.add(candidate["site_host"])
                discovered.append(candidate["name"])
                break
            except Exception:
                rejected += 1

    _compact_registry(registry)

    promoted = _promote_qualified_sources(registry, now)
    success_indexes = sum(1 for row in index_results if row["status"] == "success")
    run_record = {
        "started_at": now.isoformat(),
        "finished_at": datetime.now(SEOUL).isoformat(),
        "status": "success" if success_indexes else "failed",
        "indexes": index_results,
        "pages_considered": len(page_candidates),
        "page_errors": page_errors,
        "discovered_count": len(discovered),
        "rejected_count": rejected,
        "probed_count": len(probed),
        "promoted_count": len(promoted),
        "recovered_static_count": len(recovered_static),
        "dry_run": dry_run,
    }
    registry["discovery_runs"].append(run_record)
    registry["discovery_runs"] = registry["discovery_runs"][-MAX_DISCOVERY_RUNS:]
    registry["updated_at"] = run_record["finished_at"]
    health["updated_at"] = run_record["finished_at"]
    if not dry_run:
        _atomic_write_json(registry_path, registry)
        _atomic_write_json(health_path, health)
    return {
        "status": run_record["status"],
        "discovered": discovered,
        "promoted": promoted,
        "probed": probed,
        "recovered_static": recovered_static,
        "registry_sources": len(registry["sources"]),
        "active_dynamic": _active_count(registry),
        "index_results": index_results,
        "pages_considered": len(page_candidates),
        "page_errors": page_errors,
        "rejected_count": rejected,
        "dry_run": dry_run,
    }


def runtime_sources(
    static_sources: list[dict[str, Any]],
    registry_path: Path,
    health_path: Path,
) -> list[dict[str, Any]]:
    registry = load_registry(registry_path)
    health = load_health(health_path)
    selected: list[dict[str, Any]] = []
    names: set[str] = set()
    endpoints: set[str] = set()
    for source in static_sources:
        name = str(source.get("name") or "")
        state = health["sources"].get(name, {})
        if isinstance(state, dict) and state.get("status") == "paused":
            continue
        selected.append(dict(source))
        names.add(name)
        endpoints.add(_normalized_url(str(source["endpoint"])))
    for source in registry["sources"]:
        if source["status"] != "active":
            continue
        state = health["sources"].get(source["name"], {})
        if isinstance(state, dict) and state.get("status") == "paused":
            continue
        endpoint = _validate_https_shape(source["endpoint"])
        if source["name"] in names or endpoint in endpoints:
            continue
        selected.append(
            {
                "name": source["name"],
                "type": "rss",
                "endpoint": source["endpoint"],
                "enabled": True,
                "dynamic": True,
            }
        )
        names.add(source["name"])
        endpoints.add(endpoint)
    if not selected:
        raise StateIntegrityError("건강한 활성 소스가 하나도 없습니다.")
    return selected


def update_health(
    health_path: Path,
    registry_path: Path,
    source_stats: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    health = load_health(health_path)
    registry = load_registry(registry_path)
    dynamic_by_name = {source["name"]: source for source in registry["sources"]}
    paused_names: list[str] = []
    warning_names: list[str] = []
    for stat in source_stats:
        name = str(stat.get("name") or "")
        if not name:
            continue
        state = health["sources"].setdefault(
            name,
            {
                "kind": "dynamic" if name in dynamic_by_name else "static",
                "status": "healthy",
                "total_runs": 0,
                "successful_runs": 0,
                "failed_runs": 0,
                "consecutive_failures": 0,
                "total_fetched": 0,
                "total_selected": 0,
                "total_duplicates": 0,
                "history": [],
            },
        )
        state["total_runs"] = int(state.get("total_runs", 0)) + 1
        success = stat.get("status") == "success"
        if success:
            state["successful_runs"] = int(state.get("successful_runs", 0)) + 1
            state["consecutive_failures"] = 0
            state["status"] = "healthy"
            state["last_success_at"] = now.isoformat()
            state["last_error"] = None
        else:
            state["failed_runs"] = int(state.get("failed_runs", 0)) + 1
            failures = int(state.get("consecutive_failures", 0)) + 1
            state["consecutive_failures"] = failures
            state["last_failure_at"] = now.isoformat()
            state["last_error"] = str(stat.get("error") or "")[:500]
            state["status"] = "failing"
            if failures >= PAUSE_FAILURES:
                state["status"] = "paused"
                paused_names.append(name)
                dynamic = dynamic_by_name.get(name)
                if dynamic and dynamic["status"] == "active":
                    dynamic["status"] = "paused"
                    dynamic["status_reason"] = "daily_fetch_failed_5_times"
            elif failures >= WARNING_FAILURES:
                state["status"] = "warning"
                warning_names.append(name)
        state["total_fetched"] = int(state.get("total_fetched", 0)) + int(
            stat.get("fetched", 0)
        )
        state["total_selected"] = int(state.get("total_selected", 0)) + int(
            stat.get("selected", 0)
        )
        state["total_duplicates"] = int(state.get("total_duplicates", 0)) + int(
            stat.get("duplicate", 0)
        )
        state["last_run_at"] = now.isoformat()
        history = state.setdefault("history", [])
        history.append(
            {
                "at": now.isoformat(),
                "status": stat.get("status"),
                "fetched": int(stat.get("fetched", 0)),
                "selected": int(stat.get("selected", 0)),
                "duplicate": int(stat.get("duplicate", 0)),
                "error": str(stat.get("error") or "")[:300] or None,
            }
        )
        state["history"] = history[-MAX_HEALTH_HISTORY:]
    health["updated_at"] = now.isoformat()
    registry["updated_at"] = now.isoformat()
    _atomic_write_json(health_path, health)
    _atomic_write_json(registry_path, registry)
    return {
        "paused": paused_names,
        "warning": warning_names,
        "source_count": len(health["sources"]),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="AI 활용사례 피드 동적 소스 발굴·시험·복구 관리자"
    )
    parser.add_argument("command", choices=("discover",))
    parser.add_argument(
        "--discovery-config",
        type=Path,
        default=project_dir / "discovery.yaml",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=project_dir / "sources.yaml",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=project_dir / "data" / "source_registry.json",
    )
    parser.add_argument(
        "--health",
        type=Path,
        default=project_dir / "data" / "source_health.json",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-pages", type=int, default=MAX_DISCOVERY_PAGES)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        indexes = load_discovery_indexes(args.discovery_config.resolve())
        static_sources = _load_static_source_rows(args.sources.resolve())
        result = discover_once(
            indexes=indexes,
            static_sources=static_sources,
            registry_path=args.registry.resolve(),
            health_path=args.health.resolve(),
            fetch=make_safe_fetcher(timeout_seconds=args.timeout),
            now=datetime.now(SEOUL),
            max_pages=args.max_pages,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "success" else 2
    except SourceManagerError as exc:
        print(f"소스 관리자 오류: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"예상하지 못한 소스 관리자 오류: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
