# Obsidian LLM Wiki for DeepSeek Harness

A **Karpathy-style LLM Wiki** template: an Obsidian vault where an LLM agent writes and maintains
the knowledge, and the human only picks the sources and asks the questions. It ships the *system*
— folder contract, tooling, validation — and none of the content.

Architecture follows [Astro-Han/karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki)'s
Agent Skill contract. The UI (ingest panels, graph retrieval, query chat) comes from the Obsidian
plugin [`karpathywiki` / obsidian-llm-wiki](https://github.com/gd4ai/obsidian-llm-wiki).

> **This repo is an empty system.** `raw/` and `wiki/<topic>/` are intentionally empty. You fill them
> by ingesting your own sources.

---

## The idea in one paragraph

An LLM-written wiki has one failure mode that ruins it: **the model writes something plausible that the
source never said.** This template's answer is a three-layer contract with a *checkable* invariant:

```
raw/          immutable sources, verbatim         ← the only fixed point
  ↓ compile (the agent writes, with judgement)
wiki/<topic>/ compiled pages, each citing its Raw: sources
  ↓ verify
tools/        scripts that PROVE the invariant still holds
```

**Grounding invariant:** every load-bearing fact in a compiled page — number, date, quotation — must
appear **verbatim** in the raw file that page links to. `lint_grounding.py` enforces it, which is what
makes the invariant real rather than aspirational.

---

## Layout

```
├── inbox/                 drop new sources here; say "process the inbox"
├── raw/<topic>/           immutable sources (never edited, never deleted)
│   └── pdfs/              archived originals
├── wiki/
│   ├── <topic>/           compiled knowledge pages   ← the agent owns these
│   ├── entities|concepts|sources|schema/             ← the plugin owns these
│   ├── index.md           the plugin's retrieval entry point
│   └── log.md             the plugin's own log
├── index.md               global index (authoritative table of contents)
├── LLM Wiki 导航.md        human entry page (explicit full-path links)
├── 更新公告.md             system changelog — how the system itself changed
├── log.md                 append-only content log — what was ingested
├── AGENTS.md              ← THE CONTRACT. Read this first.
├── docs/                  structure reference + operating guide
└── tools/                 validation and maintenance scripts
```

`AGENTS.md` is the authoritative specification: directory contract, hard constraints verified against
the plugin's own source, the ingest/query/lint workflows, page format, and the wrap-up checklist.

---

## Install

1. Clone into a folder and open it as an Obsidian vault.
2. Install and enable the **Karpathy LLM Wiki** community plugin (`karpathywiki`).
3. Configure its LLM provider in Settings → Karpathy LLM Wiki.
4. Point your coding agent (Claude Code, Codex, DeepSeek Harness, …) at this folder — it will pick up
   `AGENTS.md` automatically.

PDF ingestion needs `pypdf`:

```bash
pip install pypdf
```

The scripts hardcode a `VAULT = Path(r"...")` constant at the top, and the docs show a
`PYTHONPATH` line. **Both are meant to be edited for your machine** — that is the only path
configuration you have to touch.

---

## Use

```bash
# 1. drop files into inbox/, then:
python tools/ingest_files.py --plan          # see what was recognised (writes nothing)
python tools/ingest_files.py --topic <name>  # archive + extract into raw/<name>/

# 2. the agent then does the part no script can: triage, compile, cascade, cross-reference
# 3. close the loop:
python tools/sync_index.py --bridge-index    # rebuild index + register pages for the plugin
python tools/lint_grounding.py               # prove every fact traces back to a source
```

The tools, and what each one is actually for:

| Tool | Purpose |
|---|---|
| `ingest_files.py` | mechanical half of ingest: metadata → slug → archive original → write `raw/` |
| `sync_index.py` | rebuild `index.md`; `--bridge-index` registers compiled pages in the plugin's index |
| `lint_grounding.py` | **the gate.** Extracts numbers/dates/amounts and checks them against `Raw:` |
| `verify_bridge.py` | replays the plugin's own parsing regex offline — proves retrieval will work |
| `check_raw_fidelity.py` | archived PDF vs `raw/*.md`, so `raw/` immutability is verified, not assumed |
| `audit_vault.py` | cross-cutting health check: orphans, frontmatter, `updated` drift, stale refs |
| `check_plugin_ready.py` | recomputes the plugin's `llmReady` predicate without touching the network |
| `check_integrity.py` | hash ledger for the contract files, to catch truncation |
| `snapshot.py` | commit a snapshot (the recovery path) |

---

## Two things that will bite you

**1. The plugin overwrites `wiki/index.md` wholesale.** The chain is
`generateFlatIndex` → `indexGenerator.writeFile` → `createOrUpdateFile` →
`vault.process(file, () => content)` — an overwrite, not an append. So **after every plugin-side
ingest or lint, re-run:**

```bash
python tools/sync_index.py --bridge-index
```

**2. The plugin does not segment Chinese text.** `tokenizeQuery` treats an entire CJK run as a single
token, so a Chinese query can never match a title by substring, and the lexical path can never reach
its match threshold. Retrieval then depends entirely on the keyword fallback, which scores
**title +3 / alias +2 / summary +1**. That makes `aliases` in each page's frontmatter a *load-bearing*
field, not decoration:

```yaml
aliases:
  - your-question-words
  - English original name
  - ACRONYM
```

Full analysis, with source line numbers, is in `AGENTS.md` §5.1.

---

## Design decisions worth knowing

- **`raw/` is append-only.** Compiled pages cite it; nothing rewrites it. That is what lets a page
  verified once stay valid forever.
- **Two graphs, deliberately disconnected.** The plugin's `entities/concepts/sources` pages and the
  agent's `wiki/<topic>/` pages do not reference each other. The plugin's LLM will invent things
  (observed: a transliterated author name that appears nowhere in the source), so its output is kept
  out of the trusted track. The only bridge is the marked section in `wiki/index.md`.
- **Validation is layered, and honest about its limits.** `lint_grounding.py` flags candidates, not
  verdicts — derived values and OCR artifacts are expected false positives. It catches fabrication at
  full strength; its noise is in categories you learn to recognise.
- **Reports are not gates.** `audit_vault.py` always exits 0. Only `lint_grounding.py` (malformed
  links) and `check_integrity.py` (damaged files) fail the build. A heuristic check that blocks work
  just teaches people to bypass it.

---

## How this repository was produced

This is the system extracted from a working private vault, with all content removed. Two different
mechanisms were used, because one was not enough:

- **Mechanical substitution** for demonstration values — a sample page title, a sample topic slug, a
  sample filename. The *shape* stays, the content goes.
- **Full replacement** for the files whose value *was* the history: `log.md`, `更新公告.md`,
  `wiki/log.md`, `index.md`, `LLM Wiki 导航.md` and `tools/topics.yaml`. Substitution cannot sanitise
  those — every line describes what was actually ingested, and the `pin:` blocks even carried the
  sources' numeric findings. They ship as documented, empty contracts instead.

The result was verified by a purpose-built scanner that distinguishes a real leak (a source's author,
title, DOI, topic slug) from an expected placeholder (`example-source.pdf`, `<topic>`). It reports
**0 literature values** across all 28 files. The tools were then smoke-tested against the empty wiki
to confirm a fresh clone works: every script compiles, `sync_index.py --check` reports in-sync,
`verify_bridge.py` and `check_integrity.py` exit 0, and the integrity ledger round-trips.

## Language

The tooling, contract and documentation are written in Chinese, because that is the language this
system was built in and the plugin's prompts are configured for. The scripts' docstrings are in
English. Nothing in the design depends on either.

## Licence

MIT — see [LICENSE](LICENSE). The template is yours to use and adapt. No third-party content is
included: the knowledge directories ship empty.
