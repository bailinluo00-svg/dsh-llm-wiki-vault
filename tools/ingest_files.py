"""Ingest files into raw/<topic>/ — the mechanical half of the Ingest operation.

Handles the three shapes an incoming source can take:

  * ``.pdf``                    — text layer extracted, original archived under pdfs/
  * ``.md`` / ``.txt``          — read as text; the original is kept under _source/
  * ``.html`` / ``.htm``        — tags stripped to text; the original is kept under _source/

Two ways to run it:

  # 1. Inbox mode — process everything the user dropped into inbox/.
  python tools/ingest_files.py --dry-run
  python tools/ingest_files.py --topic example-topic-2

  # 2. Explicit mode — ingest specific files anywhere on disk.
  python tools/ingest_files.py --topic <topic> "<file-or-glob>" [more...]

Inbox mode asks you to name the topic **once**, because the topic is a judgement
about the knowledge base, not a file property. With ``--topic`` it processes the
whole inbox into that single topic; with ``--plan`` it instead prints a proposed
topic per file for a human (or agent) decision.

What this script deliberately does NOT do — because it needs reading and
judgement: triage against the existing wiki, compiling wiki pages, choosing
aliases, cascading updates, index sync, logging. See AGENTS.md section 2.1.

Fidelity: the body is reproduced from the source with mechanical edits only —
page markers become HTML comments, HTML tags are dropped, runs of 3+ blank lines
collapse. No rewording, no reordering, nothing dropped.
"""
from __future__ import annotations

import argparse
import datetime
import html as html_mod
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

VAULT = Path(r"<你的仓库>")
INBOX = VAULT / "inbox"
PAGE_RE = re.compile(r"^<<<PAGE (\d+)>>>$")
PDF_TEXT_PAGE_RE = re.compile(r"^<<<PAGE (\d+)>>>$")
META_PREFIXES = ("# PDF:", "# Pages:", "# /")
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "and", "or", "to", "with", "via",
    "using", "from", "by", "at", "as", "is", "are", "its", "how", "are",
}
TEXTUAL = {".md", ".markdown", ".txt"}
HTMLISH = {".html", ".htm"}
SUPPORTED = {".pdf"} | TEXTUAL | HTMLISH
# Directories a browser/Zotero save may nest inside; we flatten them on the way in.
CONTAINER_DIRS = {"files", "attachments", "data"}

TODAY = ""


@dataclass
class Source:
    src: Path
    kind: str = ""            # pdf | text | html
    title: str = ""
    author: str = ""
    journal: str = ""
    doi: str = ""
    published: str = ""
    keywords: str = ""
    slug: str = ""
    archive_rel: str = ""     # path under raw/<topic>/, e.g. pdfs/x.pdf
    page_count: int = 0
    body: str = ""
    year_hint: str = ""       # detected from PDF metadata / running head
    existing_url: str = ""    # a URL found in frontmatter, when present
    notes: list[str] = field(default_factory=list)
    existing_title: str = ""


# ---------------------------------------------------------------- text helpers

