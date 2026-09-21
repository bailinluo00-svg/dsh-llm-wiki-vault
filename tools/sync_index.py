"""Regenerate the auto-managed rows of the vault-root index.md.

The root index has two layers:

  - Hand-maintained scaffolding: the H1, the intro blockquote, the `## <topic>`
    headings with their one-line descriptions, and any prose section.
  - Auto-managed table rows: derived from the actual files under wiki/<topic>/.

Topic labels/blurbs/summary pins live in tools/topics.yaml.

`--bridge-index` additionally writes the Agent section into the plugin's own
engine index `wiki/index.md`, in the plugin's own bullet format, so the
karpathywiki Query panel can retrieve the compiled pages. The plugin reads that
file as its table of contents (main.js:79003 `buildWikiContext`).

CORRECTION (supersedes an earlier wrong note in this docstring): the plugin
REBUILDS `wiki/index.md` as a whole-file OVERWRITE, not an append. The chain is
`generateFlatIndex` -> `indexGenerator.writeFile` -> `createOrUpdateFile` ->
`vault.process(file, () => content)` (main.js:75521, 76627). So every plugin
ingest or Lint WIPES the marked Agent section.

Therefore the standing rule is: after any plugin-side ingest or Lint, re-run
`python tools/sync_index.py --bridge-index`, then confirm with
`--bridge-index --check`. Writing the section is not enough on its own; use
`tools/verify_bridge.py` to prove the plugin's own regex will actually parse it.

Usage:
    python tools/sync_index.py                     # rewrite root index.md
    python tools/sync_index.py --check              # report drift, change nothing
    python tools/sync_index.py --bridge-index       # also patch wiki/index.md
    python tools/sync_index.py --bridge-index --check
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

VAULT = Path(r"<你的仓库>")
WIKI = VAULT / "wiki"
INDEX = VAULT / "index.md"
ENGINE_INDEX = WIKI / "index.md"
NAV = VAULT / "LLM Wiki 导航.md"

# wiki/ subfolders owned by the karpathywiki plugin, not by this index.
PLUGIN_DIRS = {"entities", "concepts", "sources", "schema", "contradictions"}

# Markers delimiting the section this script owns inside the plugin's index.
BRIDGE_BEGIN = "<!-- AGENT-INDEX-START: maintained by tools/sync_index.py — do not edit by hand -->"
BRIDGE_END = "<!-- AGENT-INDEX-END -->"

HEADER = "# Knowledge Base Index"
INTRO = (
    "> 本文件是仓库的**全局索引**。wiki/ 下每一个知识页都应在此出现一行。\n"
    "> 表格行由 `tools/sync_index.py` 生成；表外的手写章节不会被脚本改动。\n"
    "> **人类入口页在 [[LLM Wiki 导航]]**（含规范、工具与收件箱说明的指引）；"
    "系统改动看 [[更新公告]]，内容操作看 [[log]]。\n"
    "> 注意：插件 `karpathywiki` 另有一份引擎索引 `wiki/index.md`（它自己的检索用），"
    "由插件自动重建，不要手改。"
)
TABLE_HEAD = "| Article | Summary | Updated |\n|---------|---------|---------|"


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for line in text[3:end].split("\n"):
        if ":" in line and not line.lstrip().startswith("-"):
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm


def frontmatter_list(text: str, key: str) -> list[str]:
    """Read a block-style YAML list (`key:` then `  - item` lines) or an inline one."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end < 0:
        return []
    lines = text[3:end].split("\n")
    out: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if in_block:
            if stripped.startswith("- "):
                value = stripped[2:].strip().strip('"').strip("'")
                if value:
                    out.append(value)
                continue
            if stripped and not line.startswith((" ", "\t")):
                break
            if not stripped:
                continue
            break
        if stripped.startswith(f"{key}:"):
            inline = stripped[len(key) + 1:].strip()
            if inline.startswith("[") and inline.endswith("]"):
                out = [v.strip().strip('"').strip("'") for v in inline[1:-1].split(",") if v.strip()]
                return out
            in_block = True
    return out


def summary_of(text: str, limit: int = 100) -> str:
    """Frontmatter `summary:` if present, else the first `## Overview` sentence."""
    fm = frontmatter(text)
    if fm.get("summary"):
        return clip(fm["summary"], limit)
    match = re.search(r"^##\s+Overview\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    body = match.group(1) if match else text
    for line in body.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith(">") and not line.startswith("|"):
            line = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), line)
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            line = re.sub(r"[*`]", "", line)
            # Cut at the first sentence boundary that fits, so rows do not end mid-clause.
            for end in (m.end() for m in re.finditer(r"[。；;]", line)):
                if end <= limit:
                    return line[:end]
            return clip(line, limit)
    return "(no summary)"


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def load_topics() -> dict[str, dict]:
    """Read tools/topics.yaml. PyYAML is optional: a minimal parser covers our shape."""
    path = Path(__file__).with_name("topics.yaml")
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_topics_yaml(text)
    data = yaml.safe_load(text) or {}
    return data.get("topics") or {}


