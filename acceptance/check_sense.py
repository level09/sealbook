"""Read every analyst page of the demo case and flag text no analyst should see.

"Renders without errors" passed while pages showed ids, storage words, raw scores,
repeated rows and broken plurals. This crawls the pages a reader opens, at desktop
and phone width, and runs plain text rules over what is visible.

Run against a server you started on a copy of the demo database:
  uv run python -m acceptance.check_sense --base http://localhost:5023
Exit 1 when any finding is not in acceptance/sense_allow.json.
"""

import argparse
import asyncio
import contextlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import TimeoutError as PlaywrightTimeout

from stk.ledger.contracts import Prop
from stk.runs.worker import TOOLS

ROOT = Path(__file__).resolve().parent.parent
ALLOW = Path(__file__).with_name("sense_allow.json")
WIDTHS = {"desktop": (1440, 1000), "phone": (390, 844)}
SEARCHES = ["حسن", "Branch 999", "arrested at the checkpoint", "سجن"]

# --- deny list, built from the code so new props and tools are covered ----------


def _model_ids():
    ids = set()
    for path in (ROOT / "stk").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        ids |= set(re.findall(r'"model_(?:requested|served)":\s*"([^"]+)"', text))
        ids |= set(re.findall(r'^FIXED\s*=\s*"([^"]+)"', text, re.M))
    return ids


def deny_phrases():
    """Storage words as they leak into sentences: '<prop> values', '<tool> run',
    the importer actor, and model ids."""
    props = {str(p).replace("_", " ") for p in Prop}
    phrases = {rf"\b{re.escape(p)} values?\b" for p in props}
    phrases |= {rf"\b{re.escape(t.replace('_', ' '))} run\b" for t in TOOLS}
    phrases |= {rf"\b{re.escape(t)} run\b" for t in TOOLS}
    phrases |= {r"\bimport by importer\b", r"\bby importer\b"}
    phrases |= {re.escape(m) for m in _model_ids()}
    return re.compile("|".join(sorted(phrases, key=len, reverse=True)), re.I)


DENY = deny_phrases()

# --- rules: text in, offending snippets out --------------------------------------

UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)
HEX = re.compile(r"[0-9a-f]{6,}", re.I)
SNAKE = re.compile(r"(?<![\w./@-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w@-]|\.\w)")
SCORE = re.compile(
    r"\b(?:distance|lead|cosine|score|similarity|confidence|sim)\b\s*[:=]?\s*-?\d*\.\d+",
    re.I,
)
ONE = re.compile(r"(?<![\d.,:/-])\b1[ \t]+([A-Za-z]+s)\b")
NOT_PLURAL = {
    "is",
    "was",
    "has",
    "this",
    "of",
    "news",
    "analysis",
    "status",
    "class",
    "bus",
    "access",
    "process",
    "address",
    "witness",
    "basis",
    "axis",
    "plus",
    "minus",
    "always",
    "across",
    "less",
    "its",
    "yes",
    "us",
    "thus",
    "gps",
    "ms",
    "mbps",
}
# Flex rows put each item on its own line in innerText, so a separator that starts
# or ends a line is usually drawn beside its neighbour; only flag it mid-line.
SEPARATORS = re.compile(r"\s\.\s|·[ \t]*·|,[ \t]*,|\([ \t]*\)|[ \t],[ \t]")
# A row of a sheet shown as the source text: commas are its content.
CSV_LINE = re.compile(r"^(?:[^,\n]*,(?! )){3,}", re.M)
# "Born: · Died:" or "..., Died:" at the end of a line; a heading such as "Next:"
# alone on its line is followed by its value on the next one.
EMPTY_LABEL = re.compile(r"[^\W\d_]:[ \t]*[·,;)]|\S[ \t]+[^\W\d_]+:[ \t]*$", re.M)
LITERALS = re.compile(r"\bundefined\b|\bnull\b|\bNaN\b|\[object Object\]")
# A hash that is the deliberate content is labelled as one.
HASH_LABEL = re.compile(r"(?:sha-?256|sha|md5|hash|digest|fingerprint)\W{0,3}$", re.I)


def _around(text, start, end, pad=30):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    a, b = max(line_start, start - pad), min(line_end, end + pad)
    return text[a:b].strip()


def raw_uuid(text):
    return [_around(text, m.start(), m.end()) for m in UUID.finditer(text)]


