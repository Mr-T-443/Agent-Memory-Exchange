"""Stress test to generate and query mock data across many projects."""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Mock data vocabularies.
VERBS = ["Refactor", "Optimize", "Migrate", "Debug", "Fix", "Implement", "Deploy", "Design", "Review", "Audit", "Containerize", "Benchmark", "Document", "Scale", "Secure"]
ADJECTIVES = ["asynchronous", "distributed", "legacy", "hexagonal", "reactive", "thread-safe", "stateless", "cloud-native", "serverless", "federated", "high-performance", "isolated", "fault-tolerant"]
NOUNS = ["router", "database connection pool", "authentication middleware", "caching layer", "message broker", "garbage collector", "API gateway", "retry queue", "indexing service", "event bus", "session store", "rate limiter"]
REASONS = [
    "due to high latency under load",
    "to prevent memory leaks on Windows",
    "to improve CPU utilization",
    "for compliance and auditing requirements",
    "to support cross-region failover",
    "because of high-concurrency race conditions",
    "to simplify unit testing setup",
    "to reduce token usage in context bundles",
    "to support local-first indexing"
]

RECORD_TYPES = ["task", "bug", "research", "architecture", "entity", "thread", "raw_event"]

DECISION_TEMPLATES = [
    ("Use SQLite for metadata", "Local-first storage runs in-process with minimal memory footprint, and WAL mode provides safe concurrent reads."),
    ("Adopt BM25 for ranking", "Semantic embeddings are too slow and resource-heavy for CLI execution. BM25 search via FTS5 is lightweight and sufficient."),
    ("Budget context to 3k tokens", "Dumping full history wastes tokens and degrades LLM recall. A 3k budget ensures only high-relevance items are injected."),
    ("Throttle Foundry IQ calls", "API rate limits degrade responsiveness. Caching results locally ensures the UI remains fast."),
    ("Redact secrets before ingest", "Avoid storing private keys or tokens in long-term SQLite database. Scrub any matching regex patterns on ingest."),
    ("Merge project aliases on git match", "If a git remote match is found, the system should automatically prompt to alias the project identities to avoid duplication.")
]

SUMMARY_TEMPLATES = [
    "Integrating the new hybrid indexing system. Core SQLite migrations are complete. FTS5 FTS index rebuild is working. Next step is testing BM25 weight adjustments.",
    "Refactoring MCP tools interface to add new lifecycle fields. Backward compatibility is verified against CLI clients. Stale sessions are purged automatically.",
    "Addressing token bloat issues. Reduced default budget to 1500 tokens. Implemented decision deduplication so overridden items don't double-budget."
]

STATE_TEMPLATES = [
    {"current_goal": "Optimize search ranking", "active_task": "Fine-tune BM25 weights", "open_issues": ["Low score on cross-entity queries"]},
    {"current_goal": "Add OAuth support", "active_task": "Implement token refresh flow", "open_issues": ["Redirect URI mismatch on local dev"]},
    {"current_goal": "Migrate DB schema", "active_task": "Write migration v4 script", "open_issues": ["Index rebuild is slow on large stores"]},
    {"current_goal": "Improve client adoption", "active_task": "Generate rules files automatically", "open_issues": ["Cursor IDE custom rules path varies"]}
]


def make_title() -> str:
    return f"{random.choice(VERBS)} {random.choice(ADJECTIVES)} {random.choice(NOUNS)}"


def make_body() -> str:
    return f"Need to {make_title().lower()} {random.choice(REASONS)}. This will require updating the configuration, verifying fallback behaviors, and auditing existing benchmarks."


class ToolError(Exception):
    pass


def parse(res):
    if getattr(res, "isError", False):
        raise ToolError(res.content[0].text if res.content else "error")
    if res.content and getattr(res.content[0], "text", None) is not None:
        try:
            return json.loads(res.content[0].text)
        except Exception:
            return res.content[0].text
    return None


async def call(s: ClientSession, name: str, **args):
    return parse(await s.call_tool(name, args))


