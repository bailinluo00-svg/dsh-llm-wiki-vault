"""Snapshot the vault into git — the recovery path for the contract files.

Why git (incident 2026-09-21, 更新公告 #15): a shell write truncated AGENTS.md
to 0 bytes. There was no git repo, no .obsidian/file-recovery, no .bak anywhere.
It was recoverable ONLY because the full text happened to still be in the agent's
context. `tools/check_integrity.py` can now DETECT damage; git is what makes it
RECOVERABLE. Detection without recovery is not a safety net.

Usage:
    python tools/snapshot.py                      # commit with a date-stamped message
    python tools/snapshot.py "message"            # commit with your own message
    python tools/snapshot.py --status             # what changed (no commit)
    python tools/snapshot.py --log                # recent snapshots
    python tools/snapshot.py --restore <path>     # restore ONE file from HEAD

Exit codes: 0 ok / 1 failure. Nothing to commit is reported as ok, not an error.

Deliberately NOT a daemon and NOT a hook: the trigger is the session wrap-up
checklist in AGENTS.md section 8, so every change has a human-readable message
attached to it. An auto-committing watcher would produce a history nobody can read.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

VAULT = Path(r"<你的仓库>")


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(VAULT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=check,
    )


def is_repo() -> bool:
    r = git("rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def main(argv: list[str]) -> int:
    if not is_repo():
        print("not a git repository yet. one-time setup:\n")
        print(f'  git -C "{VAULT}" init -b main')
        print(f'  git -C "{VAULT}" config user.name  "ObsidianVault Agent"')
        print(f'  git -C "{VAULT}" config user.email "agent@localhost"')
        print(f'  git -C "{VAULT}" add -A')
        print(f'  git -C "{VAULT}" commit -m "baseline"')
        return 1

    if "--status" in argv:
        print(git("status", "--short").stdout or "(clean)")
        return 0

    if "--log" in argv:
        print(git("log", "--oneline", "-15").stdout or "(no commits yet)")
        return 0

    if "--restore" in argv:
        i = argv.index("--restore")
        if i + 1 >= len(argv):
            print("usage: --restore <path-relative-to-vault>")
            return 1
        target = argv[i + 1]
        r = git("checkout", "HEAD", "--", target)
        if r.returncode != 0:
            print(f"restore FAILED: {r.stderr.strip()}")
            return 1
        print(f"restored from HEAD: {target}")
        print("NOTE: re-run tools/check_integrity.py --bless afterwards, since the "
              "restored bytes may differ from the current baseline.")
        return 0

    msg = argv[0] if argv else f"snapshot {date.today().isoformat()}"

    git("add", "-A")
    staged = git("diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        print("nothing to snapshot (working tree matches HEAD)")
        return 0

    print("staged:")
    for line in staged.split("\n"):
        print(f"  {line}")

    r = git("commit", "-m", msg)
    if r.returncode != 0:
        out = (r.stdout + r.stderr).strip()
        print(f"\ncommit FAILED:\n{out}")
        if "user.email" in out or "empty ident" in out:
            print(f'\nfix: git -C "{VAULT}" config user.email "agent@localhost"')
        return 1

    head = git("log", "-1", "--oneline").stdout.strip()
    n = git("rev-list", "--count", "HEAD").stdout.strip()
    print(f"\ncommitted: {head}   (total {n} snapshots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