def read_text_file(path: Path) -> str:
    """Read a text file, tolerating a UTF-8 BOM and common Windows encodings.

    A BOM is the difference between parsing frontmatter and silently losing it:
    `text.startswith("---")` is False when the file opens with `\\ufeff`, so the
    title/author/date all fall back to defaults. Windows PowerShell's
    `Set-Content -Encoding UTF8` writes a BOM, and so do several editors, so
    this cannot be treated as an edge case.
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def clean(value: str) -> str:
    return re.sub(r"\s{2,}", " ", (value or "").replace("\u00a0", " ")).strip()


def slugify(text: str, max_words: int = 8) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("\u2019", "").replace("'", "")
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    words = [w for w in words if w not in STOPWORDS]
    return "-".join(words[:max_words])


def surname_of(name: str) -> str:
    parts = [p for p in re.split(r"[\s.]+", clean(name)) if p]
    return parts[-1] if parts else ""


# Characters that are unsafe or awkward in a filename on Windows/Obsidian.
UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# A title is "mostly CJK" when it contains a run of 3+ CJK/Hangul/Kana chars.
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff]{3,}")
CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff]")


def title_slug(title: str, ascii_words: int = 10, cjk_chars: int = 30) -> str:
    """A filename-safe slug that keeps CJK titles intact.

    `slugify` is ASCII-only by design (right for author names) but returns ""
    for a pure-Chinese title, which would throw the title away. So:

      * mostly-CJK title -> keep the CJK run (trimmed to `cjk_chars`), plus any
        short ASCII lead so the file still sorts and reads sensibly;
      * mostly-ASCII title -> run the normal stopword-dropping slugify, capped
        at `ascii_words` words.

    This keeps English paper titles lowercase-and-short while letting a Chinese
    title survive verbatim.
    """
    text = clean(title)
    text = UNSAFE_CHARS.sub(" ", text)
    text = text.replace("\u2014", " ").replace("\u2013", " ")

    if CJK_RUN.search(text):
        cjk = "".join(CJK_CHAR.findall(text))[:cjk_chars]
        ascii_lead = slugify(CJK_CHAR.sub(" ", text), max_words=4)
        return f"{ascii_lead}-{cjk}".strip("-") if ascii_lead else cjk

    slug = slugify(text, max_words=ascii_words)
    if not slug:
        slug = re.sub(r"\s+", "-", text.strip())
    return re.sub(r"-{2,}", "-", slug).strip("-.")


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter-ish dict, body). Tolerant of BOM, leading blanks, CRLF."""
    text = text.lstrip("\ufeff\n\r ")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm: dict[str, str] = {}
    for line in text[3:end].split("\n"):
        if ":" in line and not line.lstrip().startswith("-"):
            key, _, value = line.partition(":")
            fm[key.strip().lower()] = value.strip().strip('"').strip("'")
    return fm, text[end + 4:].lstrip("\n")