async def stress_test(db_path: str):
    print(f"Starting AMX MCP stress test...")
    print(f"Target Database Path: {db_path}")
    
    # Configure stdio launcher for the MCP server.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "amx.mcp.server"],
        env={**os.environ, "AMX_DB_PATH": db_path, "PYTHONIOENCODING": "utf-8"},
    )
    
    start_time = time.time()
    total_calls = 0
    total_records = 0
    total_decisions = 0
    total_summaries = 0
    total_states = 0
    
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("MCP Server connected and initialized successfully.\n")
            
            # Onboard client.
            print("Initializing client adoption...")
            await call(s, "amx_init", client="stress-tester", applied=True)
            total_calls += 1
            
            # Populate mock projects.
            num_projects = 100
            for i in range(1, num_projects + 1):
                proj_name = f"StressProject-{i:03d}"
                proj_id = f"name-stressproject-{i:03d}"
                
                # Ingest memory records.
                num_records = random.randint(10, 20)
                for r_idx in range(num_records):
                    rtype = random.choice(RECORD_TYPES)
                    title = make_title()
                    body = make_body()
                    
                    # Add mock entities.
                    entities = [random.choice(NOUNS) for _ in range(random.randint(0, 3))]
                    
                    await call(
                        s, 
                        "memory_ingest", 
                        type=rtype, 
                        title=title, 
                        body=body, 
                        entities=entities, 
                        project_id=proj_id, 
                        project_name=proj_name
                    )
                    total_records += 1
                    total_calls += 1
                
                # Record decisions.
                num_decisions = random.randint(2, 5)
                selected_decisions = random.sample(DECISION_TEMPLATES, num_decisions)
                for d_title, d_rationale in selected_decisions:
                    await call(
                        s, 
                        "memory_record_decision", 
                        project_id=proj_id, 
                        title=d_title, 
                        rationale=d_rationale
                    )
                    total_decisions += 1
                    total_calls += 1
                
                # Submit summaries.
                num_summaries = random.randint(1, 3)
                for _ in range(num_summaries):
                    await call(
                        s, 
                        "memory_submit_summary", 
                        project_id=proj_id, 
                        body=random.choice(SUMMARY_TEMPLATES)
                    )
                    total_summaries += 1
                    total_calls += 1
                
                # Update state.
                await call(
                    s, 
                    "memory_update_project_state", 
                    project_id=proj_id, 
                    patch=random.choice(STATE_TEMPLATES)
                )
                total_states += 1
                total_calls += 1
                
                # Report progress.
                if i % 10 == 0 or i == num_projects:
                    elapsed = time.time() - start_time
                    rate = total_calls / elapsed if elapsed > 0 else 0
                    print(
                        f"  Processed {i}/{num_projects} projects... "
                        f"({total_records} records, {total_decisions} decisions, "
                        f"{total_summaries} summaries, {total_states} states) "
                        f"Elapsed: {elapsed:.1f}s, Rate: {rate:.1f} calls/s"
                    )
            
            # Verify search functionality.
            print("\nRunning post-generation semantic search verification...")
            search_start = time.time()
            for _ in range(10):
                query = random.choice(NOUNS)
                proj_id = f"name-stressproject-{random.randint(1, num_projects):03d}"
                res = await call(s, "memory_search", query=query, project_id=proj_id)
                total_calls += 1
            search_elapsed = time.time() - search_start
            print(f"Completed 10 search queries in {search_elapsed:.3f}s (Average: {search_elapsed/10:.3f}s per search)")
            
            # Verify context bundle retrieval.
            print("Retrieving context bundle for a random project...")
            proj_id = f"name-stressproject-{random.randint(1, num_projects):03d}"
            bundle = await call(s, "memory_get_context_bundle", project_id=proj_id, budget_tokens=1500)
            total_calls += 1
            print(f"Bundle retrieved successfully: used_tokens={bundle['used_tokens']}, slices_count={len(bundle['slices'])}")

    duration = time.time() - start_time
    db_size_bytes = Path(db_path).stat().st_size
    db_size_mb = db_size_bytes / (1024 * 1024)
    
    print("\n" + "="*40)
    print("           STRESS TEST RESULTS")
    print("="*40)
    print(f"Total time:          {duration:.2f} seconds")
    print(f"Total MCP calls:     {total_calls}")
    print(f"Throughput rate:     {total_calls / duration:.2f} calls/sec")
    print(f"Projects created:    {num_projects}")
    print(f"Records ingested:    {total_records}")
    print(f"Decisions recorded:  {total_decisions}")
    print(f"Summaries submitted: {total_summaries}")
    print(f"States updated:      {total_states}")
    print(f"Database file size:  {db_size_mb:.2f} MB ({db_size_bytes:,} bytes)")
    print("="*40)


def main():
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        # Default to a temporary database file.
        workspace_dir = Path(__file__).resolve().parent.parent
        temp_dir = workspace_dir / "scratch"
        temp_dir.mkdir(exist_ok=True)
        db_path = str(temp_dir / "stress_test_amx.db")
        
    try:
        asyncio.run(stress_test(db_path))
    except KeyboardInterrupt:
        print("\nStress test interrupted by user.")
        sys.exit(1)


if __name__ == "__main__":
    main()
