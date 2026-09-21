"""Independent check of the karpathywiki plugin's LLM readiness.

Mirrors the plugin's own predicate (main.js:54824 isProviderConfigured) and its
probeLlm() failure branches (main.js:81347), reading the same data.json the
plugin reads. It never contacts the network and never prints secret values.

Writes an artifact row to .plugin-readiness.json so a later run can detect that
the plugin rewrote wiki/index.md, log.md, or dropped files into its namespaces.

Usage:
    python tools/check_plugin_ready.py           # check + snapshot
    python tools/check_plugin_ready.py --diff     # check + compare with snapshot
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

VAULT = Path(r"<你的仓库>")
DATA_JSON = VAULT / ".obsidian" / "plugins" / "karpathywiki" / "data.json"
SNAPSHOT = VAULT / "tools" / ".plugin-readiness.json"
PLUGIN_DIRS = ("entities", "concepts", "sources", "schema", "contradictions")

# Providers that need no API key (mirrors isLocalNoKeyProvider).
NO_KEY_PROVIDERS = {"ollama", "lmstudio", "custom-openai", "llamacpp", "local"}


def has_secret_reference(settings: dict) -> bool:
    """The plugin treats a non-empty secretId as 'a key may be stored there'."""
    return bool((settings.get("providerApiKeySecretId") or "").strip())


def is_configured(s: dict) -> tuple[bool, list[str]]:
    """Reimplementation of isProviderConfigured + probeLlm's error branches."""
    notes: list[str] = []
    if not (s.get("model") or "").strip():
        return False, ["probeLlm: 'Model not selected.' — model is empty"]
    notes.append(f"model selected: {s['model']}")

    provider = (s.get("provider") or "").strip()
    if provider == "openai-codex":
        return bool(s.get("openAICodexSecretId")), notes + ["codex credential path"]
    if provider.startswith("bedrock-") and s.get("bedrockAuthMethod") in {"sso", "iam"}:
        return True, notes + ["bedrock credential path"]

    if provider in NO_KEY_PROVIDERS:
        notes.append(f"provider '{provider}' needs no API key")
        return True, notes

    plain = (s.get("apiKey") or "").strip()
    if plain:
        notes.append("API key present in data.json (plaintext field)")
        return True, notes
    if has_secret_reference(s):
        notes.append(
            "apiKey field empty + secretId set -> key lives in Obsidian SecretStorage; "
            "cannot be read from disk (by design)"
        )
        return True, notes
    return False, notes + ["probeLlm: 'API key not configured.'"]


def census() -> dict:
    wiki = VAULT / "wiki"
    topics = sorted(
        d.name for d in wiki.iterdir()
        if d.is_dir() and d.name not in PLUGIN_DIRS and not d.name.startswith(".")
    )
    return {
        "topic_dirs": topics,
        "topic_pages": sum(len(list((wiki / t).glob("*.md"))) for t in topics),
        "raw_pages": len(list((VAULT / "raw").rglob("*.md"))),
        "plugin_ns_files": {
            d: sorted(p.name for p in (wiki / d).rglob("*.md"))
            for d in PLUGIN_DIRS if (wiki / d).exists()
        },
        "wiki_index_exists": (wiki / "index.md").exists(),
        "log_bytes": (VAULT / "log.md").stat().st_size if (VAULT / "log.md").exists() else 0,
        "root_index_bytes": (VAULT / "index.md").stat().st_size if (VAULT / "index.md").exists() else 0,
    }


def main(argv: list[str]) -> int:
    if not DATA_JSON.exists():
        print(f"MISSING {DATA_JSON} — plugin has never saved settings")
        return 2
    settings = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    ok, notes = is_configured(settings)
    print("=== 插件 LLM 就绪判定（按 main.js 的逻辑复算）===")
    print(f"provider        : {settings.get('provider')}")
    print(f"model           : '{settings.get('model')}'")
    print(f"baseUrl         : {settings.get('baseUrl')}")
    print(f"availableModels : {settings.get('availableModels')}")
    print(f"llmReady (插件写入的缓存值): {settings.get('llmReady')}")
    print("\n-- 判定过程 --")
    for n in notes:
        print(f"  · {n}")
    print("\n" + "=" * 52)
    print(f"结论: {'[OK] 已就绪' if ok else '[NO] 未就绪'}")
    if ok and settings.get("llmReady") is not True:
        print("注意：判定通过但 llmReady 缓存为 false —— 重启 Obsidian 后插件会重新推导该值。")

    current = census()
    print("\n=== 仓库结构快照 ===")
    for key, value in current.items():
        print(f"  {key}: {value}")

    if "--diff" in argv and SNAPSHOT.exists():
        previous = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        print("\n=== 与上次快照对比（插件是否动过东西）===")
        drifted = False
        for key in current:
            if previous.get(key) != current[key]:
                drifted = True
                print(f"  CHANGED {key}: {previous.get(key)} -> {current[key]}")
        print("  （无变化）" if not drifted else "  ⚠️ 有变化，逐项核对上面各项")
    else:
        SNAPSHOT.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n快照已写入 {SNAPSHOT.relative_to(VAULT)}（下次加 --diff 可对比）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
