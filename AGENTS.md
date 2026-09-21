# AGENTS.md — ObsidianVault for DSH（LLM 本地知识库）

本仓库是一个 **Karpathy 式 LLM Wiki**：LLM 编写并维护知识，人负责挑选素材与提问。
架构对齐 [Astro-Han/karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) 的 Agent Skill 契约，
并通过 Obsidian 插件 [`karpathywiki`（obsidian-llm-wiki）](https://github.com/gd4ai/obsidian-llm-wiki) 提供 UI 操作与 Graph View。

**一句话边界：`raw/` 与 `wiki/<topic>/` 归你（Agent）；`wiki/entities|concepts|sources/` 与 `wiki/index.md`、`wiki/log.md` 归插件。**

---

## 1. 目录契约

```
ObsidianVault for DSH/            ← 仓库根（Obsidian vault + Agent 项目根，两者重合）
├── inbox/                        ← 【暂存区】用户投递新素材的收件箱。不属于知识库，不参与 grounding
│   ├── README.md                 ← 给用户看的操作说明
│   └── *.pdf|md|txt|html         ← 未处理的素材；处理后会被移走（空 = 全部处理完）
├── raw/                          ← 【不可变】已入库原始素材。你只读，永不修改、永不删除
│   └── <topic>/
│       ├── YYYY-<作者>-<slug>.md ← 一篇素材一个文件
│       ├── pdfs/                 ← 原始 PDF 存档（只增不改）
│       └── _source/              ← 文本/HTML 素材的原件存档（只增不改）
├── wiki/                         ← 【你拥有】编译后的知识
│   ├── entities/                 ← 【插件拥有】人物/机构/项目/产品等命名实体页
│   ├── concepts/                 ← 【插件拥有】主题/方法/定义等概念页
│   ├── sources/                  ← 【插件拥有】每篇被 UI 摄取的原稿一页（溯源锚点）
│   ├── schema/                   ← 【插件拥有】词汇表与栏目模板（含 schema/config）
│   ├── contradictions/           ← 【插件拥有】矛盾记录
│   └── index.md                  ← 【插件追加 / Agent 后缀】头部由插件维护；末尾 AGENT 标记段登记编译页
├── index.md                      ← 【半自动】全局索引（唯一权威目录）。表外手写、表内由脚本生成
├── LLM Wiki 导航.md               ← 【半自动】人类入口页：显式全路径链接到所有编译页。Obsidian 可跳转，插件读不到
├── 更新公告.md                    ← 【手工维护】系统更新日志（changelog）。只记「改变系统怎么运转」的改动
├── log.md                        ← 【Agent】追加式操作日志。只追加，不改写旧条目
│                                    （记录内容操作：ingest / query 归档 / lint）
│                                    （插件另有自己的 wiki/log.md，两者互不相干，见第 4 节）
├── docs/                         ← 本仓库的说明文档（改契约时同步更新）
│   ├── 目录结构说明.md              ← 目录契约 + 与 SKILL.md 的对齐清单与偏离理由
│   └── 操作指南.md                 ← DSH 中的指令范式与插件分工
├── tools/                        ← 本仓库的脚本（见第 7 节）
└── .obsidian/                    ← Obsidian 配置与插件。除插件设置外不要动
```

### 硬约束（来自源码，不是约定俗成）

| 事实                                                                | 对你的影响                                                       |
| ----------------------------------------------------------------- | ----------------------------------------------------------- |
| 插件重建 `wiki/index.md` 是**整文件覆盖**（`indexGenerator.writeFile` → `createOrUpdateFile` → `vault.process(file, () => content)`，见 `main.js:75521`、`76627`） | 写进该文件末尾的 AGENT 标记段**会在每次插件 ingest 后被抹掉**；必须重跑 `sync_index.py --bridge-index` 恢复 |
| 插件 Query 引擎只解析 bullet 行：`^- \[\[path\|Name\]\] - summary$`，且**索引里没有的页面它永远不读**（`buildWikiContext`，`main.js:79003`） | 想让插件检索到你的编译页，只有把它们写进 `wiki/index.md`；根 `index.md` 与导航页都无效 |
| 插件只索引 `wiki/{entities,concepts,sources}`                          | 你写的 `wiki/<topic>/` 页面不在插件图谱里——这是**刻意的隔离**，避免插件的 LLM 覆写你的编译结果 |
| 插件正文禁止 `[[sources/...]]` 链接                                       | 溯源只放在 frontmatter 的 `sources:` 字段或 `Raw:` 元数据行              |
| 插件自己的日志 `wiki/log.md` 是**追加**的，超 512 KB 裁掉最旧条目                  | 插件日志与你的根 `log.md` 是两个独立文件（见第 4 节）                            |
| 插件自动维护 `wiki/schema/config` 词表（已初始化，v1）                        | 写页面 tag 时优先复用词表里的值，不要新造同义项                                  |

---

## 2. 三种操作的触发时机

### 2.1 Ingest（入库 → 编译）

**触发**（任一）：
- 用户说「**处理收件箱**」「收件箱里有新东西」「Ingest 收件箱」→ 走**收件箱模式**（下方 A）
- 用户说「Ingest 这篇 / 这个链接 / 这个 PDF」「把 X 加进 wiki」→ 走**指定文件模式**（下方 B）

**绝不触发**：普通问答。问答走 Query，Query 不写任何文件。

---

#### A. 收件箱模式（用户的默认工作方式）

用户把文件丢进 `inbox/` 就完事，你收到「处理收件箱」后按顺序做：

1. **先看一眼**：`python tools/ingest_files.py --plan` —— 列出识别到的标题/作者/类型，以及脚本**推测**的主题。
   推测只是参考，**主题归属由你判断**。
2. **定主题**（见下方第 2 步规则），可以一个主题也可以分批。
3. **执行机械部分**：`python tools/ingest_files.py --topic <主题>`。
   它读元数据、生成 slug、归档原件、写出 `raw/<主题>/<slug>.md`，并把文件**移出**收件箱。
4. 接着做第 4–8 步（triage / compile / 级联 / 收尾 / 自检）——与指定文件模式完全相同。
5. **向用户汇报**：处理了几个文件、分别进了哪个主题、建了/改了哪些知识页、Triage 结论、有无 Disputed。

**空收件箱 = 全部处理完**。这是这个流程的自检信号；如果处理完还有文件剩在收件箱，说明有文件被跳过
（不支持的类型 / 读取失败 / 尚未下载完），要明确告诉用户。

#### B. 指定文件模式

用户直接给了路径或链接时，跳过收件箱：

```powershell
$env:PYTHONPATH="D:\DSH\.tools\pylibs"     # pypdf 装在这里，不入全局环境
python tools/ingest_files.py --topic <topic-dir> "<文件路径或通配符>"
# --dry-run 预演；--force 覆盖；--year 覆盖年份
```

---

#### 两种模式共同的后续步骤

1. **取内容**（机械部分，已由脚本完成）。`ingest_files.py` 支持三类来源：

   | 类型 | 处理方式 | 原件归档到 |
   |---|---|---|
   | `.pdf` | 提取文本层，逐行保真，页码标记转 HTML 注释 | `raw/<topic>/pdfs/` |
   | `.md` / `.txt` | 直接收录；解析 frontmatter 的 title/author/date/tags/url | `raw/<topic>/_source/` |
   | `.html` / `.htm` | 去标签转纯文本 | `raw/<topic>/_source/` |

   它自动识别标题、作者姓、期刊、DOI、发布日期，生成 `<年>-<姓>-<标题>` 形式的 slug
   （英文标题转 kebab-case；**中文标题保留 CJK 字符**，不做音译）。**BOM、GB18030、UTF-16 都能正确处理。**

   它**不做** triage、编译、索引、日志——那些需要判断，是下面这些步骤。URL 素材用抓取工具，粘贴文本直接采用。
2. **定主题目录**。先看 `raw/` 下已有子目录，能复用就复用；只有确实是新领域时才新建（保持主题目录少而稳定）。
   主题名用 kebab-case 英文。**主题是知识库层面的判断，不让脚本猜**——脚本的 `--plan` 输出只是参考。
3. **写入 `raw/<topic>/`**（脚本完成），命名 `<年>-<作者姓>-<slug>.md`。
   - 元数据头必须含：来源 / 采集日期 / 发布日期（未知写 `Unknown`）。
   - **逐字保真**：不改写、不重排、不删减措辞；只清理页眉页脚、导航栏、断行等格式噪声。
   - 同名文件已存在时追加 `-2`、`-3` 后缀（或 `--force` 覆盖）。
4. **Triage（分诊）**。在 `wiki/` 中检索素材的关键实体与同义词，然后**明确声明**处置结论：
   - `New` — 新建一篇或多篇知识页
   - `Update` — 并入已有页面
   - `Disputed` — 与既有内容冲突（可与 New/Update 并用，按第 3 节标注）
   - `No material` — 除了已有知识没有新东西：保留 raw 文件、只记日志，**不要硬凑一篇文章**
5. **Compile（编译）**。同一核心论点 → 并入既有文章；新概念 → 新建 `wiki/<topic>/<概念名>.md`（文件名取概念名，不取素材文件名）；跨主题 → 放在最相关的目录并加 See Also。
   每篇页面的 frontmatter 必须带 **`aliases`**（中文问题词 + 英文原名 + 缩写），理由见 5.1。
6. **Cascade（涟漪更新）**。不要只看索引：全文检索素材涉及的关键实体、别名与论断，把所有**实质受影响**的非归档页面一并更新，并刷新其 `updated`。
7. **收尾**：`python tools/sync_index.py --bridge-index` 重建根 `index.md` 并把编译页登记进 `wiki/index.md`；在 `LLM Wiki 导航.md` 补一行；向 `log.md` 追加条目（格式见第 4 节）。
8. **自检**：`python tools/lint_grounding.py`，确认数字与日期都能在 Raw 中找到，且没有畸形 wiki-link（退出码须为 0）。

### 2.2 Query（查询）

**触发**：「我对 X 了解多少」「总结一下 Y」「比较 A 和 B」。
也可以是「Query 我对示例领域韧性的了解」这类显式指令。

1. 先读 `index.md` 定位候选页，再对 `wiki/` 做全文检索，**同时检索同义词/英文术语**（本仓库素材多为英文论文，页面为中文摘要）。
2. 读页面，综合成答案。
3. **优先使用 wiki 内容**，不要用你自己的训练知识覆盖它；两者不一致时明确指出。
4. 用仓库相对路径引用：`[页面标题](wiki/<topic>/<article>.md)`。
5. **在对话中作答，不写任何文件**——除非用户明确说「归档 / 保存到 wiki」。

> 只有当索引与全文检索都为空时，才能说 wiki 里没有相关内容，并且要说明你检索过。

**归档**：用户明确要求时，把答案写成新页面 `wiki/<topic>/<主题>.md`，frontmatter 用 `type: archive`、`archived: <日期>`，**不写 `Raw:` 字段**（内容不是来自 raw），把引用改写成相对本文的相对路径；然后 `sync_index.py`，在 index 的 Summary 前加 `[Archived]` 前缀，并向 `log.md` 追加 `query | Archived: <标题>`。归档页永不参与 cascade 更新。

### 2.3 Lint（校验）

**触发**：「Lint 一下 wiki」「检查一下知识库」。

**可自动修复**（做完在日志里说明修了什么）：

- **索引一致性**：`wiki/<topic>/` 有文件但 `index.md` 没条目 → `python tools/sync_index.py`；`index.md` 条目指向不存在的文件 → 标记 `[MISSING]` 并保留条目，交给用户决定；索引 `Updated` 与页面 frontmatter `updated` 不一致 → 以页面为准。
- **内部链接**：`wiki/<topic>/` 正文中的 wiki-link 或 markdown 链接失效 → 在 `wiki/` 全库搜索同名文件；**恰好一处匹配则修路径**；零处或多处则报告用户。
- **Raw 引用**：`Raw:` 行指向不存在的 raw 文件 → 在 `raw/` 搜索同名文件；恰好一处匹配则修路径；否则报告。
- **See Also**：链接目标不存在 → 恰好一处匹配则修路径；零处匹配则删除该链接（死链不是承载性内容）；多处则报告。

**只报告、不修改**：

- 跨页面的事实矛盾
- 被新素材推翻但未标 `Status:` 的旧论断
- 缺少冲突标注、`Status:` 块格式不完整
- 缺失的交叉引用（建议，不擅自添加）
- 孤儿页（没有任何其他 wiki 页面链入）
- 频繁提及但没有独立页面的概念
- 归档页引用的源页面在此之后被大幅更新

**证据保真**：`python tools/lint_grounding.py [页面.md ...]`。报告出来的是**候选疑点而非裁决**——两类已知的假阳性：

1. **派生值**（你算出的和、差、比值）本就不会逐字出现在原文里。
2. **PDF 文本层的 OCR 瑕疵**。提取器常把数学符号误识别：`=` 变 `:`（如 `Td ¼ 0:25h` 实为 0.25 h）、`ﬃ`/`ﬁ` 连字断裂、公式字符错位。

对 OCR 瑕疵**两者兼顾**：页面里保留原文形态以便逐字核对，加注时**只用原文可核实的说法**（别引入原文没有的数值写法，否则注解本身会变成新疑点）。例如：

```markdown
原文记作 **T_d = 0:25h**，同句自注为「15 min，等于采样间隔」。（`0:25` 系 PDF 文本层将等号误识别为冒号的产物。）
```

**绝不**为了让 lint 通过而把数字改回「正常」写法——那会破坏 grounding invariant。也**绝不**因为脚本报疑点就认定自己写错了：逐条对照原文语境判断。

**收尾**：追加 `## [YYYY-MM-DD] lint | <N> issues found, <M> auto-fixed>`。

---

## 3. 知识页格式

```markdown
---
title: 页面标题
type: concept            # concept | archive
tags:
  - example-domain/resilience
aliases:
  - 别名或英文名
  - 别人可能用来问它的说法   # 承重字段：插件种子选择靠它命中，见 5.1
updated: YYYY-MM-DD      # 知识内容最后变化之日；错别字/格式修正不刷新
summary: 一句话摘要（用于 index.md 表格，60–100 字）
---

# 页面标题

> Sources: 作者, YYYY-MM-DD; 作者, YYYY-MM-DD
> Raw: [素材名](../../raw/<topic>/<file>.md); [素材名2](../../raw/<topic>/<file2>.md)
> Updated: YYYY-MM-DD

## Overview

{一段话概括本页要点。}

## {正文小节}

{从素材综合出的连贯结构。不要逐句照抄原文；提炼并重组。}

> **Status: Outdated** (YYYY-MM-DD)
> {什么变了、当前理解是什么，附来源归属。}

> **Status: Disputed**
> {各方对立主张，各自附来源归属。}

## See Also

- [[其它页面标题]]
```

格式约定：

- **`Raw:` 行是承重字段**：必须是指向 `raw/` 的 markdown 链接，从 `wiki/<topic>/` 出发用 `../../raw/<topic>/<file>.md`。
- **`Sources:` 行**：作者/机构/刊物名 + 日期，分号分隔。
- **`updated`** 有两处（frontmatter 与 `>` 元数据行），必须一致。
- 原文语言为英文而页面写中文时，**专有名词首次出现保留英文原名**。

### 引用格式约定（重要）

- **正文交叉引用一律用 wiki-link，且只指向 `wiki/` 下的编译页面**：`[[示例页面 A]]`。
- **绝不**用 wiki-link 指向 `raw/`：`[[raw/...]]` 是错误写法。raw 素材只通过页面的 `Raw:` 字段出现，保持「原始素材不可变、不被正文引用」的契约。
- **绝不**用 wiki-link 指向 `wiki/entities|concepts|sources/`（插件拥有的命名空间），也不要手工往那三个目录写文件。
- See Also 用短式链接（`[[页面标题]]`），Obsidian 会自行解析；跨主题页面同理。

### Grounding invariant（不可违背）

**知识页里每一个承重事实——数字、日期、直接引语——都必须逐字存在于该页 `Raw:` 字段所链接的 raw 文件中。**

- 写之前先在 raw 里**定位**，不要凭记忆写。
- 原文写 `42K` 就写 `42K`，不要改写成 `42,000`；原文单位与量纲照搬。
- 派生值（和、差、百分比、计数）要**列出组成部分**，使每个组成部分都能在 raw 中找到。
- 定位不到就**不要写精确形式**：要么略去，要么只作定性表述。
- 因为 `raw/` 不可变，一篇校验通过的页面会一直成立——没有增量状态需要维护。

### 级联与历史

- 新素材推翻或矛盾于既有论断时，**保留旧论断但加 `Status:` 块**（`Outdated` 需带日期；`Disputed` 需列出双方主张）。**永不静默改写历史。**
- 归档页是时间点快照，**永不**被 cascade 更新。

---

## 4. 日志格式

**两个日志文件，互不相干**（这是实测结论，别搞混）：

| 文件 | 归属 | 内容 |
|---|---|---|
| `log.md`（仓库根） | **Agent** | skill 规范格式的追加式日志。只追加，不改写旧条目 |
| `wiki/log.md` | **插件** | 插件自己的操作日志（`<!-- llm-wiki-log-header-start -->` 开头），记录每次 ingest/lint 及产出的页面，超 512 KB 自动裁掉最旧条目 |

不要再问「插件会不会污染我的日志」——它写的是 `wiki/log.md`，从不碰根 `log.md`。

**Agent 写根 `log.md`（对齐 skill 规范）**：

```
## [YYYY-MM-DD] ingest | <主页面标题>
- Disposition: <New; Update; Disputed>
- Raw: <仓库相对路径 raw/topic/file.md>
- Updated: <被涟漪更新的页面标题>

## [YYYY-MM-DD] ingest | no material: <仓库相对路径 raw/topic/file.md>
- Disposition: No material

## [YYYY-MM-DD] query | Archived: <页面标题>

## [YYYY-MM-DD] lint | <N> issues found, <M> auto-fixed>
```

**插件写 `wiki/log.md`（别去改它）**：

```
## [YYYY-MM-DD HH:MM] ingest | <source_title> · <耗时> · <模型> · <体积>
**Created pages**：[[concepts/X]]
**Updated pages**：[[entities/Y]]
```

规则：没有涟漪更新就省略 `- Updated:` 行；`no material` 的标题是机器可读的清单键，必须逐字保持该形式；只追加，不改写既有条目。

---

## 4.1 系统更新公告（`更新公告.md`）

**两个记录文件，分工严格不同**：

| 文件 | 记什么 | 读者 |
|---|---|---|
| `log.md` | **内容操作**：ingest / query 归档 / lint。追加式，一次操作一条 | 审计「这个知识库是怎么长起来的」 |
| `更新公告.md` | **系统更新**：改变「这套知识库怎么运转」的改动 | 你（了解系统现状、是否要动手）+ 将来的 Agent（别重复踩坑、别回退决策） |

**触发**：任何改动**契约、目录结构、工具脚本、插件配置、依赖、流程规则**之后，都要在 `更新公告.md` **顶部**加一条。
**不触发**：摄入素材、编译知识页、重建索引、追加日志——这些是内容操作，只进 `log.md`。

**每条必须写清**：

- 一句话标题（带递增编号）+ 日期
- **问题/动机** —— 为什么要改
- **改动** —— 具体改了什么（文件 + 行为）
- **影响** —— 系统变成什么样了；**你要动手吗**（多数是「不需要」，但这句必须写）
- **关键决策（勿回退）** —— 这个决定将来可能被误判时，写清理由

**特别重要**：凡是**推翻了旧结论**或**改变了既定流程**的改动，必须显式写出来（见 `更新公告.md` 里那次自我纠正）。
否则将来的 Agent 会照旧行为办事，把已经修过的坑再踩一遍。

**删除文件时**：除在条目里写明「路径 + 为什么删 + 什么替代了它」，**还要登记到文末的「已删除文件清单」**——
那是全库唯一的删除台账。清理已删文件的文档引用时，要区分**当前描述**（工具表、目录树、FAQ —— 必须改）
与**历史记录**（`log.md`、本文件的旧条目 —— 不改写历史）。

**编号只增不重排**，最新在最上面，历史条目不改写（与 `log.md` 的追加式原则一致）。

---

## 5. 与插件 obsidian-llm-wiki 的协作边界

| 操作 | 走插件 UI | 走 Agent 文件读写 |
|---|---|---|
| 摄取**散落在仓库里、格式不规范的 Obsidian 笔记**，让插件自动抽实体/概念并建 `[[wiki-link]]` 图谱 | ✅ 插件强项：命令面板 → `Ingest multiple files` / `Ingest from folder` | ❌ |
| 摄取**外部素材**（URL、PDF、粘贴文本）并做保真编译、撰写结构化长文 | ❌ 插件不做逐字保真，也没有 `Raw:` 溯源契约 | ✅ 你写 `raw/` + `wiki/<topic>/` |
| 实体/概念之间自动建链接、自动生成 stub 页、去重合并 | ✅ 插件的 Lint 管线 | ❌ |
| 全局索引 | 插件维护 `wiki/index.md`（它自己的检索用） | 你维护仓库根 `index.md`（人的目录） |
| 操作日志 | 插件追加它自己的 ingest/lint 条目 | 你追加 skill 格式条目 |
| 提问检索 | 插件 Query Wiki 面板（图检索 + LLM 综合） | 你读 `index.md` + 全文检索 `wiki/` |
| Graph View 可视化 | ✅ Obsidian 原生，插件建的 `[[链接]]` 与你写的 wiki-link 都会出现在图上 | — |

**三个不要**：

1. **不要**往 `wiki/entities|concepts|sources|schema/` 写文件——插件拥有它们，它的 LLM 会覆写。
2. **不要**手改 `wiki/index.md` 的**插件部分**。它是插件检索入口，格式必须保持 bullet 行；你唯一能碰的是末尾 `<!-- AGENT-INDEX-START -->` 与 `<!-- AGENT-INDEX-END -->` 之间的段落，且那段由 `sync_index.py --bridge-index` 生成，不要手写。
3. **不要**把 `raw/` 里的素材搬进 `wiki/` 或反向修改 `raw/`——raw 是唯一的不动点，是 grounding 的锚。

**插件侧 LLM 需要你自己配置**：在 Settings → Karpathy LLM Wiki 里选 provider、填 API key、选 model，点 Test Connection 通过后再保存。
核实是否就绪：`python tools/check_plugin_ready.py`（按 `main.js` 的判定逻辑复算，不联网、不打印密钥）。

插件已初始化 `wiki/schema/config`（v1），其中规定 `tags` 只能取「Active Tag Vocabulary」里的值，不在列表中的会被系统移除。**该约束只作用于插件写出的 `entities/`、`concepts/`、`sources/` 页面**——你的 `wiki/<topic>/` 页面不归插件管，不会被它改写或清洗；但为了让 tag 命名保持同一套习惯，`wiki/<topic>/` 页面的 `tags` 统一写成 `example-domain/<子类>` 这种斜杠命名空间形式（插件把它识别为 domain vocabulary）。

若插件报 `LLM client not configured`，先确认设置已保存（未保存的空闲副本会显示陈旧状态），再以 `check_plugin_ready.py` 的实测结果为准。

### 5.1 实测结论（2026-09-21，一次插件摄取的全量核对）

已用一篇测试笔记跑完整摄取并逐项核对，双轨隔离**成立**：

| 核对项 | 结果 |
|---|---|
| Agent 编译页（`wiki/example-topic/`）、`raw/`、根 `index.md`、`log.md` 的字节数、条目数与修改时间 | **完全未变**（摄取期间只写入了时间戳） |
| 插件写入范围 | 只新增 `wiki/{entities,concepts,sources}/` 若干页面 + 首次生成 `wiki/index.md` |
| `raw/` 是否被当作源目录扫描 | 否，只生成 1 个 `sources/` 页，对应被摄取的 `inbox/` 笔记 |
| 摄取后 `sync_index.py --check` / `lint_grounding.py` | 仍同步 / 仍 0 疑点 |

**插件侧溯源机制（与你的 `Raw:` 是两套并行机制）**：`sources/` 页的 frontmatter 用 `source_file: "[[inbox/<原笔记>.md]]"` 指回**笔记原文**（vault 相对路径、带空格），并用 `contentHash` 记录内容哈希以检测源文件变化。它**不引用 `raw/`**，`Wiki:<topic>` 也不是它的检索范围。

**因此两套图谱默认互不连通**：插件页面不会引用 `wiki/<topic>/`，你的页面也不引用 `wiki/entities|concepts/`。要打通只有一条真正有效的路——**把编译页登记进 `wiki/index.md` 并带上 aliases**：

```
python tools/sync_index.py --bridge-index          # 写入/刷新 AGENT 标记段（自动带 aliases）
python tools/sync_index.py --bridge-index --check   # 复核是否被插件覆盖掉
```

它把编译页渲染成插件自己的 bullet 格式：

```
- [[wiki/example-topic/示例页面 A|示例页面 A]] `aliases: EXA, 全局韧性分析示例, 示例领域示例评估, 示例评估方法, …` - 以应力—应变曲线评估韧性：…
```

#### 为什么 aliases 是承重的，不是装饰

这是一次真实失败的根因，务必理解：

1. 插件的种子选择器 `selectPprSeeds`（`main.js:77861`）分两级——词面匹配，失败后走 LLM 关键词兜底。
2. 词面匹配用 `tokenizeQuery`（`main.js:76990`）切词，而它对 CJK 的处理是：**整段中文当一个 token**——
   `const cjkRun = query.match(/[一-鿿...]{2,}/g)`。于是「示例领域韧性有哪些评估方法？」成了**一个超长 token**，
   不可能子串匹配任何标题。
3. 结果 `lexTopScore` 最高只能拿 3 分，而强匹配门槛是 `LEX_MATCH_MIN_TOP_SCORE = 5`（`main.js:181`）——
   **纯中文问句永远无法走词面路径**，必须依赖关键词兜底。
4. 关键词兜底按 `标题 +3 / 别名 +2 / 摘要 +1` 打分（`main.js:77025`）。页面标题若是「示例页面 A」，
   而 LLM 抽出的关键词是「示例评估」「评估方法」，就**一分都拿不到**，该页被彻底跳过 → 面板回落到通用知识库并给出
   「Wiki 中无相关页面」的警告。

**所以每篇编译页必须在 frontmatter `aliases:` 里放上别人可能用来问它的说法**（中文问题词、英文原名、缩写、
上位概念）。`sync_index.py` 会自动把它们写进索引行的 `` `aliases: …` `` 组，插件据此打分。

对中文知识库尤其关键：**插件不做中文分词，别名/标题是它唯一能命中你的入口。**

#### 另一个坑：图成熟度阈值

`isGraphMature`（`main.js:77042`）要求 **≥ 30 个节点**（`DEFAULT_MIN_PAGES = 30`）才算「成熟图」。
小 wiki 会走 `pprCascade` 的退化分支：依赖种子的 **出入度 ≥ 1**（`DEFAULT_SEED_MIN_DEGREE`）。
好消息是编译页之间只要有 `[[wiki-link]]` 互通就有边。
**结论：编译页之间的交叉引用要维持住，别让页面变成孤儿。**

> ⚠️ **关键前提（源码级已确认）**：插件重建 `wiki/index.md` 是**整文件覆盖**，不是追加。
> 链路是 `generateFlatIndex` → `indexGenerator.writeFile` → `createOrUpdateFile` → `vault.process(file, () => content)`（`main.js:75521`、`76627`）。
> **所以每次用插件 UI 摄取（或跑插件 Lint）之后，AGENT 标记段都会被整段抹掉。**
>
> 标准操作：**插件 ingest 完成后，立刻跑 `python tools/sync_index.py --bridge-index` 恢复**。

除 `wiki/index.md` 外没有别的通路：根 `index.md` 与根目录的 `LLM Wiki 导航.md` **Obsidian 能跳转、Graph View 有边，但插件 Query 绝不读**（`buildWikiContext` 只读 `wiki/index.md`，索引外的页面一律不加载）。

**已知瑕疵（插件侧，无需你处理但要知情）**：

- 插件正文会出现**裸链接**（如 `[[示例领域]]`、`[[示例页面 A]]`），Obsidian 能按文件名解析；这是插件 LLM 的输出习惯，源码只强制 `[[path|display]]` 用于 entities/concepts，未强制统一。
- 插件会把**被摄取的笔记本身**也建成一个 `entities/` 页（如 `entities/<笔记名>.md`），并可能从你的工程性描述里派生出实体/概念页（本次派生出了 `entities/obsidian-llm-wiki.md`）。
- 插件 LLM 会**自行推断别名**，本次给作者编了一个音译名 `某作者`——原文里没有。因此**不要信任插件页面的 alias 与数值**，它们是 LLM 生成物，不走你的 grounding 校验。凡要引用到你的知识页里的插件内容，必须回到 `raw/` 核实。

---

## 6. 命名与语言约定

- **日期**：日志、采集日期、归档日期用今天；`Published` 取素材自身日期，未知写 `Unknown`；`updated` 只反映知识内容变化。
- **raw 文件名**：`<年>-<作者姓>-<标题slug>.md`，由 `ingest_files.py` 生成。英文标题转小写 kebab-case 并截断到 10 个词；
  **中文标题保留 CJK 字符**（不音译，因为音译会丢掉可检索性）。作者缺失时该段省略，年份缺失时写 `unknown`。
- **页面标题**：中文概念名，可含空格与英文缩写（如 `示例页面 A`）。Obsidian 对空格与中文支持良好，不要在标题里用 `|`、`[`、`]`、`#`、`^`。
- **主题目录名**：kebab-case，一层，英文（如 `example-topic`）。中文只出现在页面标题里。
- **wiki-link 显示文本**：写页面标题本身，不要带目录前缀。

---

## 7. 工具脚本（`tools/`）

| 脚本 | 用途 |
|---|---|
| `ingest_files.py` | **入库主入口**：收件箱模式（不带路径参数，处理 `inbox/`）与指定文件模式都走它。读元数据 → 生成 slug → 归档原件 → 写 `raw/<topic>/<slug>.md`。支持 `.pdf/.md/.txt/.html`。参数：`--plan`（只给主题建议）、`--topic`、`--dry-run`、`--force`、`--year` |
| `check_raw_fidelity.py` | **PDF 原件 → raw 的保真校验**（补全 grounding 链条缺的一环）：归档 PDF 的文本层 vs 对应 `raw/*.md` 的 6-gram 存活率，用于确认归档原件与 raw 文本一致。缺 raw 时退出码 1；存活率低只报告不判死（可能是合法的格式清理）。**怀疑 `raw/` 被动过时跑它** |
| `check_integrity.py` | **契约文件完整性跳闸器**：对全部**无备份的**手工维护文件（`AGENTS.md`、`更新公告.md`、`log.md`、索引、导航页、docs、脚本）做哈希台账，检出 `SHRUNK`/`EMPTY`/`MISSING` 时退出码 1。**不是备份、也不是冻结**——改过契约后要 `--bless` 更新基线。起因见 `更新公告.md` |
| `snapshot.py` | **git 快照（唯一的恢复途径）**：把当前工作树提交为一次快照。`--status` 看改动、`--log` 看历史、`--restore <路径>` 从 HEAD 取回单个文件。**每次会话收尾必须跑一次**（见第 8 节）。起因见 `更新公告.md` |
| `sync_index.py` | 重建根 `index.md` 的表格行；`--check` 报告漂移并检查导航页是否漏主题；`--bridge-index` 额外把编译页（含 aliases）登记进 `wiki/index.md` 的 AGENT 标记段 |
| `topics.yaml` | 主题显示名、一句话说明、Summary 覆盖、`settings.index_name`（索引链接是否用显式全路径） |
| `lint_grounding.py` | 抽取知识页里的数字/日期/金额核对 Raw，并检测畸形（空目标）wiki-link；退出码 1 表示有畸形链接 |
| `verify_bridge.py` | **离线重放插件检索链路**：用插件自己的正则（`main.js:70573`）验证桥接段可被解析、图与 `pprCascade` 退化分支可用、并验证「中文问句必须靠 aliases」的结论。**页面改名或新增后建议跑一次** |
| `check_plugin_ready.py` | 按插件源码逻辑复算 `llmReady`，并给仓库结构拍快照；`--diff` 对比插件是否动过文件 |
| `audit_vault.py` | **全库结构审计**（横向体检，补前面几个脚本的盲区）：契约树完整性、孤儿页、frontmatter 必填字段、`updated` 两处一致性、空 wiki-link、过期文件名引用、工具表登记齐全。**只报告不改文件**，退出码恒为 0——它是体检报告，不是闸门 |

Python 依赖（pypdf）装在你自己的 Python 环境里，**不污染全局环境**；跑脚本前设：

```powershell
$env:PYTHONPATH="D:\DSH\.tools\pylibs"
```

---

## 8. 每次会话的收尾检查

做完任何写操作后，逐项确认：

- [ ] 若是**收件箱模式**：`inbox/` 里除 `README.md` 与 `.gitkeep` 外**已空**；有文件遗留必须说明原因
- [ ] `raw/` 里新增的文件**只增不改**，且有完整元数据头
- [ ] 每个新论断都能在对应 raw 中定位（`python tools/lint_grounding.py`，退出码须为 0）
- [ ] 受影响页面的 `updated`（frontmatter 与元数据行两处）已刷新且一致
- [ ] `python tools/sync_index.py --bridge-index --check` 通过（或已运行不带 `--check` 的版本）
- [ ] 新增/改名知识页时，`LLM Wiki 导航.md` 里也补了对应的一行
- [ ] `log.md` 追加了对应条目
- [ ] 若本次动了**契约 / 目录结构 / 工具 / 插件配置 / 流程规则**，`更新公告.md` 顶部加了一条（只记系统更新，不记摄入）
- [ ] 没有手工改动 `wiki/entities|concepts|sources|schema/`，也没有改动 `wiki/index.md` 里 AGENT 标记段之外的内容
- [ ] **写文档内容只用文件编辑工具，绝不用 shell 重定向 / `Set-Content` / `Add-Content` 写入**（一次 shell 写入曾把 `AGENTS.md` 截断成 0 字节）。动过契约文件后跑 `python tools/check_integrity.py --bless` 更新完整性基线
- [ ] **本次会话结束前跑 `python tools/snapshot.py "一句话说明这次改了什么"`** —— git 是本仓库唯一的恢复途径。
      凡是改了 `AGENTS.md` / `更新公告.md` / `log.md` / `tools/` / `wiki/` / `raw/`，**都必须快照**。
      提交信息写清「改了什么」，因为它是将来唯一的线索来源

---