def html_to_text(raw: str) -> tuple[str, str]:
    """Return (title, text) from an HTML document."""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    if m:
        title = clean(html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1))))
    body = raw
    body = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>", "\n\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html_mod.unescape(body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return title, body.strip()


# ------------------------------------------------------------- pdf extraction

def read_pdf(path: Path) -> tuple[dict, int, list[str]]:
    from pypdf import PdfReader  # late import so --help works without pypdf

    reader = PdfReader(str(path))
    meta = dict(reader.metadata or {})
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            pages.append(f"[extraction failed: {exc}]")
    return meta, len(reader.pages), pages


def pdf_author(meta: dict, pages: list[str]) -> str:
    author = clean(str(meta.get("/Author") or ""))
    if author:
        return surname_of(author.split(",")[0].split(" and ")[0])
    head = " ".join(pages[:1]) if pages else ""
    for pattern in (
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*(?:[a-z0-9\s,†‡§¶*]*?),",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s+[a-z]\s*,",
    ):
        m = re.search(pattern, head.strip(), re.M)
        if m:
            return surname_of(m.group(1))
    return ""


def pdf_year(meta: dict, pages: list[str]) -> str:
    head = pages[0] if pages else ""
    m = re.search(r"\b(19|20)\d{2}\b", head)
    if m:
        return m.group(0)
    m = re.search(r"(19|20)\d{2}", clean(str(meta.get("/CreationDate") or "")))
    return m.group(0) if m else ""


MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def pdf_published(meta: dict, pages: list[str]) -> str:
    head = " ".join(pages[:1])
    for pattern in (
        r"Available online (\d{1,2} \w+ \d{4})",
        r"Published:? (\d{1,2} \w+ \d{4})",
        r"(\d{1,2} \w+ \d{4});\s*Accepted",
    ):
        m = re.search(pattern, head)
        if m:
            parts = m.group(1).split()
            if len(parts) == 3 and parts[1] in MONTHS:
                return f"{parts[2]}-{MONTHS[parts[1]]:02d}-{int(parts[0]):02d}"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", head)
    if m:
        return m.group(1)
    m = re.search(r"D:((19|20)\d{2})(\d{2})(\d{2})", clean(str(meta.get("/CreationDate") or "")))
    return f"{m.group(1)}-{m.group(3)}-{m.group(4)}" if m else "Unknown"


def pdf_journal(meta: dict, pages: list[str]) -> str:
    subject = clean(str(meta.get("/Subject") or ""))
    if subject:
        return subject
    head = pages[0] if pages else ""
    m = re.search(r"^([A-Z][A-Za-z&\s]+?),?\s+\d+\s*\((\d{4})\)", head, re.M)
    return m.group(0).strip() if m else ""


def pdf_doi(meta: dict, pages: list[str]) -> str:
    subject = clean(str(meta.get("/Subject") or ""))
    m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", subject)
    if m:
        return m.group(1).rstrip(".")
    head = " ".join(pages[:2])
    m = re.search(r"(?:doi:|doi\.org/|dx\.doi\.org/)\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", head)
    return m.group(1).rstrip(".") if m else ""


def pdf_keywords(pages: list[str], limit: int = 12) -> str:
    text = "\n".join(pages[:3])
    m = re.search(r"(?:Keywords|Index Terms|KEYWORDS)\s*:?\s*(.{0,400}?)(?:\n\s*\n|ABSTRACT|Abstract|article info)", text, re.S)
    if not m:
        return ""
    raw = re.sub(r"\s+", " ", m.group(1))
    parts = [p.strip(" .;,") for p in re.split(r"[;·]|\s{2,}", raw) if p.strip(" .;,")]
    return "; ".join(parts[:limit])


def pdf_body(pages: list[str]) -> str:
    out: list[str] = []
    for i, page in enumerate(pages, 1):
        out += ["", f"<!-- page {i} -->", ""]
        for line in page.split("\n"):
            if any(line.startswith(p) for p in META_PREFIXES):
                continue
            out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# -------------------------------------------------------------------- loading

def load_source(path: Path) -> Source:
    src = Source(src=path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        src.kind = "pdf"
        meta, page_count, pages = read_pdf(path)
        src.page_count = page_count
        src.title = clean(str(meta.get("/Title") or "")) or path.stem
        src.author = pdf_author(meta, pages)
        src.journal = pdf_journal(meta, pages)
        src.doi = pdf_doi(meta, pages)
        src.published = pdf_published(meta, pages)
        src.keywords = pdf_keywords(pages)
        src.body = pdf_body(pages)
        src.year_hint = pdf_year(meta, pages)
        src.existing_title = f"pdfs/{path.name}"
    elif suffix in HTMLISH:
        src.kind = "html"
        raw = read_text_file(path)
        title, text = html_to_text(raw)
        src.title = title or path.stem
        src.body = text
        src.published = TODAY
    else:
        src.kind = "text"
        raw = read_text_file(path)
        fm, body = split_frontmatter(raw)
        src.title = fm.get("title") or path.stem
        src.author = fm.get("author") or ""
        src.published = fm.get("date") or fm.get("published") or fm.get("created") or TODAY
        src.journal = fm.get("source") or ""
        src.existing_url = fm.get("url") or fm.get("link") or fm.get("source") or ""
        src.doi = fm.get("doi") or ""
        src.keywords = fm.get("tags") or ""
        src.body = body
        src.existing_title = fm.get("title") or ""
    return src


def assign_slug(src: Source, topic: str, force_year: str | None) -> None:
    """Derive `<year>-<surname>-<kebab-title>`, preferring the source's own signals."""
    year = force_year or src.year_hint
    if not year:
        m = re.search(r"\b(19|20)\d{2}\b", src.published or "")
        year = m.group(0) if m else ""
    if not year:
        m = re.search(r"\b(19|20)\d{2}\b", src.body[:2000])
        year = m.group(0) if m else ""
    if not year:
        m = re.search(r"(19|20)\d{2}", src.src.name)
        year = m.group(0) if m else ""
    year = year or "unknown"
    # Author segment is optional: a missing author must not inject "untitled",
    # and the original filename is a better lead than a placeholder.
    lead = slugify(src.author, max_words=1) or slugify(src.src.stem, max_words=2)
    title_part = title_slug(src.title) or slugify(src.src.stem, max_words=8) or "source"
    slug = "-".join(p for p in (year, lead, title_part) if p)[:110].strip("-.")
    src.slug = slug or f"{year}-source"
    if src.kind == "pdf":
        src.archive_rel = f"pdfs/{src.slug}.pdf"
    else:
        keep = src.src.suffix.lower()
        src.archive_rel = f"_source/{src.slug}{keep}"


def strip_leading_h1(body: str, title: str) -> str:
    """Drop a leading H1 that merely repeats the title already used as the heading.

    Text/Markdown sources usually open with `# <their title>`; the raw file's own
    `# <title>` line would duplicate it. Only an exact (whitespace-normalised)
    match is removed — any other heading is content and is kept.
    """
    lines = body.lstrip("\n").split("\n")
    for i, line in enumerate(lines[:6]):
        if not line.strip():
            continue
        if re.match(r"^#\s+\S", line):
            if clean(line.lstrip("#").strip()) == clean(title):
                return "\n".join(lines[i + 1:]).lstrip("\n")
        break
    return body


def source_line(src: Source) -> str:
    """The provenance line: prefer a resolvable identifier over vague prose."""
    if src.doi:
        return f"https://doi.org/{src.doi}"
    for candidate in (src.journal, src.existing_url):
        if candidate and re.match(r"https?://", candidate):
            return candidate
    if src.kind == "pdf":
        return f"收件箱投递（原件：{src.archive_rel}）"
    if src.src.parent.resolve() == INBOX.resolve():
        return f"收件箱投递（原件：{src.archive_rel}）"
    return f"本地文件（原件：{src.archive_rel}）"


def render(src: Source) -> str:
    lines = [
        f"# {src.title}",
        "",
        f"> 来源：{source_line(src)}",
        f"> 采集日期：{TODAY}",
        f"> 发布日期：{src.published or 'Unknown'}",
        f"> 原始文件名：{src.src.name}",
    ]
    if src.author:
        lines.append(f"> 作者：{src.author}")
    if src.journal and src.journal != src.doi and not re.match(r"https?://", src.journal):
        lines.append(f"> 出处：{src.journal}")
    if src.doi:
        lines.append(f"> DOI：{src.doi}")
    lines.append(f"> 原始文件：{src.archive_rel}")
    if src.kind == "pdf":
        lines.append(
            f"> 提取说明：由上述 PDF 文本层提取（{src.page_count} 页）。正文逐行保真，未改写、未改动顺序、"
            "未增删任何措辞；仅将页码标记转为 HTML 注释。"
        )
    elif src.kind == "html":
        lines.append("> 提取说明：由 HTML 去除标签后转为纯文本，未改写任何措辞。")
    else:
        lines.append("> 提取说明：原生文本文件，按原文收录；仅整理元数据头。")
    if src.keywords:
        lines.append(f"> 原文关键词：{src.keywords}")
    body = strip_leading_h1(src.body, src.title)
    lines += ["", "---", "", body.strip()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- inbox scan

def inbox_files() -> list[Path]:
    if not INBOX.exists():
        return []
    found: list[Path] = []
    for path in sorted(INBOX.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name.startswith("_"):
            continue
        if path.name.lower() == "readme.md":
            continue
        if path.name.endswith(".crdownload") or path.name.endswith(".part"):
            continue  # a browser download still in flight
        found.append(path)
    return found


def guess_topic(path: Path) -> str:
    """A proposal only — the topic is a judgement, the agent confirms it."""
    stem = slugify(path.stem, max_words=4) or "unfiled"
    return f"example-domain-{stem}" if any(
        k in path.stem.lower() for k in ("leak", "burst", "resilien", "water")
    ) else "unfiled"


def move_and_write(src: Source, topic: str, force: bool, dry_run: bool) -> None:
    topic_dir = VAULT / "raw" / topic
    target = topic_dir / src.archive_rel
    raw_path = topic_dir / f"{src.slug}.md"
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        src.notes.append(f"归档已存在，保留原文件：{src.archive_rel}")
    else:
        shutil.move(str(src.src), str(target))
    if raw_path.exists() and not force:
        src.notes.append("raw .md 已存在，未覆盖（用 --force 覆盖）")
    else:
        raw_path.write_text(render(src), encoding="utf-8")


def report(src: Source, topic: str, dry_run: bool) -> None:
    print("=" * 74)
    print(f"文件       : {src.src.name}    [{src.kind}]")
    print(f"标题       : {src.title}")
    if src.author:
        print(f"作者       : {src.author}")
    if src.journal:
        print(f"出处       : {src.journal}")
    if src.doi:
        print(f"DOI        : {src.doi}")
    print(f"发布日期   : {src.published or 'Unknown'}")
    if src.kind == "pdf":
        print(f"页数       : {src.page_count}")
    if src.keywords:
        print(f"原文关键词 : {src.keywords}")
    print(f"raw 目标   : raw/{topic}/{src.slug}.md")
    print(f"原件归档   : raw/{topic}/{src.archive_rel}")
    for note in src.notes:
        print(f"注意       : {note}")
    if dry_run:
        print("(dry-run：未写任何文件)")


def main(argv: list[str]) -> int:
    global TODAY
    TODAY = datetime.date.today().isoformat()

    ap = argparse.ArgumentParser(description="Ingest files (PDF / text / HTML) into raw/<topic>/")
    ap.add_argument("files", nargs="*", help="files or globs; omit to process inbox/")
    ap.add_argument("--topic", help="target topic directory under raw/ (required unless --plan)")
    ap.add_argument("--plan", action="store_true",
                    help="inbox mode only: print a proposed topic per file, write nothing")
    ap.add_argument("--year", help="override the detected year used in the slug")
    ap.add_argument("--force", action="store_true", help="overwrite existing raw files / archives")
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = ap.parse_args(argv)

    # ---- collect candidates
    if args.files:
        candidates: list[Path] = []
        for arg in args.files:
            if any(c in arg for c in "*?"):
                p = Path(arg)
                base = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
                candidates += sorted(base.glob(p.name))
            else:
                candidates.append(Path(arg))
    else:
        candidates = inbox_files()
        if not candidates:
            print(f"收件箱是空的：{INBOX}")
            print("把 PDF / Markdown / 文本 / HTML 丢进去，再跑一次。")
            return 0

    loaded: list[Source] = []
    for path in candidates:
        if not path.exists():
            print(f"!! 找不到：{path}")
            continue
        if path.suffix.lower() not in SUPPORTED:
            print(f"!! 不支持的类型，已跳过：{path.name}（支持 {', '.join(sorted(SUPPORTED))}）")
            continue
        try:
            src = load_source(path)
        except Exception as exc:  # noqa: BLE001
            print(f"!! 读取失败 {path.name}：{exc}")
            continue
        assign_slug(src, args.topic or "", args.year)
        loaded.append(src)

    if not loaded:
        print("没有可处理的文件")
        return 1

    # ---- plan mode: propose, do not touch anything
    if args.plan:
        print(f"收件箱：{INBOX}\n发现 {len(loaded)} 个文件\n")
        for src in loaded:
            print(f"  {src.src.name}")
            print(f"    -> 推测主题 : {guess_topic(src.src)}")
            print(f"    -> 识别标题 : {src.title}")
            print(f"    -> 类型     : {src.kind}")
        print("\n以上主题只是**推测**。主题归属是知识库层面的判断，请确认后再跑：")
        print('  python tools/ingest_files.py --topic <确认的主题>')
        return 0

    if not args.topic:
        print("!! 需要 --topic <主题目录名>（主题是知识库层面的判断，不由脚本猜）")
        print("   想先看脚本识别到了什么：加 --dry-run 并带上 --topic")
        print("   想让脚本给出主题建议：加 --plan")
        return 2

    for src in loaded:
        move_and_write(src, args.topic, args.force, args.dry_run)
        report(src, args.topic, args.dry_run)

    print("=" * 74)
    print(f"共 {len(loaded)} 个文件 -> raw/{args.topic}/")
    if args.dry_run:
        print("(dry-run：文件仍在原位)")
    else:
        print("收件箱中已处理的文件已被移走；空收件箱 = 全部处理完")
    print("\n下一步（需要判断，本脚本不做）：")
    print("  1. 对照 wiki/ 做 triage（New / Update / Disputed / No material）")
    print("  2. 编译知识页到 wiki/<topic>/，补 aliases（插件种子选择靠它命中）")
    print("  3. python tools/sync_index.py --bridge-index")
    print("  4. 在 LLM Wiki 导航.md 补行；向 log.md 追加条目")
    print("  5. python tools/lint_grounding.py   # 退出码须为 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