def _parse_topics_yaml(text: str) -> dict[str, dict]:
    """Indentation-aware parser for the topics.yaml shape used here."""
    topics: dict[str, dict] = {}
    current: str | None = None
    section: str | None = None
    for raw in text.split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if indent == 0:
            # Top-level key. `settings:` collects scalar options into the
            # pseudo-topic "settings"; anything else (e.g. `topics:`) is skipped.
            current = None
            section = None
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                if not value.strip():
                    current = "settings" if key == "settings" else None
                    if current:
                        topics.setdefault(current, {})
                continue
        if indent == 2 and line.endswith(":"):
            current = line[:-1].strip()
            topics[current] = {"pin": {}}
            continue
        if current is None:
            continue
        if indent == 4 and line.endswith(":"):
            section = line[:-1].strip()
            continue
        if indent == 4 and ":" in line:
            key, _, value = line.partition(":")
            topics[current][key.strip()] = value.strip()
            continue
        if indent == 2 and ":" in line and current == "settings":
            key, _, value = line.partition(":")
            topics["settings"][key.strip()] = value.strip()
            continue
        if indent == 6 and section == "pin" and ":" in line:
            key, _, value = line.partition(":")
            topics[current].setdefault("pin", {})[key.strip()] = value.strip()
    return topics


def mtime_date(path: Path) -> str:
    import datetime

    return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()


def topic_dirs() -> list[Path]:
    return sorted(
        d for d in WIKI.iterdir()
        if d.is_dir() and d.name not in PLUGIN_DIRS and not d.name.startswith(".")
    )


def collect(pins: dict[str, str], index_name: bool) -> dict[str, list[tuple]]:
    """Gather one row per wiki/<topic>/*.md page.

    A row is (title, summary, updated, link, aliases). `link` is either the bare
    page stem (`[[页面名]]`, human-friendly in Obsidian) or the explicit
    vault-relative path (`[[wiki/<topic>/<page>|stem]]`), per `index_name`.
    """
    result: dict[str, list[tuple]] = {}
    for directory in topic_dirs():
        rows = []
        for page in sorted(directory.glob("*.md")):
            if page.name.startswith("_"):
                continue
            text = page.read_text(encoding="utf-8")
            fm = frontmatter(text)
            title = fm.get("title") or page.stem
            updated = fm.get("updated") or mtime_date(page)
            summary = pins.get(page.stem) or pins.get(title) or summary_of(text)
            rel = page.relative_to(VAULT).with_suffix("").as_posix()
            link = f"[[{rel}|{title}]]" if index_name else f"[[{title}]]"
            aliases = frontmatter_list(text, "aliases")
            rows.append((title, summary, updated, link, aliases))
        result[directory.name] = rows
    return result


def parse_existing(text: str) -> dict[str, str]:
    """Map topic name -> one-line blurb already present in index.md."""
    descriptions: dict[str, str] = {}
    pattern = r"^##\s+wiki/([\w\-.]+)\s+—.*?$(.*?)(?=^##\s|\Z)"
    for match in re.finditer(pattern, text, re.M | re.S):
        name = match.group(1)
        for line in match.group(2).split("\n"):
            line = line.strip()
            if line and not line.startswith("|") and not line.startswith("#"):
                descriptions[name] = line
                break
    return descriptions


def render(existing: str, data: dict[str, list[tuple[str, str, str, str]]], index_name: bool) -> str:
    topics_cfg = load_topics()
    descriptions = parse_existing(existing)
    chunks = [HEADER, "", INTRO, ""]
    if not data:
        chunks.append("> 尚无编译知识页。运行一次 Ingest 后此索引将自动填充。")
        return "\n".join(chunks).rstrip() + "\n"

    for topic, rows in data.items():
        cfg = topics_cfg.get(topic, {})
        label = cfg.get("label") or topic.replace("-", " ")
        blurb = cfg.get("blurb") or descriptions.get(topic) or "（待补充一句话说明）"
        chunks.append(f"## wiki/{topic} — {label}")
        chunks.append("")
        chunks.append(blurb)
        chunks.append("")
        chunks.append(TABLE_HEAD)
        for _title, summary, updated, link, _aliases in rows:
            chunks.append(f"| {link} | {summary} | {updated} |")
        chunks.append("")

    chunks.append("## wiki/entities、wiki/concepts、wiki/sources — 插件生成的页面")
    chunks.append("")
    chunks.append(
        descriptions.get(
            "entities",
            "由 Obsidian 插件 `karpathywiki` 通过 UI 摄取（Ingest）生成并维护，"
            "页面格式与命名由插件决定。其扁平索引见 `wiki/index.md`。",
        )
    )
    return "\n".join(chunks).rstrip() + "\n"