def raw_hex_id(text, expected=()):
    """A run of 6+ hex chars holding both digits and letters, alone or glued to a
    word: 'Request 1fbaac41', 'texteb148f8f'. Uuids are their own rule."""
    text = UUID.sub(" ", text)
    out = []
    for m in HEX.finditer(text):
        token = m.group()
        if not (re.search(r"\d", token) and re.search(r"[a-f]", token, re.I)):
            continue
        if sum(c.isdigit() for c in token) < 2:
            continue
        if any(
            token.lower() in e.lower() or e.lower() in token.lower() for e in expected
        ):
            continue
        if HASH_LABEL.search(text[max(0, m.start() - 20) : m.start()]):
            continue
        out.append(_around(text, m.start(), m.end()))
    return out


def snake_case(text):
    return [_around(text, m.start(), m.end()) for m in SNAKE.finditer(text)]


def internal_words(text):
    return [_around(text, m.start(), m.end()) for m in DENY.finditer(text)]


def raw_score(text):
    return [_around(text, m.start(), m.end()) for m in SCORE.finditer(text)]


def one_plural(text):
    out = []
    for m in ONE.finditer(text):
        word = m.group(1).lower()
        if word in NOT_PLURAL or word.endswith(("ss", "us", "is")):
            continue
        out.append(_around(text, m.start(), m.end()))
    return out


def stray_separator(text):
    text = CSV_LINE.sub(lambda m: " " * len(m.group()), text)
    return [_around(text, m.start(), m.end()) for m in SEPARATORS.finditer(text)]


def empty_label(text):
    return [_around(text, m.start(), m.end()) for m in EMPTY_LABEL.finditer(text)]


def broken_value(text):
    return [_around(text, m.start(), m.end()) for m in LITERALS.finditer(text)]


def repeated_rows(lists, at_least=3):
    """Lists are the visible texts of sibling rows; the same row 3+ times in one
    list is a duplicate the reader must skim past."""
    out = []
    for rows in lists:
        rows = [r for r in rows if len(r) > 3 and re.search(r"[^\W\d_]", r)]
        for row, n in Counter(rows).items():
            if n >= at_least:
                out.append(f"{row[:80]} (x{n})")
    return sorted(set(out))


COUNTED = re.compile(r"\b(\d+) facts? from (\d+) documents?\b")


def count_mismatch(counts):
    """counts: (person, label, screen, facts, documents) rows. The People list row
    for the same scope is the reference: the person page against the list of every
    case, search against the list of the case it searched. Any other count for the
    same person is a finding: the reader sees two numbers for one thing."""
    ref = {(c[0], c[2]): c for c in counts if c[2].startswith("People list")}
    out = []
    for person, label, screen, facts, docs in counts:
        if screen.startswith("People list"):
            continue
        scope = "People list, all cases" if screen == "person page" else "People list"
        base = ref.get((person, scope))
        if base and (base[3], base[4]) != (facts, docs):
            out.append(
                f"{label}: {scope} {base[3]} facts from {base[4]} documents, "
                f"{screen} {facts} facts from {docs} documents"
            )
    return out


TEXT_RULES = {
    "uuid": raw_uuid,
    "hex-id": raw_hex_id,
    "snake-case": snake_case,
    "internal-word": internal_words,
    "raw-score": raw_score,
    "one-plural": one_plural,
    "separator": stray_separator,
    "empty-label": empty_label,
    "broken-value": broken_value,
}
MESSAGES = {
    "uuid": "a uuid is shown; name the thing instead",
    "hex-id": "a raw id or hash fragment is shown in running text",
    "snake-case": "a storage token is shown instead of words",
    "internal-word": "a prop, run kind or model id reads as a sentence",
    "raw-score": "a raw model score is shown; use a band word",
    "one-plural": "count 1 with a plural noun",
    "separator": "a separator with nothing on one side",
    "empty-label": "a label with no value",
    "broken-value": "a missing value printed as code",
    "repeated-row": "the same row 3+ times in one list",
    "count-mismatch": "one person counted differently on two screens",
}


def check_page(page, allow=()):
    """page: {"page", "text", "lists", "expected"}; returns findings not allowed."""
    found = []
    for rule, fn in TEXT_RULES.items():
        args = (
            (page["text"], page.get("expected", ()))
            if rule == "hex-id"
            else (page["text"],)
        )
        found += [(rule, s) for s in fn(*args)]
    found += [("repeated-row", s) for s in repeated_rows(page.get("lists", []))]
    found += [("count-mismatch", s) for s in count_mismatch(page.get("counts", []))]
    counts = Counter(found)
    return [
        {"page": page["page"], "rule": rule, "snippet": snip, "count": n}
        for (rule, snip), n in counts.items()
        if not allowed(page["page"], rule, snip, allow)
    ]


def allowed(page, rule, snippet, allow):
    return any(
        a["rule"] == rule
        and re.search(a["page"], page)
        and re.search(a["match"], snippet)
        for a in allow
    )


