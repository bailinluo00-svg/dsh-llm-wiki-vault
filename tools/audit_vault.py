"""Health audit for the LLM Wiki — checks what the other tools do NOT.

The standing tools cover, and this does NOT repeat:
  - `sync_index.py --check`      index sync, nav-page topic coverage
  - `lint_grounding.py`          Raw-link resolution, evidence fidelity, malformed links
  - `verify_bridge.py`           plugin index regex + graph/pprCascade replay
  - `check_plugin_ready.py`      plugin llmReady + structure snapshot

What THIS adds:
  1. wiki-link resolution across compiled pages, and orphan detection
  2. frontmatter completeness (required scalar and block-list fields), plus
     the frontmatter-vs-metadata-line `updated` consistency check
  3. alias coverage scored the way the plugin's keyword fallback scores it,
     against a few representative Chinese questions
  4. doc consistency: every topic present in topics.yaml, the nav page, and the
     AGENTS.md contract tree; every tool script mentioned in AGENTS.md
  5. stale-text scan (excluding the append-only history files, where old names
     are correct by design)

Exit code is 0 always — this is a report, not a gate. Read the [BUG]/[WARN] lines.

GOTCHA (cost me a false report once): the frontmatter parser must handle
*block-style* YAML lists — `tags:` on its own line followed by indented `- item`
lines. A naive `not line.lstrip().startswith('-')` filter silently drops the
`tags:` / `aliases:` keys themselves and reports 7 phantom "missing field" bugs.
Keep the scalar and list branches separate, and verify a claimed bug by reading
the raw bytes before acting on it.
"""
from __future__ import annotations

import re
from pathlib import Path

VAULT = Path(r"<你的仓库>")
WIKI = VAULT / "wiki"
PLUGIN_DIRS = {"entities", "concepts", "sources", "schema", "contradictions"}

problems: list[str] = []
notes: list[str] = []


def say(kind: str, msg: str) -> None:
    if kind in {"BUG", "WARN"}:
        problems.append(f"[{kind}] {msg}")
    else:
        notes.append(f"[{kind}] {msg}")


