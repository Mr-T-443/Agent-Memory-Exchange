"""Verify Foundry IQ integration by querying the index and local database."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from amx.config import AMXConfig
from amx.memory.retrieval import search_memory
from amx.store import Store


def direct_query(cfg: AMXConfig, query: str) -> int:
    url = (
        f"{cfg.foundry_endpoint.rstrip('/')}/indexes/{cfg.foundry_index}"
        f"/docs/search?api-version=2024-07-01"
    )
    payload = json.dumps({"search": query, "top": 5}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "api-key": cfg.foundry_api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  AZURE ERROR: HTTP {e.code} — {e.read().decode('utf-8', 'replace')[:300]}")
        return 0
    except urllib.error.URLError as e:
        print(f"  NETWORK ERROR: {e.reason}")
        return 0

    docs = data.get("value", [])
    for d in docs:
        print(f"  score={d.get('@search.score', 0):.3f}  {d.get('title', '?')}")
    if not docs:
        print(f"  Azure returned 0 documents containing '{query}'.")
    return len(docs)


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "memory"
    cfg = AMXConfig()
    print(f"foundry configured: {cfg.foundry_configured}")
    if not cfg.foundry_configured:
        print("Set the three AMX_FOUNDRY_IQ_* vars (env or ~/.amx/.env) and retry.")
        return

    print(f"\n1) direct Azure query for '{query}':")
    direct_query(cfg, query)

    store = Store(cfg.db_path)
    row = store._conn.execute("SELECT DISTINCT project_id FROM records LIMIT 1").fetchone()
    if not row:
        print("\nNo local records to run the merged search against.")
        store.close()
        return

    print(f"\n2) merged AMX search for '{query}':")
    result = search_memory(store, row["project_id"], query, limit=10, cfg=cfg)
    store.close()
    for m in result.matches:
        print(f"  [{m.source:<10}] score={m.score:.3f}  {m.title}")

    if any(m.source == "foundry_iq" for m in result.matches):
        print("\nOK — grounded Foundry IQ results are merging with local search.")
    else:
        print("\nNo foundry_iq rows in the merged list — compare with step 1 above:")
        print("if step 1 found docs, pick a query word that appears in them.")


if __name__ == "__main__":
    main()