def bridge_section(data: dict[str, list[tuple]]) -> str:
    """Render the Agent section in the plugin's own bullet format.

    Format the plugin's parser accepts (main.js:70571 `parseIndexForPages`):
        - [[path|Name]] `aliases: a, b` - summary

    The `aliases:` group is load-bearing, not decoration. The plugin's seed
    selector (`selectPprSeeds`, main.js:77861) gates on keyword hits against
    title / aliases / summary, and its tokenizer treats a whole CJK run as ONE
    token (main.js:77000), so a Chinese question can never substring-match a
    title. Aliases are what make those pages selectable.

    `path` stays wiki-folder-relative: `buildGraphFromContent` strips the
    `wiki/` prefix before matching link targets, and the graph loader re-adds
    it when reading.
    """
    lines = [BRIDGE_BEGIN, "", "## Agent-compiled knowledge pages", ""]
    for topic, rows in data.items():
        lines.append(f"### {topic}")
        lines.append("")
        for _title, summary, _updated, link, aliases in rows:
            path, _, display = link[2:-2].partition("|")
            display = (display or path.split("/")[-1]).strip()
            alias_group = f" `aliases: {', '.join(aliases)}`" if aliases else ""
            lines.append(f"- [[{path}|{display}]]{alias_group} - {summary}")
        lines.append("")
    lines.append(BRIDGE_END)
    return "\n".join(lines).rstrip() + "\n"


def patch_engine_index(section: str, check: bool) -> tuple[bool, str]:
    """Insert/replace the Agent section inside the plugin's wiki/index.md.

    Returns (in_sync, message). The plugin appends to its index rather than
    truncating it, so a marked section at the end survives regeneration; the
    check mode tells us when that assumption stops holding.
    """
    if not ENGINE_INDEX.exists():
        return False, (
            f"{ENGINE_INDEX.relative_to(VAULT)} does not exist yet — the plugin creates it "
            "on its first ingest. Re-run --bridge-index afterwards."
        )
    current = ENGINE_INDEX.read_text(encoding="utf-8")
    if BRIDGE_BEGIN in current and BRIDGE_END in current:
        head = current[: current.index(BRIDGE_BEGIN)]
        updated = head + section
    else:
        head = current.rstrip() + "\n\n"
        updated = head + section
    if updated.strip() == current.strip():
        return True, f"{ENGINE_INDEX.relative_to(VAULT)}: agent section in sync"
    if check:
        return False, (
            f"{ENGINE_INDEX.relative_to(VAULT)}: agent section missing or drifted — "
            "run: python tools/sync_index.py --bridge-index"
        )
    ENGINE_INDEX.write_text(updated, encoding="utf-8")
    return True, f"{ENGINE_INDEX.relative_to(VAULT)}: agent section written ({len(section)} bytes)"


def nav_missing(data: dict[str, list[tuple]]) -> list[str]:
    """Pages that exist in wiki/<topic>/ but have no line in the navigation note."""
    if not NAV.exists():
        return []
    nav_text = NAV.read_text(encoding="utf-8")
    missing = []
    for topic, rows in data.items():
        for _title, _summary, _updated, _link, _aliases in rows:
            needle = f"wiki/{topic}/"
            if needle not in nav_text:
                missing.append(f"{topic}（整个主题目录未出现在导航页）")
                break
    return missing


def main(argv: list[str]) -> int:
    check = "--check" in argv
    bridge = "--bridge-index" in argv
    existing = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
    topics_cfg = load_topics()
    pins: dict[str, str] = {}
    for cfg in topics_cfg.values():
        pins.update(cfg.get("pin") or {})
    index_name = bool(topics_cfg.get("settings", {}).get("index_name"))
    data = collect(pins, index_name)
    rendered = render(existing, data, index_name)

    exit_code = 0
    if check:
        missing = nav_missing(data)
        if missing:
            for item in missing:
                print(f"nav page MISSING: {item}")
            exit_code = 1
        else:
            print("LLM Wiki 导航.md covers every topic directory")
        if rendered.strip() == existing.strip():
            print("index.md is in sync")
        else:
            print("index.md is OUT OF SYNC with wiki/ — run: python tools/sync_index.py")
            exit_code = 1
    else:
        INDEX.write_text(rendered, encoding="utf-8")
        total = sum(len(v) for v in data.values())
        print(f"index.md regenerated: {len(data)} topic(s), {total} article(s)")
        for item in nav_missing(data):
            print(f"提醒：导航页缺少 {item}")

    if bridge:
        ok, message = patch_engine_index(bridge_section(data), check)
        print(message)
        if not ok:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
