from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from process import ProcessingDataError, load_cases


def _inline_json(payload: Any) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def render_html(cases: list[dict[str, Any]]) -> str:
    ordered = sorted(
        cases,
        key=lambda card: (card["collected_at"], card["id"]),
        reverse=True,
    )
    inline_cases = _inline_json(ordered)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <meta name="description" content="매일 수집한 구체적인 AI 활용사례 개인 피드">
  <title>AI 활용사례 피드</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #090b11;
      --panel: #11141d;
      --panel-2: #171b27;
      --line: #272c3b;
      --text: #f4f5f8;
      --muted: #9aa3b7;
      --accent: #9b8cff;
      --accent-2: #62d6ae;
      --warn: #ffbd66;
      --shadow: 0 18px 50px rgba(0,0,0,.28);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at 50% -12rem, rgba(115,92,255,.18), transparent 28rem),
        var(--bg);
      color: var(--text);
      font-family: Pretendard, "Noto Sans KR", "Malgun Gothic", system-ui, sans-serif;
      line-height: 1.62;
      word-break: keep-all;
    }}
    button, select, input {{ font: inherit; }}
    button, select, label {{ min-height: 44px; }}
    button, select {{
      border: 1px solid var(--line);
      border-radius: 12px;
      color: var(--text);
      background: var(--panel-2);
    }}
    button {{ cursor: pointer; }}
    button:focus-visible, select:focus-visible, a:focus-visible, input:focus-visible {{
      outline: 3px solid rgba(155,140,255,.42);
      outline-offset: 2px;
    }}
    .app {{ width: min(640px, 100%); margin: 0 auto; padding: 0 16px 80px; }}
    header {{ padding: 48px 4px 26px; }}
    .eyebrow {{
      color: var(--accent-2);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: .1em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 8px;
      font-size: clamp(31px, 8vw, 46px);
      line-height: 1.12;
      letter-spacing: -.045em;
    }}
    header p {{ margin: 0; color: var(--muted); }}
    .filter-shell {{
      position: sticky;
      top: 0;
      z-index: 20;
      margin: 0 -16px 20px;
      padding: 10px 16px 12px;
      border-bottom: 1px solid rgba(39,44,59,.84);
      background: rgba(9,11,17,.92);
      backdrop-filter: blur(16px);
    }}
    .filter-row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }}
    select {{ width: 100%; padding: 0 12px; }}
    .toggles {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 8px;
    }}
    .toggle {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 0 11px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel);
      color: var(--muted);
      font-size: 13px;
      cursor: pointer;
      user-select: none;
    }}
    .toggle:has(input:checked) {{
      border-color: rgba(155,140,255,.72);
      background: rgba(155,140,255,.13);
      color: #ddd8ff;
    }}
    .toggle input {{ margin: 0; accent-color: var(--accent); }}
    .status {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin: 13px 2px 0;
      color: var(--muted);
      font-size: 12px;
    }}
    .feed {{ display: grid; gap: 14px; }}
    .card {{
      position: relative;
      overflow: hidden;
      padding: 21px 19px 17px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: linear-gradient(145deg, rgba(24,28,40,.98), rgba(15,18,26,.98));
      box-shadow: var(--shadow);
      transition: opacity .18s ease, border-color .18s ease, transform .18s ease;
    }}
    .card:hover {{ border-color: #3a4053; transform: translateY(-1px); }}
    .card.is-read {{ opacity: .52; box-shadow: none; }}
    .card-head {{ display: grid; grid-template-columns: 1fr 44px; gap: 10px; align-items: start; }}
    h2 {{ margin: 0; font-size: 20px; line-height: 1.36; letter-spacing: -.02em; }}
    .star {{
      width: 44px;
      height: 44px;
      padding: 0;
      color: #798197;
      font-size: 23px;
    }}
    .star.is-on {{ color: #ffd36d; border-color: rgba(255,211,109,.42); }}
    .summary {{ margin: 13px 0 15px; color: #c9cede; font-size: 14px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 27px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #222738;
      color: #bdc4d5;
      font-size: 11px;
      font-weight: 800;
    }}
    .badge.tool {{ color: #d8d2ff; background: rgba(155,140,255,.16); }}
    .badge.action {{ color: #a7f0d4; background: rgba(98,214,174,.13); }}
    .badge.hard {{ color: #ffd8a4; background: rgba(255,189,102,.12); }}
    .card-foot {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 17px;
      padding-top: 13px;
      border-top: 1px solid rgba(39,44,59,.78);
    }}
    .meta {{ margin-right: auto; color: #7f899e; font-size: 11px; }}
    .read-button {{
      min-width: 72px;
      padding: 0 12px;
      color: #b9c1d2;
      font-size: 12px;
      font-weight: 800;
    }}
    .source {{
      display: inline-grid;
      place-items: center;
      min-height: 44px;
      padding: 0 12px;
      border: 1px solid rgba(155,140,255,.35);
      border-radius: 12px;
      color: #d8d2ff;
      text-decoration: none;
      font-size: 12px;
      font-weight: 900;
      background: rgba(155,140,255,.08);
    }}
    .empty {{
      padding: 58px 24px;
      border: 1px dashed #343a4d;
      border-radius: 18px;
      color: var(--muted);
      text-align: center;
    }}
    .selftest {{
      display: none;
      margin-bottom: 12px;
      padding: 12px;
      border-radius: 10px;
      background: #12281f;
      color: #a7f0d4;
      font: 12px/1.4 ui-monospace, Consolas, monospace;
    }}
    body[data-selftest] .selftest {{ display: block; }}
    @media (max-width: 430px) {{
      .app {{ padding-inline: 12px; }}
      .filter-shell {{ margin-inline: -12px; padding-inline: 12px; }}
      header {{ padding-top: 34px; }}
      .card {{ padding: 18px 15px 14px; }}
      .card-foot {{ flex-wrap: wrap; }}
      .meta {{ flex-basis: 100%; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      * {{ scroll-behavior: auto !important; transition: none !important; }}
    }}
  </style>
</head>
<body>
  <main class="app">
    <header>
      <div class="eyebrow">Daily AI field notes</div>
      <h1>AI 활용사례 피드</h1>
      <p>뉴스가 아니라, 실제로 무엇을 만들고 자동화했는지만 모았습니다.</p>
    </header>

    <section class="filter-shell" aria-label="피드 필터">
      <div class="filter-row">
        <select id="toolFilter" aria-label="도구별 필터"><option value="">모든 도구</option></select>
        <select id="domainFilter" aria-label="도메인별 필터"><option value="">모든 도메인</option></select>
      </div>
      <div class="toggles">
        <label class="toggle"><input id="actionableOnly" type="checkbox"> 따라하기 쉬운 사례</label>
        <label class="toggle"><input id="bookmarkedOnly" type="checkbox"> ★ 북마크만</label>
      </div>
      <div class="status"><span id="countStatus">0건</span><span>최신순 · 로컬 저장</span></div>
    </section>

    <div id="selftest" class="selftest" role="status"></div>
    <section id="feed" class="feed" aria-live="polite"></section>
  </main>

  <script id="caseData" type="application/json">{inline_cases}</script>
  <script>
    (() => {{
      "use strict";
      const CASES = JSON.parse(document.getElementById("caseData").textContent);
      const KEYS = {{
        bookmarks: "ai-usecase-feed:bookmarks:v1",
        read: "ai-usecase-feed:read:v1"
      }};
      const byId = id => document.getElementById(id);
      const safeLoadSet = key => {{
        try {{
          const value = JSON.parse(localStorage.getItem(key) || "[]");
          return new Set(Array.isArray(value) ? value.filter(v => typeof v === "string") : []);
        }} catch (_) {{ return new Set(); }}
      }};
      const safeSaveSet = (key, set) => {{
        try {{ localStorage.setItem(key, JSON.stringify([...set])); return true; }}
        catch (_) {{ return false; }}
      }};
      const bookmarks = safeLoadSet(KEYS.bookmarks);
      const read = safeLoadSet(KEYS.read);

      const toolFilter = byId("toolFilter");
      const domainFilter = byId("domainFilter");
      const actionableOnly = byId("actionableOnly");
      const bookmarkedOnly = byId("bookmarkedOnly");
      const feed = byId("feed");
      const countStatus = byId("countStatus");

      const appendOptions = (select, values) => {{
        [...new Set(values)].sort((a, b) => a.localeCompare(b, "ko")).forEach(value => {{
          const option = document.createElement("option");
          option.value = value;
          option.textContent = value;
          select.append(option);
        }});
      }};
      appendOptions(toolFilter, CASES.flatMap(card => card.tool));
      appendOptions(domainFilter, CASES.map(card => card.domain));

      const makeBadge = (text, className = "") => {{
        const badge = document.createElement("span");
        badge.className = `badge ${{className}}`.trim();
        badge.textContent = text;
        return badge;
      }};

      const toggleBookmark = id => {{
        bookmarks.has(id) ? bookmarks.delete(id) : bookmarks.add(id);
        safeSaveSet(KEYS.bookmarks, bookmarks);
        render();
      }};
      const toggleRead = id => {{
        read.has(id) ? read.delete(id) : read.add(id);
        safeSaveSet(KEYS.read, read);
        render();
      }};

      const makeCard = card => {{
        const article = document.createElement("article");
        article.className = "card" + (read.has(card.id) ? " is-read" : "");
        article.dataset.id = card.id;

        const head = document.createElement("div");
        head.className = "card-head";
        const title = document.createElement("h2");
        title.textContent = card.title;
        const star = document.createElement("button");
        star.type = "button";
        star.className = "star" + (bookmarks.has(card.id) ? " is-on" : "");
        star.setAttribute("aria-label", bookmarks.has(card.id) ? "북마크 해제" : "북마크");
        star.setAttribute("aria-pressed", String(bookmarks.has(card.id)));
        star.textContent = bookmarks.has(card.id) ? "★" : "☆";
        star.addEventListener("click", () => toggleBookmark(card.id));
        head.append(title, star);

        const summary = document.createElement("p");
        summary.className = "summary";
        summary.textContent = card.summary;

        const badges = document.createElement("div");
        badges.className = "badges";
        card.tool.forEach(tool => badges.append(makeBadge(tool, "tool")));
        badges.append(makeBadge(card.domain));
        badges.append(makeBadge(card.difficulty, card.difficulty === "어려움" ? "hard" : ""));
        if (card.actionable) badges.append(makeBadge("따라하기 가능", "action"));

        const foot = document.createElement("div");
        foot.className = "card-foot";
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = `${{card.source_name}} · ${{card.collected_at}}`;
        const readButton = document.createElement("button");
        readButton.type = "button";
        readButton.className = "read-button";
        readButton.textContent = read.has(card.id) ? "읽음 취소" : "읽음";
        readButton.setAttribute("aria-pressed", String(read.has(card.id)));
        readButton.addEventListener("click", () => toggleRead(card.id));
        const source = document.createElement("a");
        source.className = "source";
        source.href = card.source_url;
        source.target = "_blank";
        source.rel = "noopener noreferrer";
        source.textContent = "원문 보기 ↗";
        source.addEventListener("click", () => {{
          if (!read.has(card.id)) {{
            read.add(card.id);
            safeSaveSet(KEYS.read, read);
          }}
        }});
        foot.append(meta, readButton, source);
        article.append(head, summary, badges, foot);
        return article;
      }};

      const filteredCases = () => CASES.filter(card =>
        (!toolFilter.value || card.tool.includes(toolFilter.value)) &&
        (!domainFilter.value || card.domain === domainFilter.value) &&
        (!actionableOnly.checked || card.actionable) &&
        (!bookmarkedOnly.checked || bookmarks.has(card.id))
      );

      function render() {{
        const visible = filteredCases();
        feed.replaceChildren();
        if (!visible.length) {{
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "조건에 맞는 사례가 없습니다.";
          feed.append(empty);
        }} else {{
          const fragment = document.createDocumentFragment();
          visible.forEach(card => fragment.append(makeCard(card)));
          feed.append(fragment);
        }}
        countStatus.textContent = `${{visible.length}}건 / 전체 ${{CASES.length}}건`;
      }}

      [toolFilter, domainFilter, actionableOnly, bookmarkedOnly]
        .forEach(control => control.addEventListener("change", render));

      const runSelfTest = () => {{
        const mode = new URLSearchParams(location.search).get("selftest");
        if (!mode) return;
        const status = byId("selftest");
        try {{
          if (!CASES.length) throw new Error("테스트할 카드가 없음");
          const id = CASES[0].id;
          if (mode === "write") {{
            bookmarks.add(id);
            read.add(id);
            if (!safeSaveSet(KEYS.bookmarks, bookmarks) || !safeSaveSet(KEYS.read, read)) {{
              throw new Error("localStorage 저장 실패");
            }}
            render();
          }}
          toolFilter.value = CASES[0].tool[0];
          domainFilter.value = CASES[0].domain;
          actionableOnly.checked = CASES[0].actionable;
          bookmarkedOnly.checked = true;
          render();
          const filtered = filteredCases();
          const filtersPassed =
            filtered.length > 0 &&
            filtered.every(card =>
              card.tool.includes(toolFilter.value) &&
              card.domain === domainFilter.value &&
              (!actionableOnly.checked || card.actionable) &&
              bookmarks.has(card.id)
            );
          toolFilter.value = "";
          domainFilter.value = "";
          actionableOnly.checked = false;
          bookmarkedOnly.checked = false;
          render();
          const card = feed.querySelector(`[data-id="${{CSS.escape(id)}}"]`);
          const touchTargets = [
            toolFilter,
            domainFilter,
            actionableOnly.closest("label"),
            bookmarkedOnly.closest("label"),
            card && card.querySelector(".star"),
            card && card.querySelector(".read-button"),
            card && card.querySelector(".source")
          ].filter(Boolean);
          const passed =
            filtersPassed &&
            bookmarks.has(id) &&
            read.has(id) &&
            !!card &&
            card.classList.contains("is-read") &&
            card.querySelector(".star").getAttribute("aria-pressed") === "true" &&
            touchTargets.every(target => target.getBoundingClientRect().height >= 44) &&
            toolFilter.options.length > 1 &&
            domainFilter.options.length > 1;
          if (!passed) throw new Error("상태 또는 필터 검증 실패");
          document.body.dataset.selftest = "PASS";
          status.textContent = `PASS:${{mode}}:cards=${{CASES.length}}`;
        }} catch (error) {{
          document.body.dataset.selftest = "FAIL";
          status.textContent = `FAIL:${{mode}}:${{error.message}}`;
        }}
      }};

      window.__feedTestApi = {{
        render,
        filteredCases,
        toggleBookmark,
        toggleRead,
        bookmarks,
        read
      }};
      render();
      runSelfTest();
    }})();
  </script>
</body>
</html>
"""


def build_site(cases_path: Path, output_path: Path) -> dict[str, Any]:
    cases = load_cases(cases_path)
    html = render_html(cases)
    _atomic_write_text(output_path, html)
    return {
        "status": "success",
        "cases": len(cases),
        "tools": len({tool for card in cases for tool in card["tool"]}),
        "domains": len({card["domain"] for card in cases}),
        "output": str(output_path),
        "bytes": output_path.stat().st_size,
        "self_contained": True,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="AI 활용사례 Phase 3 정적 사이트 빌더")
    parser.add_argument(
        "--cases",
        type=Path,
        default=project_dir / "data" / "cases.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "site" / "index.html",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_site(args.cases.resolve(), args.output.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ProcessingDataError, OSError) as exc:
        print(f"빌드 오류: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"예상하지 못한 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
