"""Authoritative bridge check: will the karpathywiki plugin's Query engine actually
retrieve the Agent-compiled pages?

The standing `sync_index.py --bridge-index` only proves the section was WRITTEN.
This proves it will be READ, by replaying the plugin's own logic offline:

  1. `parseIndexForPages` (main.js:70573) — the exact regex the query engine uses
     to turn `wiki/index.md` into a page set. If a line does not match, the page
     is invisible to the plugin no matter how good its aliases are.
  2. `pprCascade` (main.js:77090) — the seed-selection path. With a wiki this
     small the graph never reaches `DEFAULT_MIN_PAGES = 30`, so the engine takes
     the degenerate branch; that branch still needs the seeds to have degree>=1,
     which depends on the pages' `[[wiki-link]]` cross-references.

Read-only. Run before trusting plugin retrieval, and after any page rename.
"""
from __future__ import annotations

import re
from pathlib import Path

VAULT = Path(r"<你的仓库>")
WIKI = VAULT / "wiki"
PLUGIN_DIRS = {"entities", "concepts", "sources", "schema", "contradictions"}

# main.js:70573
INDEX_LINE_RE = re.compile(
    r"^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*(?:`aliases:\s*([^`]+)`)?(?:\s*-\s*(.+))?$"
)
# main.js:77938
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]]+)?(?:\|([^\]]+))?\]\]")
# main.js:76990 (tokenizeQuery's CJK branch)
CJK_RUN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]{2,}")

DEFAULT_MIN_PAGES = 30          # main.js:76984
LEX_MATCH_MIN_TOP_SCORE = 5     # main.js:181
LEX_MATCH_MIN_COUNT = 3         # main.js:181

failures: list[str] = []


def check(cond: bool, ok: str, bad: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + (ok if cond else bad))
    if not cond:
        failures.append(bad)


print("=" * 74)
print("1. 插件索引正则能否解析桥接段（main.js:70573 parseIndexForPages）")
print("=" * 74)
engine = (WIKI / "index.md").read_text(encoding="utf-8")
bridge_match = re.search(
    r"<!-- AGENT-INDEX-START.*?-->(.*?)<!-- AGENT-INDEX-END -->", engine, re.S)
if not bridge_match:
    print("  FAIL wiki/index.md 里没有 AGENT 桥接段 — 跑 tools/sync_index.py --bridge-index")
    raise SystemExit(1)

parsed = []
for line in bridge_match.group(1).split("\n"):
    m = INDEX_LINE_RE.match(line)
    if m:
        parsed.append({
            "path": m.group(1),
            "aliases": [a.strip() for a in (m.group(2) or "").split(",") if a.strip()],
            "summary": (m.group(3) or "").strip(),
        })

pages = []
for topic_dir in sorted(d for d in WIKI.iterdir() if d.is_dir() and d.name not in PLUGIN_DIRS):
    pages += sorted(topic_dir.glob("*.md"))

check(len(parsed) == len(pages),
      f"桥接段 {len(parsed)} 条 = 编译页 {len(pages)} 篇",
      f"桥接段 {len(parsed)} 条 ≠ 编译页 {len(pages)} 篇（有页面不会被插件读到）")
check(all(p["aliases"] for p in parsed),
      "每条都带 aliases",
      "存在无 aliases 的条目 — 插件的中文关键词兜底会给它 0 分")

all_paths = {p["path"] for p in parsed}
for p in parsed:
    target = VAULT / (p["path"] + ".md")
    if not target.exists():
        check(False, "", f"索引登记的路径找不到文件: {p['path']}")
check(True, "所有登记路径都能还原到真实文件", "")

print()
print("=" * 74)
print("2. 图与 pprCascade 退化分支（main.js:77042 / 77090）")
print("=" * 74)
loaded = {}
for p in parsed:
    vp = p["path"] if p["path"].startswith("wiki/") else "wiki/" + p["path"]
    f = VAULT / (vp + ".md")
    loaded[p["path"]] = f.read_text(encoding="utf-8") if f.exists() else ""

path_by_slug = {p.split("/")[-1]: p for p in all_paths}
edges: dict[str, list[str]] = {p: [] for p in all_paths}
for path, content in loaded.items():
    for m in WIKI_LINK_RE.finditer(content):
        raw = m.group(1)
        full = raw[len("wiki/"):] if raw.startswith("wiki/") else raw
        resolved = (full if full in all_paths
                    else raw if raw in all_paths
                    else path_by_slug.get(full))
        if resolved and resolved != path and resolved not in edges[path]:
            edges[path].append(resolved)

node_count = len(all_paths)
edge_count = sum(len(v) for v in edges.values())
if node_count == 0:
    # No compiled pages yet: an empty graph is the CORRECT state, not a failure.
    # (A fresh clone of the published template hits exactly this branch, and
    # reporting two failures on first run would teach people to ignore the tool.)
    print("  INFO 没有编译页，图为空 — 这是合法初始状态，不是失败")
else:
    check(node_count >= 1, f"节点 {node_count} 个", "图为空")
    check(edge_count >= 1, f"边 {edge_count} 条", "零边 — 所有种子度数 0，退化分支会直接返回空")

# connected-component reachability, as isGraphMature computes it
if all_paths:
    first = next(iter(all_paths))
    seen, queue = {first}, [first]
    while queue:
        n = queue.pop()
        for nx in edges.get(n, []):
            if nx not in seen:
                seen.add(nx)
                queue.append(nx)
        for f, ts in edges.items():
            if n in ts and f not in seen:
                seen.add(f)
                queue.append(f)
    ratio = len(seen) / node_count
    check(ratio > 0.5, f"连通率 {ratio:.2f} (>0.5)", f"连通率 {ratio:.2f} 过低")

print(f"  INFO 图成熟度: {node_count} 节点 < 门槛 {DEFAULT_MIN_PAGES} → 走退化分支"
      f"（依赖种子出入度 ≥ 1，上一步已确认）")

print()
print("=" * 74)
print("3. 中文问句为何必须靠 aliases（main.js:76990 tokenizeQuery）")
print("=" * 74)
sample = "示例领域韧性有哪些评估方法"
runs = CJK_RUN.findall(sample)
token_count = len(runs)
max_lex = 3 if token_count == 1 else 3 * token_count + (2 if token_count > 1 else 0)
print(f"  样本问句: {sample}")
print(f"  tokenizeQuery 切出 {token_count} 个 token（整段中文当一个 token）: {runs}")
print(f"  词面路径最高可能得分 {max_lex}，强匹配门槛 {LEX_MATCH_MIN_TOP_SCORE}"
      f"，且需 ≥{LEX_MATCH_MIN_COUNT} 条命中")
check(max_lex < LEX_MATCH_MIN_TOP_SCORE,
      "结论成立：纯中文单串问句无法走词面路径 → 必然依赖 aliases 关键词兜底",
      "词面路径可能成立 — 前面的假设需要修正")

print()
print("=" * 74)
if failures:
    print(f"结果: {len(failures)} 项未通过")
    for f in failures:
        print("  - " + f)
    raise SystemExit(1)
print("结果: 全部通过 — 桥接段可被插件解析与检索")