def load_allow():
    entries = json.loads(ALLOW.read_text(encoding="utf-8"))
    for a in entries:
        assert a.get("reason", "").strip(), f"allowlist entry without a reason: {a}"
        assert a["rule"] in MESSAGES, f"unknown rule in allowlist: {a['rule']}"
    return entries


# --- crawl -----------------------------------------------------------------------

ROWS_JS = """() => {
  const shown = el => el.getClientRects().length > 0;
  const sig = k => k.tagName + '.' + [...k.classList]
    .filter(c => !/active|selected|hover|focus|open|current/.test(c)).sort().join('.');
  const out = [];
  for (const p of document.querySelectorAll('body *')) {
    const kids = [...p.children].filter(shown);
    if (kids.length < 3) continue;
    const groups = {};
    for (const k of kids) (groups[sig(k)] ||= []).push(k);
    for (const g of Object.values(groups))
      if (g.length >= 3) out.push(g.map(k => (k.innerText ?? k.textContent ?? '').trim().replace(/\\s+/g, ' ')));
  }
  return out;
}"""


async def settle(page):
    # The monitor keeps a live stream open, so networkidle may never come.
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(400)


async def read(page, name, expected=()):
    text = await page.inner_text("body")
    return {
        "page": name,
        "url": page.url,
        "text": text,
        "lists": await page.evaluate(ROWS_JS),
        "expected": list(expected),
    }


async def reveal(page):
    """Show each sensitive item, as an analyst reading the source would."""
    buttons = page.get_by_role("button", name="Show sensitive item (access is")
    for _ in range(await buttons.count()):
        await buttons.first.click()
        await settle(page)


async def links(page, prefix, n):
    hrefs = await page.eval_on_selector_all(
        f'a[href^="{prefix}"]', "as => as.map(a => a.getAttribute('href'))"
    )
    return list(dict.fromkeys(h.split("?")[0] for h in hrefs))[:n]


async def crawl_width(browser, base, token_url, case, width):
    ctx = await browser.new_context(
        viewport=dict(zip(("width", "height"), WIDTHS[width], strict=True))
    )
    await ctx.add_init_script(
        f"try {{ localStorage.setItem('sb.case', {json.dumps(case)}); }} catch (e) {{}}"
    )
    page = await ctx.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda e: errors.append(f"{page.url}: {e}"))
    await page.goto(base + token_url)
    await settle(page)
    pages = []

    async def visit(path, name=None, expected=()):
        await page.goto(base + path)
        await settle(page)
        pages.append(await read(page, f"{name or path} @{width}", expected))

    for path in [
        "/desk",
        "/inbox",
        "/discover",
        "/review",
        "/sources",
        "/stories",
        "/watch",
        "/packages",
        "/case-map",
        "/ingest/",
        "/guide",
        "/agents/",
    ]:
        await visit(path)
    resp = await page.request.get(
        f"{base}/api/agents/notice?investigation={quote(case)}"
    )
    noticed = [i for i in (await resp.json())["items"] if not i.get("locked")]
    # Sheets are read below by type; this adds the panel on another kind of source.
    noticed.sort(key=lambda i: i["here"]["title"].startswith("Family intake sheet"))
    if noticed:
        href = noticed[0]["here"]["href"]
        # The header shows the short hash on purpose, as on every evidence page.
        await visit(href, "/evidence/noticer", [href.rsplit("/", 1)[1]])

    await visit("/people")
    people = await links(page, "/people/", 5)
    await visit("/places")
    places = await links(page, "/places/", 3)
    for i, p in enumerate(people):
        await visit(p, f"/people/{i + 1}")
    incidents = []
    for i, p in enumerate(places):
        await visit(p, f"/places/{i + 1}")
        incidents += await links(page, "/incidents/", 3)
    resp = await page.request.get(
        f"{base}/api/discover/incidents?investigation={quote(case)}"
    )
    incidents += [
        f"/incidents/{x['id'].split(':', 1)[1]}" for x in (await resp.json())["items"]
    ]
    for i, p in enumerate(list(dict.fromkeys(incidents))[:3]):
        await visit(p, f"/incidents/{i + 1}")

    resp = await page.request.get(
        f"{base}/api/sources?investigation={quote(case)}&per_page=100"
    )
    sources = (await resp.json())["items"]
    kinds = {}
    for s in sources:
        kinds.setdefault(s["mime"], s)
    for mime, s in sorted(kinds.items()):
        name = f"/evidence/{mime}"
        await page.goto(f"{base}/evidence/{s['sha256']}")
        await settle(page)
        await reveal(page)
        # The header shows the short hash on purpose: it is what an analyst cites.
        expected = [s["sha256"]]
        pages.append(await read(page, f"{name} @{width}", expected))
        for tab in ("Read", "Copies", "Relations"):
            t = page.get_by_role("tab", name=re.compile(rf"^{tab}\b"))
            if await t.count():
                await t.first.click()
                await settle(page)
                pages.append(await read(page, f"{name} {tab} @{width}", expected))

    if width == "desktop":
        counts = await person_counts(page, base, case, people)
        pages.append(
            {"page": "counts @desktop", "url": base, "text": "", "counts": counts}
        )

    queries = SEARCHES + [sources[0]["sha256"][:12]] if sources else SEARCHES
    for q in queries:
        await visit(f"/search?q={quote(q)}&case={quote(case)}", f"/search {q}", [q])

    await ctx.close()
    return pages, errors


