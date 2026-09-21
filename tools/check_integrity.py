"""Integrity ledger: detect silent damage to the files that have no backup.

Why this exists (a real incident, 2026-09-21): a shell write to `AGENTS.md`
failed midway and left the file at 0 bytes. There is no git repo, no
`.obsidian/file-recovery`, no `.bak` anywhere in the vault — so the only reason
it could be restored is that its full text happened to still be in the agent's
context. Had that happened a few turns earlier, the vault's entire contract
would have been lost. `raw/` is protected by `check_raw_fidelity.py`; nothing
protected the *contract* files.

What it does: hashes the critical hand-maintained files and compares against
`docs/.integrity.json`. It is a TRIPWIRE for accidental truncation or silent
edits — not a backup, and not a freeze (these files are SUPPOSED to change).

Reading the result:
  * `changed`  — expected whenever you edit the contract. Re-bless with --bless.
  * `SHRUNK`   — the file lost bytes. Almost always damage. Look at it FIRST.
  * `EMPTY`    — 0 bytes. Damage.
  * `MISSING`  — damage or an intentional delete (then re-bless).
  * `new`      — a contract file the ledger has never seen.

Usage:
    python tools/check_integrity.py            # compare, exit 1 on SHRUNK/EMPTY/MISSING
    python tools/check_integrity.py --bless    # accept the current state as the baseline
    python tools/check_integrity.py --list     # show the ledger

Deliberate exclusions: `raw/` (covered by check_raw_fidelity.py), `wiki/` (the
plugin rebuilds parts of it), and anything under `.obsidian/`.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

VAULT = Path(r"<你的仓库>")
LEDGER = VAULT / "docs" / ".integrity.json"

# Hand-maintained files with no other recovery path. Add to this list when a new
# one appears AND register it in AGENTS.md section 7's tool table neighbour list.
WATCHED = [
    "AGENTS.md",
    "更新公告.md",
    "log.md",
    "index.md",
    "LLM Wiki 导航.md",
    "docs/目录结构说明.md",
    "docs/操作指南.md",
    # The public repo's README and LICENSE: their authoritative copies live here,
    # not at the vault root, and the vault root is the only place a person thinks
    # to look. Truncating one would silently change what the world downloads.
    "docs/publish/README.md",
    "docs/publish/LICENSE",
    "inbox/README.md",
    "tools/topics.yaml",
]

# Scripts: a truncated script is easier to notice (it stops running), but a
# silently TRUNCATED one that still runs is the dangerous case.
WATCHED_SCRIPTS = "tools/*.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def watched() -> list[Path]:
    files = [VAULT / w for w in WATCHED]
    files += sorted((VAULT / "tools").glob("*.py"))
    return [f for f in files if f.exists()]


def census() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in watched():
        rel = path.relative_to(VAULT).as_posix()
        out[rel] = {"sha256": sha(path), "bytes": path.stat().st_size}
    return out


def main(argv: list[str]) -> int:
    if "--list" in argv:
        if not LEDGER.exists():
            print("no ledger yet; run without --list to create one")
            return 0
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        for rel, meta in sorted(data.get("files", {}).items()):
            print(f"  {meta['bytes']:>8}B  {rel}")
        return 0

    now = census()

    if "--bless" in argv or not LEDGER.exists():
        verb = "re-blessed" if LEDGER.exists() else "created"
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(
            json.dumps({"files": now}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"ledger {verb}: {LEDGER.relative_to(VAULT).as_posix()} "
              f"({len(now)} files)")
        return 0

    old = json.loads(LEDGER.read_text(encoding="utf-8")).get("files", {})
    bad: list[str] = []
    changed: list[str] = []

    print("=" * 78)
    for rel, meta in sorted(now.items()):
        if rel not in old:
            print(f"  new        {rel}  ({meta['bytes']}B)")
            continue
        was = old[rel]
        if meta["bytes"] == 0:
            bad.append(rel)
            print(f"  EMPTY      {rel}  (was {was['bytes']}B)")
        elif meta["bytes"] < was["bytes"]:
            bad.append(rel)
            print(f"  SHRUNK     {rel}  {was['bytes']}B -> {meta['bytes']}B "
                  f"({meta['bytes'] - was['bytes']:+d})")
        elif meta["sha256"] != was["sha256"]:
            changed.append(rel)
            print(f"  changed    {rel}  {was['bytes']}B -> {meta['bytes']}B")
    for rel in sorted(set(old) - set(now)):
        bad.append(rel)
        print(f"  MISSING    {rel}  (was {old[rel]['bytes']}B)")
    print("=" * 78)

    if changed and not bad:
        print(f"{len(changed)} file(s) changed — expected for contract edits. "
              f"Re-bless with --bless once you have reviewed them.")
    if bad:
        print(f"!! {len(bad)} file(s) DAMAGED: {', '.join(bad)}")
        print("   Inspect before doing anything else. Check the Obsidian vault "
              "for a .bak, or reconstruct from context.")
    else:
        print("no damage detected")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
