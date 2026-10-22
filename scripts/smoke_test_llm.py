"""Smoke test for the local Claude CLI insight analyzer.

Run this from a normal terminal (NOT from inside a Claude Code session,
where a nested `claude` call hangs):

    python3 scripts/smoke_test_llm.py            # analyze 3 items from today
    python3 scripts/smoke_test_llm.py 2026-06-10 5

It does NOT touch your data files: it only reads raw_news.json and prints
the insights Claude generates for a small sample.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# Allow running as `python3 scripts/smoke_test_llm.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
from src.storage.json_storage import raw_news_path


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    if not is_available():
        print("ERRORE: il binario `claude` non è nel PATH.")
        return 1

    path = raw_news_path(date=date.fromisoformat(day))
    if not path.exists():
        print(f"ERRORE: raw news non trovato: {path}")
        print("Lancia prima `python3 -m src.main collect`.")
        return 1

    raw = json.load(path.open(encoding="utf-8"))
    batch = [item for item in raw if str(item.get("url", "")).strip()][:sample]
    print(f"Analizzo {len(batch)} articoli da {path} con Claude CLI...\n")

    analyzer = ClaudeCliAnalyzer(batch_size=sample)
    insights = analyzer.analyze_items(batch)

    if not insights:
        print("Nessun insight generato (vedi i log sopra per l'errore del CLI).")
        return 1

    for item in batch:
        url = str(item.get("url", "")).strip()
        insight = insights.get(url)
        print(f"# {str(item.get('title', ''))[:80]}")
        if not insight:
            print("  (nessun insight per questo item)\n")
            continue
        print(f"  Summary:    {insight.get('summary', '')}")
        print(f"  Why:        {insight.get('why_it_matters', '')}")
        print(f"  Action:     {insight.get('possible_action', '')}")
        print(f"  Relevance:  {insight.get('relevance', '?')}\n")

    print(f"OK: {len(insights)}/{len(batch)} insight generati da Claude CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
