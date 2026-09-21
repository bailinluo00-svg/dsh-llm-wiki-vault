"""Fidelity check: do the archived PDFs still back the raw/*.md verbatim?

The grounding invariant rests on a chain the other tools do NOT verify:

    raw/<topic>/pdfs/<stem>.pdf   (the immutable original)
        --extract_text()-->  raw/<topic>/<stem>.md   (what the compiler reads)

`lint_grounding.py` checks article -> raw. Nothing checked PDF -> raw, so the
immutability of `raw/` was a convention, not a checked fact: if a raw file were
edited (or a PDF swapped), every downstream check would still pass.

Method: take the PDF's own text layer, whitespace-strip it, sample overlapping
6-character n-grams, and measure how many survive into the whitespace-stripped
raw body. Sampling (every 7th offset) keeps it fast on 50-80k-char papers.

How to read the number:
  * ~100%  — raw carries the PDF's own words. Expected.
  * LOW or a missing raw — the pair needs a human look. Do NOT just re-extract
    and overwrite: `raw/` is append-only, and a genuine discrepancy may mean the
    raw was deliberately cleaned (running headers, broken hyphenation) or that
    the two files were never a pair. Investigate, then decide.

Expected small delta: the raw is a few hundred chars LONGER than the text layer
(it adds the metadata header). A raw SHORTER than the PDF is the suspicious case.

Limits, stated honestly: this proves word-level preservation, not byte identity —
it deliberately tolerates the mechanical cleanup (page markers -> HTML comments,
collapsed blank runs) that ingest is allowed to do. It also cannot see a PDF
swapped for a visually similar one. It is a tripwire for accidental damage.

Usage:
    python tools/check_raw_fidelity.py              # all topics with pdfs/
    python tools/check_raw_fidelity.py <topic>      # one topic
Exit code is 1 only when a raw file is MISSING for an archived PDF; a low ratio
is reported, not failed (it may be legitimate — see above).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

VAULT = Path(r"<你的仓库>")
RAW = VAULT / "raw"

NEXAM = 6
STRIDE = 7


def norm(text: str) -> str:
    """Strip frontmatter/comments and all whitespace, so only content compares."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"^---.*?^---", " ", text, flags=re.S | re.M)
    return re.sub(r"\s+", "", text)


def survival(pdf_text: str, raw_text: str) -> float:
    grams = {pdf_text[i:i + NEXAM] for i in range(0, max(0, len(pdf_text) - NEXAM), STRIDE)}
    if not grams:
        return 0.0
    return sum(1 for g in grams if g in raw_text) / len(grams)


def main(argv: list[str]) -> int:
    from pypdf import PdfReader  # late import so --help works without pypdf

    wanted = argv[0] if argv else None
    topics = sorted(p for p in RAW.iterdir() if p.is_dir() and (p / "pdfs").is_dir())
    if wanted:
        topics = [t for t in topics if t.name == wanted]
        if not topics:
            print(f"no topic named {wanted!r} with a pdfs/ subdirectory")
            return 0

    missing = 0
    checked = 0
    print("=" * 96)
    for topic in topics:
        for pdf in sorted((topic / "pdfs").glob("*.pdf")):
            raws = sorted(topic.glob(f"{pdf.stem}*.md"))
            if not raws:
                missing += 1
                print(f"  !! NO RAW for archived PDF: raw/{topic.name}/pdfs/{pdf.name}")
                continue
            raw_text = norm(raws[0].read_text(encoding="utf-8"))
            reader = PdfReader(str(pdf))
            pdf_text = norm("".join((pg.extract_text() or "") for pg in reader.pages))
            ratio = survival(pdf_text, raw_text)
            delta = len(raw_text) - len(pdf_text)
            flag = "  <-- investigate" if ratio < 0.95 else ""
            print(f"{ratio:6.1%}  delta={delta:>+6}  {pdf.stem[:56]}{flag}")
            print(f"         raw: {raws[0].name}")
            checked += 1
    print("=" * 96)
    print(f"{checked} pair(s) checked; {missing} archived PDF(s) with no raw file")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