def parse_fm(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return (scalars, block lists). Handles `key:` + indented `- item` lists."""
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    if not text.startswith("---"):
        return scalars, lists
    end = text.find("\n---", 3)
    if end < 0:
        return scalars, lists
    current: str | None = None
    for line in text[3:end].split("\n"):
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            if current:
                lists.setdefault(current, []).append(line.lstrip()[2:].strip())
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if value:
                scalars[key] = value
                current = None
            else:
                current = key
                lists.setdefault(key, [])
    return scalars, lists


# ---- collect -------------------------------------------------------------
pages: list[Path] = []
for topic_dir in sorted(d for d in WIKI.iterdir() if d.is_dir() and d.name not in PLUGIN_DIRS):
    pages += sorted(topic_dir.glob("*.md"))
by_stem = {p.stem: p for p in pages}
print(f"编译页: {len(pages)} 篇，{len({p.parent.name for p in pages})} 个主题\n")

# ---- 1. links + orphans --------------------------------------------------
LINK = re.compile(r"\[\[([^\]|#]+?)(?:\|([^\]]+))?\]\]")
inbound = {p.stem: 0 for p in pages}
unresolved: list[tuple[str, str]] = []
for page in pages:
    body = page.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    for m in LINK.finditer(body):
        name = m.group(1).strip().split("/")[-1]
        if name in by_stem:
            if name != page.stem:
                inbound[name] += 1
        else:
            unresolved.append((page.stem, m.group(1).strip()))

for src, tgt in unresolved:
    say("WARN", f"wiki-link 无法解析: {src} → [[{tgt}]]")
if not unresolved:
    say("OK", "wiki-link 全部解析成功")
orphans = [s for s, n in inbound.items() if n == 0]
for s in orphans:
    say("WARN", f"孤儿页: {s}")
if not orphans:
    say("OK", f"无孤儿页（入链统计: {sorted(inbound.items(), key=lambda kv: -kv[1])}）")

# ---- 2. frontmatter ------------------------------------------------------
REQ_SCALAR = ["title", "type", "updated", "summary"]
REQ_LIST = ["tags", "aliases"]
for page in pages:
    text = page.read_text(encoding="utf-8")
    scalars, lists = parse_fm(text)
    if not scalars:
        say("BUG", f"缺少 frontmatter: {page.name}")
        continue
    miss = [k for k in REQ_SCALAR if not scalars.get(k)]
    miss += [k for k in REQ_LIST if not lists.get(k)]
    if miss:
        say("BUG", f"frontmatter 缺 {miss}: {page.name}")
    meta = re.search(r"^> Updated: (.+)$", text, re.M)
    if meta and scalars.get("updated") and meta.group(1).strip() != scalars["updated"]:
        say("BUG", f"updated 不一致: {page.name}")
    if not re.search(r"^> Raw: \[", text, re.M):
        say("BUG", f"缺 Raw: 字段: {page.name}")
    if not re.search(r"^## See Also\s*$", text, re.M):
        say("WARN", f"缺 See Also: {page.name}")
    if scalars.get("type") not in {"concept", "archive"}:
        say("WARN", f"type 非 concept/archive（{scalars.get('type')}）: {page.name}")
say("OK", f"frontmatter 结构检查完成（{len(pages)} 篇）")

# ---- 3. alias coverage vs plugin scoring --------------------------------
def score(title: str, aliases: list[str], summary: str, kws: list[str]) -> int:
    t, al, sm = title.lower(), [a.lower() for a in aliases], summary.lower()
    s = 0
    for kw in kws:
        k = kw.lower()
        if k in t:
            s += 3
        elif any(k in a for a in al):
            s += 2
        elif k in sm:
            s += 1
    return s


QUERIES = {
    "示例领域韧性有哪些评估方法？": ["示例评估", "评估方法", "示例领域韧性", "示例指标"],
    "怎么检测和定位漏水？": ["示例检测", "示例定位", "示例定位", "leak detection"],
    "比较 EXA 和拓扑方法": ["EXA", "拓扑", "示例属性", "示例指标"],
    "示例替代指标和可靠性关系": ["示例替代指标", "示例可靠性", "可靠性", "示例优化"],
}
print("── 别名对插件检索的命中分 ──")
for q, kws in QUERIES.items():
    row = []
    for page in pages:
        scalars, lists = parse_fm(page.read_text(encoding="utf-8"))
        row.append((score(scalars.get("title", ""), lists.get("aliases", []),
                          scalars.get("summary", ""), kws), page.stem))
    row.sort(reverse=True)
    hit = sum(1 for sc, _ in row if sc > 0)
    print(f"  {q}")
    print(f"    命中 {hit}/{len(pages)} | 最高: " + ", ".join(f"{n}={s}" for s, n in row[:3]))

# ---- 4. doc consistency -------------------------------------------------
topics_cfg = (VAULT / "tools" / "topics.yaml").read_text(encoding="utf-8")
agents = (VAULT / "AGENTS.md").read_text(encoding="utf-8")
nav = (VAULT / "LLM Wiki 导航.md").read_text(encoding="utf-8")
for topic in sorted({p.parent.name for p in pages}):
    if topic not in topics_cfg:
        say("BUG", f"topics.yaml 缺主题: {topic}")
    if f"wiki/{topic}/" not in nav:
        say("BUG", f"导航页缺主题: {topic}")
    if topic not in agents:
        say("WARN", f"AGENTS.md 未出现该主题目录名: {topic}")

for script in sorted(p.name for p in (VAULT / "tools").glob("*.py")):
    if script not in agents:
        say("WARN", f"AGENTS.md 工具表未提及: {script}")

# ...and the REVERSE, which the forward check above cannot see: a script that the
# tools table advertises but that no longer exists. That is how a deleted helper
# (pdf_extract.py, removed 2026-09-21) stayed listed for a while: the table is
# hand-written and nothing verified its rows actually resolve.
tool_names = {p.name for p in (VAULT / "tools").iterdir() if p.is_file()}
for named in sorted(set(re.findall(r"^\|\s*`([\w.-]+\.py)`", agents, re.M))):
    if named not in tool_names:
        say("WARN", f"AGENTS.md 工具表登记了不存在的脚本: {named}")

# root index must point at the nav page, or a human starting at index gets stuck
index_txt = (VAULT / "index.md").read_text(encoding="utf-8")
if "LLM Wiki 导航" not in index_txt:
    say("WARN", "根 index.md 未指向导航页（从索引进入的人找不到入口页）")
else:
    say("OK", "根 index.md 已指向导航页")

# ---- 5. stale text (excluding append-only history) ----------------------
HISTORY = {"log.md", "更新公告.md"}  # 追加式/历史文件保留旧名是正确的
STALE = ["整体覆盖 `wiki/index.md`", "禁止手改", "尚未生成",
         "llm_config_status: failed", "raw/inbox/"]
for path in [VAULT / "AGENTS.md", VAULT / "LLM Wiki 导航.md", VAULT / "index.md",
             *sorted((VAULT / "docs").glob("*.md")), *(VAULT / "inbox").glob("*.md")]:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    for needle in STALE:
        if needle in text:
            say("WARN", f"过期表述「{needle}」在 {path.name}")

# ---- 6. plugin bridge quality ------------------------------------------
engine = (WIKI / "index.md").read_text(encoding="utf-8")
bridge = re.search(r"<!-- AGENT-INDEX-START.*?-->(.*?)<!-- AGENT-INDEX-END -->", engine, re.S)
if not bridge:
    say("BUG", "wiki/index.md 缺 AGENT 桥接段")
else:
    line_re = re.compile(r"^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*(?:`aliases:\s*([^`]+)`)?(?:\s*-\s*(.+))?$")
    parsed = [m for m in (line_re.match(l) for l in bridge.group(1).split("\n")) if m]
    no_alias = [m.group(1) for m in parsed if not m.group(2)]
    if len(parsed) != len(pages):
        say("BUG", f"桥接段 {len(parsed)} 条 vs 编译页 {len(pages)} 篇")
    elif no_alias:
        say("WARN", f"桥接段 {len(no_alias)} 条无 aliases")
    else:
        say("OK", f"桥接段 {len(parsed)} 条全部带 aliases")

# ---- summary -----------------------------------------------------------
print("\n" + "=" * 72)
for line in notes + problems:
    print(line)
bugs = sum(1 for p in problems if p.startswith("[BUG]"))
warns = sum(1 for p in problems if p.startswith("[WARN]"))
print("=" * 72)
print(f"BUG: {bugs}   WARN: {warns}   OK/INFO: {len(notes)}")
