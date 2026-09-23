#!/usr/bin/env python3
import os
import sys
import json
import re
from pathlib import Path

QUEUE_FILE = "/Users/toannguyen/chototcaodulieu/tasks_queue.json"
DOCS_DIR = "timoto-space"
MINT_JSON_PATH = os.path.join(DOCS_DIR, "mint.json")

REPOS = [
    "timoto-internal-app",
    "chototcaodulieu",
    "TiMoto-AI-Web",
    "timoto-dealer-portal",
    "timoto_dealer_portal_v2",
    "timoto-mobile"
]

def init_queue():
    print("[*] Initializing clean module task queue across all 6 repositories...")
    tasks = []
    seen_ids = set()
    existing_by_id = {}
    if os.path.exists(QUEUE_FILE):
        try:
            for old in json.loads(Path(QUEUE_FILE).read_text(encoding="utf-8")):
                existing_by_id[old["id"]] = old
        except (json.JSONDecodeError, OSError, KeyError):
            existing_by_id = {}

    skip_parts = [
        "node_modules", ".git", "dist", "build", "test", "__pycache__",
        "worktrees", ".claude", "graphify-out", "venv", "site-packages",
        ".build",
    ]
    patterns = [
        "**/controllers/**/*.*",
        "**/routes/**/*.*",
        "**/services/**/*.*",
        "**/crawlers/**/*.*",
        "**/pipelines/**/*.*",
        "**/models.py",
        "**/models/*.py",
        "**/views.py",
        "**/views/*.py",
        "**/handler.mjs",
        "**/*_api.py",
        "**/instant_sell.py",
        "**/payouts.py",
        "**/chotot.py",
        "**/lambda_handler.py",
        "**/refresh_handler.py",
    ]

    for repo in REPOS:
        repo_path = Path(f"/Users/toannguyen/{repo}")
        if not repo_path.exists():
            continue

        found_files = []
        for p in patterns:
            found_files.extend(list(repo_path.glob(p)))

        filtered = [
            str(f) for f in found_files
            if not any(x in str(f) for x in skip_parts)
        ]

        for file_path in filtered:
            rel_path = os.path.relpath(file_path, f"/Users/toannguyen/{repo}")
            sanitized_rel = rel_path.replace("/", "__").replace(".", "_")
            task_id = f"{repo}__{sanitized_rel}"

            if task_id in seen_ids:
                continue
            seen_ids.add(task_id)

            output_mdx = f"{repo}/{rel_path.replace('/', '_').replace('.py', '.mdx').replace('.ts', '.mdx').replace('.tsx', '.mdx').replace('.mjs', '.mdx')}"
            prior = existing_by_id.get(task_id, {})
            tasks.append({
                "id": task_id,
                "repo": repo,
                "target_file": file_path,
                "relative_path": rel_path,
                "output_mdx": output_mdx,
                "status": prior.get("status", "PENDING"),
                "word_count": prior.get("word_count", 0),
            })

    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    print(f"[✓] Created clean queue with {len(tasks)} distinct tasks in {QUEUE_FILE}")
    return tasks

def quality_gate(mdx_path):
    if not os.path.exists(mdx_path):
        return False, "MDX file was not generated"
        
    with open(mdx_path, "r", encoding="utf-8") as f:
        content = f.read()

    words = len(content.split())
    
    errors = []
    if words < 600:
        errors.append(f"Content too short ({words} words, required > 600 words)")
    if "```mermaid" not in content:
        errors.append("Missing Sequence or State Machine Mermaid Diagram")
    if ("| Column |" not in content and "| Field |" not in content and "CREATE TABLE" not in content and "| HTTP Code |" not in content and "| Status Code |" not in content):
        errors.append("Missing DB Schema / DDL Table / Error Catalog Matrix")
    if "// ... rest of code" in content or "// ... rest" in content or "TODO" in content:
        errors.append("Contains lazy placeholder (// ... rest of code or TODO)")
        
    if errors:
        return False, "; ".join(errors)
    return True, words

def main():
    tasks = init_queue()
    completed_count = sum(1 for t in tasks if t["status"] == "COMPLETED")
    pending_count = len(tasks) - completed_count
    
    print(f"\n=======================================================")
    print(f"[*] Deterministic Orchestrator Status:")
    print(f"[*] Total Deduplicated Tasks: {len(tasks)} | Completed: {completed_count} | Pending: {pending_count}")
    print(f"=======================================================\n")

if __name__ == "__main__":
    main()