async def person_counts(page, base, case, hrefs):
    """Facts and documents for each visited person as the People list (in the case
    and across cases), the person page and a search for their name count them."""
    lists = {}
    for scope, q in (
        ("People list", f"?investigation={quote(case)}"),
        ("People list, all cases", ""),
    ):
        resp = await page.request.get(f"{base}/api/people{q}")
        lists[scope] = {i["person"]: i for i in (await resp.json())["items"]}
    csrf = await page.get_attribute('meta[name="csrf-token"]', "content")
    out = []
    for href in hrefs:
        pid = href.rsplit("/", 1)[1]
        mine = lists["People list"].get(pid)
        if mine is None:
            continue
        label = mine["name"] or "Unnamed person"
        for scope, rows in lists.items():
            if pid in rows:
                out.append(
                    (pid, label, scope, rows[pid]["facts"], rows[pid]["documents"])
                )
        resp = await page.request.get(f"{base}/api/people/{pid}")
        facts = [r for rs in (await resp.json())["facts"].values() for r in rs]
        docs = {r["doc"]["sha256"] for r in facts if r.get("doc")}
        out.append((pid, label, "person page", len(facts), len(docs)))
        if not mine["name"]:
            continue
        resp = await page.request.post(
            f"{base}/api/search",
            data=json.dumps(
                {
                    "item": {
                        "query": mine["name"],
                        "types": ["people"],
                        "semantic": False,
                        "filter": {"investigation": case, "time": None},
                    }
                }
            ),
            headers={"X-CSRFToken": csrf, "Content-Type": "application/json"},
        )
        for hit in (await resp.json())["groups"]["people"]["items"]:
            m = COUNTED.search(hit.get("subtitle") or "")
            if hit["id"] == f"person:{pid}" and m:
                out.append((pid, label, "search", int(m[1]), int(m[2])))
    return out


async def crawl(base, user, case):
    from playwright.async_api import async_playwright

    from stk.agent_login import create_agent_login_token
    from stk.app import create_app

    app = create_app()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pages, errors = [], []
        for width in WIDTHS:
            # Tokens are short lived; mint one per context.
            async with app.app_context():
                token = create_agent_login_token(user, "/desk")
            got, errs = await crawl_width(
                browser, base, f"/_test/login?token={token}", case, width
            )
            pages += got
            errors += errs
        await browser.close()
    return pages, errors


def report(findings, pages, errors):
    lines = []
    for f in sorted(findings, key=lambda f: (f["page"], f["rule"], f["snippet"])):
        lines.append(f"{f['page']:<40} {f['rule']:<14} x{f['count']:<3} {f['snippet']}")
    lines.append("")
    summary = Counter()
    for f in findings:
        summary[(f["page"].rsplit(" @", 1)[0], f["rule"])] += f["count"]
    lines.append("By page and rule:")
    for (page, rule), n in summary.most_common():
        lines.append(f"  {n:>4}  {page:<40} {rule}  ({MESSAGES[rule]})")
    lines.append("")
    for e in errors:
        lines.append(f"page error: {e}")
    lines.append(
        f"{len(findings)} findings ({sum(f['count'] for f in findings)} occurrences) "
        f"on {len({f['page'] for f in findings})} of {len(pages)} page views."
    )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--base", required=True, help="server origin, e.g. http://localhost:5023"
    )
    ap.add_argument("--user", default="admin@example.com")
    ap.add_argument("--case", default="case-demo-02")
    ap.add_argument("--json", default=str(ROOT / "instance" / "check_sense.json"))
    args = ap.parse_args()
    allow = load_allow()
    pages, errors = asyncio.run(crawl(args.base.rstrip("/"), args.user, args.case))
    findings = [f for p in pages for f in check_page(p, allow)]
    print(report(findings, pages, errors))
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "findings": findings,
                "errors": errors,
                "pages": [
                    {k: p[k] for k in ("page", "url", "text", "counts") if k in p}
                    for p in pages
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"JSON: {out}")
    sys.exit(1 if findings or errors else 0)


if __name__ == "__main__":
    main()
