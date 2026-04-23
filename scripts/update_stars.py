#!/usr/bin/env python3
"""
update_stars.py — Refresh star counts for all repos in data/repos.json,
discover new harness engineering repos, and regenerate README.md.

Usage:
    python scripts/update_stars.py

Requires:
    pip install requests

Environment:
    GITHUB_TOKEN   GitHub personal access token (recommended — raises rate
                   limit from 60 to 5,000 req/hr; search API 30 req/min)
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parent.parent
DATA_FILE   = REPO_ROOT / "data" / "repos.json"
README_FILE = REPO_ROOT / "README.md"

# ── GitHub API ────────────────────────────────────────────────────────────────
GITHUB_API  = "https://api.github.com/repos/{}"
HEADERS: dict[str, str] = {"Accept": "application/vnd.github+json"}
if token := os.getenv("GITHUB_TOKEN"):
    HEADERS["Authorization"] = f"Bearer {token}"

SLEEP_BETWEEN = 0.5   # seconds between REST requests
SEARCH_SLEEP  = 7     # seconds between Search API calls (≤30/min with token)
MIN_STARS     = 10    # minimum stars to keep / add a repo

# ── Search queries: category → list[query_string] ─────────────────────────────
SEARCH_QUERIES: dict[str, list[str]] = {
    "\U0001f916 Agent Harness Frameworks": [
        "topic:harness-engineering stars:>50",
        "topic:agent-harness stars:>50",
        '"agent harness" framework stars:>100',
        '"harness engineering" agent framework stars:>50',
        '"multi-agent harness" stars:>50',
    ],
    "\U0001f680 CI/CD & DevOps Platforms": [
        "topic:harness stars:>100 ci",
        "harness ci cd in:name stars:>50",
        "harness devops in:name stars:>50",
    ],
    "\U0001f4ca LLM Evaluation Harnesses": [
        "topic:evaluation-framework language-model stars:>100",
        "evaluation-harness in:name stars:>50",
        '"lm evaluation harness" stars:>100',
        '"evaluation harness" llm stars:>50',
        "benchmark harness llm in:description stars:>50",
    ],
    "\U0001f4da Harness Engineering Guides & Learning": [
        "harness-engineering in:name stars:>20",
        '"harness engineering" tutorial stars:>20',
        '"harness engineering" guide stars:>20',
        "learn harness engineering in:name stars:>20",
    ],
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits": [
        "topic:agent-memory harness stars:>30",
        "topic:agent-skills harness stars:>30",
        '"context harness" agent stars:>20',
        "agent skills memory harness in:description stars:>20",
    ],
    "\u26a1 Benchmarking & Testing Harnesses": [
        "topic:test-harness stars:>50",
        "test-harness in:name stars:>50",
        "benchmark-harness in:name stars:>30",
        '"test harness" framework stars:>100',
    ],
    "\U0001f510 Security & Fuzzing Harnesses": [
        "topic:fuzzing harness stars:>50",
        "fuzzing harness in:name stars:>50",
        "fuzz harness in:name stars:>30",
    ],
    "\U0001f4cb Awesome & Curated Lists": [
        "awesome-harness in:name stars:>30",
        "awesome harness engineering in:name stars:>10",
    ],
    "\U0001f527 Miscellaneous Harness Projects": [
        "harness in:name stars:>200",
        '"agent harness" in:description stars:>100',
    ],
}

# ── Category config ───────────────────────────────────────────────────────────
CATEGORY_ORDER = [
    "\U0001f916 Agent Harness Frameworks",
    "\U0001f680 CI/CD & DevOps Platforms",
    "\U0001f4ca LLM Evaluation Harnesses",
    "\U0001f4da Harness Engineering Guides & Learning",
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits",
    "\u26a1 Benchmarking & Testing Harnesses",
    "\U0001f510 Security & Fuzzing Harnesses",
    "\U0001f4cb Awesome & Curated Lists",
    "\U0001f527 Miscellaneous Harness Projects",
]

CAT_DESC = {
    "\U0001f916 Agent Harness Frameworks":
        "Core frameworks for building, running, and orchestrating AI agents with harness engineering principles — feedback loops, context management, and production-grade scaffolding.",
    "\U0001f680 CI/CD & DevOps Platforms":
        "Industrial-grade CI/CD platforms and DevOps tools with harness branding or harness-first architectures.",
    "\U0001f4ca LLM Evaluation Harnesses":
        "Frameworks and tools for evaluating LLMs, agents, and AI systems at scale — benchmarking, grading, and leaderboards.",
    "\U0001f4da Harness Engineering Guides & Learning":
        "Tutorials, guides, playbooks, and educational resources on harness engineering principles and practices.",
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits":
        "Libraries and toolkits for managing agent memory, skills, context, and execution environments.",
    "\u26a1 Benchmarking & Testing Harnesses":
        "Test frameworks, benchmark harnesses, and quality assurance tools for software and AI systems.",
    "\U0001f510 Security & Fuzzing Harnesses":
        "Security research tools, fuzzing harnesses, and vulnerability research frameworks.",
    "\U0001f4cb Awesome & Curated Lists":
        "Curated awesome lists focused on harness engineering resources, tools, and projects.",
    "\U0001f527 Miscellaneous Harness Projects":
        "Other notable harness-related projects spanning specialized runtimes, domain-specific tools, and unique applications.",
}

TOC_ANCHORS = {
    "\U0001f916 Agent Harness Frameworks":              "agent-harness-frameworks",
    "\U0001f680 CI/CD & DevOps Platforms":              "cicd--devops-platforms",
    "\U0001f4ca LLM Evaluation Harnesses":              "llm-evaluation-harnesses",
    "\U0001f4da Harness Engineering Guides & Learning": "harness-engineering-guides--learning",
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits": "skills-memory--context-toolkits",
    "\u26a1 Benchmarking & Testing Harnesses":          "benchmarking--testing-harnesses",
    "\U0001f510 Security & Fuzzing Harnesses":          "security--fuzzing-harnesses",
    "\U0001f4cb Awesome & Curated Lists":               "awesome--curated-lists",
    "\U0001f527 Miscellaneous Harness Projects":        "miscellaneous-harness-projects",
}


# ── GitHub Search helpers ──────────────────────────────────────────────────────
def search_github(query: str) -> list[dict]:
    """Search GitHub repositories matching *query*. Returns raw API items."""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": 100}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if resp.status_code == 200:
            return resp.json().get("items", [])
        if resp.status_code == 403:
            reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait  = max(reset - int(time.time()), 0) + 5
            print(f"  [403] Rate-limited. Waiting {wait}s …")
            time.sleep(wait)
            resp2 = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp2.status_code == 200:
                return resp2.json().get("items", [])
        print(f"  [Search {resp.status_code}] {query!r}")
    except requests.RequestException as exc:
        print(f"  [Search ERR] {query!r}: {exc}")
    return []


def normalize_repo(gh_item: dict, category: str) -> dict:
    """Convert a GitHub Search API result item to the internal schema."""
    return {
        "name":        gh_item["full_name"],
        "url":         gh_item["html_url"],
        "description": gh_item.get("description") or "",
        "stars":       gh_item["stargazers_count"],
        "forks":       gh_item.get("forks_count", 0),
        "language":    gh_item.get("language") or "",
        "topics":      ",".join(gh_item.get("topics") or []),
        "updated":     gh_item.get("updated_at", ""),
        "category":    category,
    }


def discover_repos(existing_urls: set[str]) -> list[dict]:
    """
    Run all SEARCH_QUERIES; return newly-discovered repos not in
    *existing_urls* and with stars > MIN_STARS.
    """
    new_repos: list[dict] = []
    for category, queries in SEARCH_QUERIES.items():
        for query in queries:
            print(f"  Searching: {query!r}")
            items = search_github(query)
            for item in items:
                html_url = item.get("html_url", "")
                if html_url in existing_urls:
                    continue
                if item.get("stargazers_count", 0) <= MIN_STARS:
                    continue
                new_repos.append(normalize_repo(item, category))
                existing_urls.add(html_url)
            time.sleep(SEARCH_SLEEP)
    print(f"Discovered {len(new_repos)} new repos")
    return new_repos


# ── Star fetching ──────────────────────────────────────────────────────────────
def fetch_stars(full_name: str) -> int | None:
    """Return current star count for <owner>/<repo>, or None on error."""
    url = GITHUB_API.format(full_name)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("stargazers_count")
        if resp.status_code == 404:
            print(f"  [404] {full_name} — not found (archived/deleted?)")
        elif resp.status_code == 403:
            reset = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait  = max(reset - int(time.time()), 1)
            print(f"  [403] Rate limited. Waiting {wait}s …")
            time.sleep(wait + 1)
            return fetch_stars(full_name)   # retry once
        else:
            print(f"  [{resp.status_code}] {full_name}")
    except requests.RequestException as exc:
        print(f"  [ERR] {full_name}: {exc}")
    return None


# ── README generation ──────────────────────────────────────────────────────────
def fmt_row(r: dict) -> str:
    name  = r["name"]
    url   = r["url"]
    stars = r["stars"]
    lang  = f"`{r['language']}`" if r.get("language") else ""
    desc  = (r.get("description") or "").replace("|", "\\|")
    if len(desc) > 120:
        desc = desc[:117] + "..."
    return f"| [**{name}**]({url}) | ⭐ {stars:,} | {lang} | {desc} |"


def generate_readme(repos_by_cat: dict[str, list[dict]], today: str) -> str:
    all_repos = [r for cat in CATEGORY_ORDER for r in repos_by_cat.get(cat, [])]
    total     = len(all_repos)
    num_cats  = sum(1 for cat in CATEGORY_ORDER if repos_by_cat.get(cat))

    lang_counter = Counter(r["language"] for r in all_repos if r.get("language"))
    top_langs    = ", ".join(f"{l}({c})" for l, c in lang_counter.most_common(8))

    # Deduplicated top 10
    seen: set[str] = set()
    top10: list[dict] = []
    for r in sorted(all_repos, key=lambda x: -x["stars"]):
        if r["url"] not in seen:
            seen.add(r["url"])
            top10.append(r)
        if len(top10) == 10:
            break

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    today_badge = today.replace("-", "--")
    lines += [
        '<div align="center">',
        "  <h1>Awesome Harness Engineering</h1>",
        "  <p>A curated list of awesome harness engineering frameworks, libraries, tools, and resources for building reliable AI agent systems.</p>",
        "",
        "  [![Awesome](https://awesome.re/badge.svg)](https://awesome.re)",
        "  ![GitHub stars](https://img.shields.io/github/stars/yenanjing/awesome-harness-engineering?style=flat-square)",
        f"  ![Last Updated](https://img.shields.io/badge/last%20updated-{today_badge}-blue?style=flat-square)",
        "",
        f"  <p>Collected <strong>{total}</strong> repositories across <strong>{num_cats}</strong> categories covering the harness engineering ecosystem.</p>",
        "</div>",
        "",
        "---",
        "",
        "## 📖 Table of Contents",
        "",
        "- [About](#-about)",
        "",
    ]
    for cat in CATEGORY_ORDER:
        if repos_by_cat.get(cat):
            anchor = TOC_ANCHORS.get(cat, "")
            lines.append(f"- [{cat}](#{anchor})")
    lines += [
        "- [📊 Stats](#-stats)",
        "- [⭐ Star History](#-star-history)",
        "- [🤝 Contributing](#-contributing)",
        "",
        "---",
        "",
        "## 🌟 About",
        "",
        "**Harness Engineering** is a discipline focused on designing the environment, constraints, feedback loops, and tooling that allow AI agents to operate reliably, verifiably, and safely in production. It goes beyond writing prompts — it's about shaping the scaffolding, context management, memory systems, evaluation pipelines, and guardrails that keep AI systems on track.",
        "",
        "This list curates the best open-source projects in the harness engineering ecosystem:",
        "",
        "- 🤖 **Agent harness frameworks** — multi-agent orchestration, skill systems, and production runtimes",
        "- 🚀 **CI/CD & DevOps platforms** — harness-first build and deployment pipelines",
        "- 📊 **LLM evaluation harnesses** — benchmarking and grading frameworks",
        "- 📚 **Guides & learning resources** — tutorials, playbooks, and educational content",
        "- 🛠️ **Skills, memory & context toolkits** — context management and long-term memory",
        "- ⚡ **Test & benchmark harnesses** — quality assurance and performance frameworks",
        "- 🔐 **Security & fuzzing harnesses** — vulnerability research and chaos engineering",
        "",
        f"> Last updated: {today}",
        "",
        "---",
        "",
    ]

    # ── Category sections ─────────────────────────────────────────────────────
    for cat in CATEGORY_ORDER:
        repos = repos_by_cat.get(cat, [])
        if not repos:
            continue
        desc = CAT_DESC.get(cat, "")
        lines += [
            f"## {cat}",
            "",
            f"> {desc}",
            "",
            "| Repository | Stars | Language | Description |",
            "|-----------|-------|----------|-------------|",
        ]
        for r in repos:
            lines.append(fmt_row(r))
        lines += ["", "---", ""]

    # ── Stats ─────────────────────────────────────────────────────────────────
    lines += [
        "## 📊 Stats",
        "",
        f"- **Total repositories**: {total}",
        f"- **Categories**: {num_cats}",
        f"- **Top languages**: {top_langs}",
        f"- **Last updated**: {today}",
        "",
        "### 🏆 Top 10 by Stars",
        "",
        "| Rank | Repository | Stars | Description |",
        "|------|-----------|-------|-------------|",
    ]
    for i, r in enumerate(top10, 1):
        desc = (r.get("description") or "").replace("|", "\\|")
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"| {i} | [{r['name']}]({r['url']}) | ⭐ {r['stars']:,} | {desc} |")

    lines += [
        "",
        "---",
        "",
        "## ⭐ Star History",
        "",
        "[![Star History Chart](https://api.star-history.com/svg?repos=yenanjing/awesome-harness-engineering&type=Date)](https://star-history.com/#yenanjing/awesome-harness-engineering&Date)",
        "",
        "---",
        "",
        "## 🤝 Contributing",
        "",
        "Contributions are welcome! Please read the [contribution guidelines](CONTRIBUTING.md) first.",
        "",
        "To add a project:",
        "1. Fork this repository",
        "2. Add your project to the relevant section",
        "3. Ensure it has 1,000+ stars (or is highly relevant with 100+ stars) and is actively maintained",
        "4. Submit a Pull Request",
        "",
        "---",
        "",
        "## 📄 License",
        "",
        "[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](https://creativecommons.org/publicdomain/zero/1.0/)",
        "",
        "This list is under the [CC0 1.0](LICENSE) license.",
        "",
        "---",
        "",
        '<div align="center">',
        '  <sub>Generated with ❤️ using <a href="https://claude.ai/claude-code">Claude Code</a> | Auto-updated via GitHub Actions</sub>',
        "</div>",
    ]
    return "\n".join(lines) + "\n"


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading {DATA_FILE}")
    with DATA_FILE.open() as f:
        repos: list[dict] = json.load(f)

    total = len(repos)
    print(f"  {total} repos to refresh")

    updated = skipped = errors = 0
    for i, repo in enumerate(repos, 1):
        full_name = repo["name"]
        new_stars = fetch_stars(full_name)
        if new_stars is None:
            errors += 1
        elif new_stars != repo["stars"]:
            print(f"  [{i}/{total}] {full_name}: {repo['stars']:,} → {new_stars:,}")
            repo["stars"] = new_stars
            updated += 1
        else:
            skipped += 1
        time.sleep(SLEEP_BETWEEN)

    print(f"\nRefresh done: {updated} updated, {skipped} unchanged, {errors} errors")

    # Drop repos below threshold
    before = len(repos)
    repos = [r for r in repos if r.get("stars", 0) > MIN_STARS]
    print(f"Dropped {before - len(repos)} repos with ≤{MIN_STARS} stars ({len(repos)} remain)")

    # Discover new repos
    print("\n--- Discovering new repos ---")
    existing_urls: set[str] = {r["url"] for r in repos}
    new_repos = discover_repos(existing_urls)
    repos.extend(new_repos)
    print(f"Total repos after discovery: {len(repos)}")

    # Save data
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w") as f:
        json.dump(repos, f, indent=2, ensure_ascii=False)
    print(f"Saved {DATA_FILE}")

    # Group by category and sort
    repos_by_cat: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_ORDER}
    for repo in repos:
        cat = repo.get("category", "")
        if cat in repos_by_cat and repo["stars"] > MIN_STARS:
            repos_by_cat[cat].append(repo)
    for cat in repos_by_cat:
        repos_by_cat[cat].sort(key=lambda x: -x["stars"])

    today = date.today().isoformat()
    readme = generate_readme(repos_by_cat, today)
    README_FILE.write_text(readme, encoding="utf-8")
    print(f"Saved {README_FILE}")


if __name__ == "__main__":
    main()
