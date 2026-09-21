"""Lint: source-fidelity check for the agent-compiled wiki track.

Follows the grounding invariant of the karpathy-llm-wiki skill: every
load-bearing literal in a wiki article (percentages, decimals, ISO dates,
dollar amounts, large numbers) must appear verbatim in the raw file linked by
that article's `Raw:` field.

This is a REPORTING tool, not a gate. Reported suspects are candidates, not
verdicts: derived values (sums, ratios the compiler computed) are expected to
show up. Judge each hit against the raw context.

Known false-positive channels — measured, not guessed (see 更新公告.md #13):

  1. The page's OWN metadata dates. `updated:`, the `> Updated:` line, and the
     `Sources:` date are literals too, but they are not claims about the source;
     a page revised after its source was collected will always flag them. Expect
     roughly 2 per page and do not "fix" them.
  2. Unit/symbol form. `91.7%` is a different literal from `91.7`, and neither is
     `91.7 percent`. The comparison is on exact normalized text, so any change of
     form flags. This is CORRECT behavior — the grounding invariant says verbatim
     — but it means the fix is to match the raw's wording, never to "normalize"
     the number. (Documented OCR quirks are the one sanctioned exception, and the
     page must say so inline.)
  3. Derived values, as above.

The useful property to remember: this tool catches FABRICATION at full strength
(injection test: 3 fabricated literals planted, 3 flagged). Its noise is
concentrated in categories you can recognize on sight, so read the line numbers.

Usage:
    python tools/lint_grounding.py [article.md ...]

With no arguments the whole wiki/<topic>/ track is checked.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

VAULT = Path(r"<你的仓库>")
WIKI = VAULT / "wiki"
RAW = VAULT / "raw"

# Literals worth checking: suffixed numbers, decimals, percentages, dates,
# money, and long numbers.
LITERAL_RE = re.compile(
    r"""
    (?P<num>\d+(?:\.\d+)?\s*(?:%|‰|°))
    |(?P<money>\$\s?\d[\d,]*(?:\.\d+)?\s*[MmKk]?)
    |(?P<date>\d{4}-\d{2}-\d{2})
    |(?P<big>\b\d{4,}\b)
    |(?P<dec>\b\d+\.\d+\b)
    """,
    re.VERBOSE,
)

# Structural/format noise that is not a knowledge claim.
IGNORE_SUBSTRINGS = (
    "wiki/",
    "raw/",
    "http",
    "../../",
)


# Vault-root navigation / landmark pages: they are structural, hold no knowledge
# claims, and legitimately carry no `Raw:` field. Their wiki-links are still
# checked for malformed targets, but they are exempt from the evidence rules.
META_PAGES = ("LLM Wiki 导航.md",)


def article_files(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) for a in argv]
    files = []
    for extra in META_PAGES:
        path = VAULT / extra
        if path.exists():
            files.append(path)
    for path in sorted(WIKI.rglob("*.md")):
        parts = path.parts
        # Plugin-owned namespaces and special files are out of this check's scope:
        # they are written by the karpathywiki plugin, not by the Agent, and carry
        # no `Raw:` contract. Agent pages live exactly one level down, in
        # wiki/<topic>/, so a page sitting directly in wiki/ is plugin-owned.
        if path.name in {"index.md", "log.md"} or path.name.startswith("_"):
            continue
        if ".obsidian" in parts:
            continue
        if any(p in {"entities", "concepts", "sources", "schema", "contradictions"} for p in parts):
            continue
        if path.parent.resolve() == WIKI.resolve():
            continue
        files.append(path)
    return files


def raw_links(text: str) -> list[Path]:
    links = []
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+\.md)\)", text):
        target = match.group(1)
        if "/raw/" in target or target.startswith("../../raw/") or target.startswith("raw/"):
            resolved = (RAW.parent / target.lstrip("/")) if not target.startswith("..") else None
            if resolved is None:
                resolved = (VAULT / target.replace("../../", "")).resolve()
            links.append(Path(resolved))
    return links


FENCE_RE = re.compile(r"^\s*```")
WIKILINK_RE = re.compile(r"\[\[([^\]]*)\]\]")


def malformed_wikilinks(text: str) -> list[tuple[str, int]]:
    """Find degenerate wiki-links.

    Catches `[[ ]]`, `[[|label]]` and `[[  |label]]` — links with an empty
    target that Obsidian cannot resolve. These appear routinely when a script
    glues a page title that turned out to be empty, so they are worth a guard.
    Fenced code blocks are skipped: documentation legitimately shows link
    templates with placeholder targets there.
    """
    problems = []
    in_fence = False
    for lineno, line in enumerate(text.split("\n"), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in WIKILINK_RE.finditer(line):
            target = match.group(1).split("|", 1)[0].strip()
            if not target:
                problems.append((match.group(0), lineno))
    return problems


def literals(text: str) -> list[tuple[str, int]]:
    found = []
    for lineno, line in enumerate(text.split("\n"), 1):
        if any(s in line for s in IGNORE_SUBSTRINGS):
            continue
        for match in LITERAL_RE.finditer(line):
            found.append((match.group(0).strip(), lineno))
    return found


def main(argv: list[str]) -> int:
    files = article_files(argv)
    if not files:
        print("no articles found")
        return 0
    total_suspects = 0
    total_literals = 0
    total_malformed = 0
    for article in files:
        text = article.read_text(encoding="utf-8")
        is_meta = article.name in META_PAGES
        raws = [p for p in raw_links(text) if p.exists()]
        missing = [p for p in raw_links(text) if not p.exists()]
        print(f"\n=== {article.relative_to(VAULT)}")
        if not raw_links(text) and not is_meta:
            print("  !! no Raw: link found (evidence error)")
        for path in missing:
            print(f"  !! Raw link does not resolve: {path}")
        bad_links = malformed_wikilinks(text)
        total_malformed += len(bad_links)
        for raw_link, lineno in bad_links:
            print(f"  !! malformed wiki-link (empty target) L{lineno}: {raw_link!r}")
        if is_meta:
            print("  (meta page: evidence rules exempt)")
            continue
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in raws)
        corpus_norm = re.sub(r"\s+", "", corpus)
        suspects = []
        for literal, lineno in literals(text):
            total_literals += 1
            needle = re.sub(r"\s+", "", literal)
            if needle not in corpus_norm:
                suspects.append((literal, lineno))
        total_suspects += len(suspects)
        print(f"  literals checked: {len(literals(text))}   raws: {len(raws)}   suspect: {len(suspects)}")
        for literal, lineno in suspects:
            print(f"    L{lineno}: {literal}")
    print(f"\nSUMMARY: {total_suspects} suspect / {total_literals} literals; {total_malformed} malformed wiki-link")
    return 1 if total_malformed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
