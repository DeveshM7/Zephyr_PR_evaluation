#!/usr/bin/env python3
"""
PR Filtering Script for Zephyr QEMU Pipeline

Finds merged Zephyr PRs suitable for testing with Twister + QEMU.
Searches all merged PRs from Sept 2024 onwards (PR #80000+) across
date ranges, then deep-filters for QEMU compatibility.

Usage:
    python3 find_prs.py              # find 100 candidates (default)
    python3 find_prs.py --count 20   # find fewer candidates for a quick test

Output:
    - Ranked table printed to terminal
    - results/candidate_prs.json written to current directory
"""

import os
import re
import sys
import json
import time
import base64
import argparse
import collections
import threading
import requests
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env file if present
# ---------------------------------------------------------------------------

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN")
REPO            = "zephyrproject-rtos/zephyr"
MAX_CANDIDATES  = 3000   # cap on Phase 1 candidates passed to Phase 2
TIME_LIMIT_SECS = 7200   # hard 2-hour wall-clock limit

# Date ranges covering mid-2024 onwards (v3.7.0+).
# Older PRs hit Kconfig incompatibilities from west module drift.
SEARCH_DATE_RANGES = [
    "2024-06-01..2024-12-31",
    "2025-01-01..2025-06-30",
    "2025-07-01..2025-12-31",
    "2026-01-01..*",
]

HARDWARE_DEPENDS = {
    "adc", "spi", "i2c", "gpio-loopback", "can", "dma", "pwm",
    "comparator", "sensor", "usb", "ethernet", "display", "eeprom",
    "flash", "watchdog", "rtc", "counter", "jtag", "uart-pipe",
}

QEMU_BOARDS = {
    "qemu_x86", "qemu_x86_64", "qemu_cortex_m3", "qemu_cortex_m0",
    "qemu_cortex_a9", "qemu_riscv32", "qemu_riscv64", "qemu_arc",
}

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Minimum seconds between any two API calls.
# 0.72s ≈ 83 calls/min, safely under the 5000/hour primary limit.
# Also prevents burst-triggered secondary rate limits.
_MIN_CALL_INTERVAL = 0.72
_rate_state = {"last_call": 0.0}
_rate_lock = threading.Lock()

# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def gh_get(url, params=None):
    """GET request with proactive per-call throttle and reactive rate-limit backoff."""
    with _rate_lock:
        gap = _MIN_CALL_INTERVAL - (time.time() - _rate_state["last_call"])
        if gap > 0:
            time.sleep(gap)
        _rate_state["last_call"] = time.time()

    for attempt in range(5):
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code in (403, 429):
            retry_after = int(r.headers.get("Retry-After", 60))
            print(f"  Rate limited (HTTP {r.status_code}) — waiting {retry_after}s...")
            time.sleep(retry_after)
            # Reset the throttle clock so the retry also gets the full gap
            with _rate_lock:
                _rate_state["last_call"] = time.time()
            continue
        return r
    return None


def search_prs_by_date_range(date_range):
    """Fetch up to 1000 merged PRs in the given date range (paginated, max 100/page)."""
    query = f'repo:{REPO} is:pr is:merged linked:issue merged:{date_range}'
    url = "https://api.github.com/search/issues"
    items = []
    for page in range(1, 11):  # max 10 pages × 100 = 1000 results
        params = {"q": query, "sort": "updated", "order": "desc",
                  "per_page": 100, "page": page}
        r = gh_get(url, params)
        if r and r.status_code == 200:
            batch = r.json().get("items", [])
            items.extend(batch)
            if len(batch) < 100:
                break  # no more pages
        else:
            break
    return items


def get_pr_files(pr_number):
    """Return list of dicts {filename, status} for files changed in a PR."""
    url = f"https://api.github.com/repos/{REPO}/pulls/{pr_number}/files"
    r = gh_get(url, {"per_page": 100})
    if r and r.status_code == 200:
        return [{"filename": f["filename"], "status": f["status"]} for f in r.json()]
    return []


def get_pr_details(pr_number):
    """Return full PR details including base branch, diff size, and merge SHA."""
    url = f"https://api.github.com/repos/{REPO}/pulls/{pr_number}"
    r = gh_get(url)
    if r and r.status_code == 200:
        return r.json()
    return None


def get_file_content(path, ref=None):
    """Fetch decoded content of a file at the given ref (defaults to repo's default branch)."""
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    params = {"ref": ref} if ref else None
    r = gh_get(url, params)
    if r and r.status_code == 200:
        raw = r.json().get("content", "")
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    return None

# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------

SOURCE_EXTS = {".c", ".h", ".cpp", ".cc", ".S"}
HARDWARE_FILTER_RE = re.compile(r"CONFIG_TFM_|CONFIG_\w+_HAS_\w+|CONFIG_BOARD_|dt_compat_enabled\(")

# Match explicit close/fix/resolve keywords referencing a number
LINKED_ISSUE_KEYWORD_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*#(\d+)",
    re.IGNORECASE,
)
# Also match bare GitHub issue URLs
LINKED_ISSUE_URL_RE = re.compile(
    r"github\.com/zephyrproject-rtos/zephyr/issues/(\d+)",
    re.IGNORECASE,
)

# Rejection counters (updated from threads — use list to avoid GIL issues)
import collections
import threading
_reject_lock = threading.Lock()
_reject_counts = collections.Counter()


def find_testcase_yamls(files):
    return [f for f in files if f.endswith("testcase.yaml")]


def parse_yaml(content):
    try:
        return yaml.safe_load(content)
    except Exception:
        return None


def flatten_testcase(data):
    common = data.get("common", {}) or {}
    tests  = data.get("tests",  {}) or {}

    all_platform_allow = set()
    all_depends_on     = set()
    all_harnesses      = set()

    for _, cfg in tests.items():
        merged = {**common, **(cfg or {})}

        harness = merged.get("harness", "ztest") or "ztest"
        all_harnesses.add(harness.strip())

        pa = merged.get("platform_allow", [])
        if isinstance(pa, str):
            pa = pa.split()
        all_platform_allow.update(pa)

        dep = merged.get("depends_on", [])
        if isinstance(dep, str):
            dep = dep.split()
        all_depends_on.update(dep)

    return all_platform_allow, all_depends_on, all_harnesses


def has_hardware_filter(data):
    """Return True if any test case in the yaml has a hardware-specific filter."""
    common = data.get("common", {}) or {}
    for _, cfg in (data.get("tests", {}) or {}).items():
        merged = {**common, **(cfg or {})}
        f = merged.get("filter", "") or ""
        if f and HARDWARE_FILTER_RE.search(f):
            return True
    return False


def _reject(reason):
    with _reject_lock:
        _reject_counts[reason] += 1
    return None


def evaluate_pr(pr):
    """
    Full evaluation pipeline for a single PR.
    Returns a result dict on pass, or None if disqualified.
    """
    pr_number = pr["number"]

    # Skip [RFC], [WIP], [POC], [DRAFT] PRs
    title = pr.get("title", "")
    if any(tag in title.upper() for tag in ("[RFC]", "[WIP]", "[POC]", "[DRAFT]")):
        return _reject("draft/rfc/wip")

    # Extract linked issue numbers for display (filtering is done via linked:issue in search query)
    body = pr.get("body", "") or ""
    issue_numbers = (
        LINKED_ISSUE_KEYWORD_RE.findall(body) +
        LINKED_ISSUE_URL_RE.findall(body)
    )
    issue_numbers = list(dict.fromkeys(issue_numbers))  # deduplicate, preserve order

    # Must be merged into main (other branches won't be in the local git clone)
    pr_full = get_pr_details(pr_number)
    if not pr_full:
        return _reject("pr_details_fetch_failed")
    if pr_full.get("base", {}).get("ref") != "main":
        return _reject("not_main_branch")

    # Get merge SHA for fetching testcase.yaml at the correct historical state
    merge_sha = pr_full.get("merge_commit_sha")

    file_entries = get_pr_files(pr_number)
    if not file_entries:
        return _reject("no_files")

    files = [f["filename"] for f in file_entries]

    if not any(f.startswith("tests/") for f in files):
        return _reject("no_test_files")

    # Must have at least one NEW test file (status=added) — not just a modification.
    # New test files call new APIs that don't exist at parent_sha → guaranteed compile fail.
    new_test_files = [f["filename"] for f in file_entries
                      if f["filename"].startswith("tests/") and f["status"] == "added"]
    if not new_test_files:
        return _reject("no_new_test_files")

    # Must have at least one non-test header file changed (confirms new API was added)
    header_changes = [f for f in files
                      if f.endswith(".h")
                      and not f.startswith("tests/")
                      and not f.startswith("doc/")]
    if not header_changes:
        return _reject("no_header_changes")

    # Must have at least one non-test source file changed
    non_test_source = [
        f for f in files
        if not f.startswith("tests/")
        and not f.startswith("samples/")
        and not f.startswith("doc/")
        and Path(f).suffix in SOURCE_EXTS
    ]
    if not non_test_source:
        return _reject("no_non_test_source")

    yaml_paths = find_testcase_yamls(files)
    if not yaml_paths:
        return _reject("no_testcase_yaml")

    if any(f.endswith(".overlay") for f in files):
        return _reject("has_overlay")

    # Filter per yaml; keep PR if at least one yaml passes all filters
    passing_yamls = []
    yaml_reject_reasons = []
    for yaml_path in yaml_paths:
        content = get_file_content(yaml_path, ref=merge_sha)
        if not content:
            yaml_reject_reasons.append("yaml_fetch_failed")
            continue

        data = parse_yaml(content)
        if not data:
            yaml_reject_reasons.append("yaml_parse_failed")
            continue

        if (data.get("common") or {}).get("type") == "unit":
            yaml_reject_reasons.append("yaml_unit_test")
            continue

        if has_hardware_filter(data):
            yaml_reject_reasons.append("yaml_hw_filter")
            continue

        pa, dep, harnesses = flatten_testcase(data)

        if harnesses - {"ztest", "console", ""}:
            yaml_reject_reasons.append("yaml_bad_harness")
            continue

        if dep & HARDWARE_DEPENDS:
            yaml_reject_reasons.append("yaml_hw_depends")
            continue

        if pa and not (pa & QEMU_BOARDS):
            yaml_reject_reasons.append("yaml_no_qemu_board")
            continue

        # When platform_allow is empty, check arch_allow
        if not pa:
            arch_board_map = {
                "arm":   {"qemu_cortex_m3", "qemu_cortex_m0", "qemu_cortex_a9"},
                "arm64": {"qemu_cortex_a9"},
                "x86":   {"qemu_x86", "qemu_x86_64"},
                "riscv": {"qemu_riscv32", "qemu_riscv64"},
                "arc":   {"qemu_arc"},
            }
            arch_allow = data.get("common", {}).get("arch_allow") or ""
            if isinstance(arch_allow, str):
                arch_allow = arch_allow.split()
            if arch_allow:
                compatible = set()
                for arch in arch_allow:
                    compatible.update(arch_board_map.get(arch, set()))
                if not compatible:
                    yaml_reject_reasons.append("yaml_arch_no_qemu")
                    continue  # arch with no QEMU board

        passing_yamls.append((yaml_path, pa, dep))

    if not passing_yamls:
        # attribute to most common yaml rejection reason
        reason = yaml_reject_reasons[0] if yaml_reject_reasons else "yaml_all_failed_unknown"
        return _reject(reason)

    yaml_paths     = [y for y, _, _ in passing_yamls]
    platform_allow = set().union(*(pa  for _, pa,  _ in passing_yamls))
    depends_on     = set().union(*(dep for _, _,  dep in passing_yamls))

    # Architecture consistency check
    arch_files = [
        f for f in non_test_source
        if f.startswith("arch/") or f.startswith("soc/")
    ]
    if arch_files and len(arch_files) == len(non_test_source):
        arches = {f.split("/")[1] for f in arch_files if f.startswith("arch/")}
        arch_board_map = {
            "arm":   {"qemu_cortex_m3", "qemu_cortex_m0", "qemu_cortex_a9"},
            "arm64": {"qemu_cortex_a9"},
            "x86":   {"qemu_x86", "qemu_x86_64"},
            "riscv": {"qemu_riscv32", "qemu_riscv64"},
            "arc":   {"qemu_arc"},
        }
        compatible_boards = set()
        for arch in arches:
            compatible_boards.update(arch_board_map.get(arch, set()))
        if compatible_boards and platform_allow and not (platform_allow & compatible_boards):
            return _reject("arch_platform_mismatch")
        if compatible_boards and not platform_allow:
            if "qemu_x86" not in compatible_boards and "qemu_x86_64" not in compatible_boards:
                return _reject("arch_no_x86_qemu")

    # --- Scoring ---
    score = 0
    reasons = []

    if not platform_allow:
        score += 2
        reasons.append("platform agnostic")
    elif platform_allow & QEMU_BOARDS:
        score += 2
        reasons.append(f"targets QEMU: {platform_allow & QEMU_BOARDS}")

    if not depends_on:
        score += 1
        reasons.append("no depends_on")

    if any(
        f.startswith(yp.rsplit("/", 1)[0]) and f.endswith(".c")
        for yp in yaml_paths for f in files
    ):
        score += 1
        reasons.append("includes test source .c")

    score += 1
    reasons.append("modifies non-test source")

    linked_issues = [
        f"https://github.com/{REPO}/issues/{n}" for n in issue_numbers
    ]
    if linked_issues:
        score += 1
        reasons.append(f"linked issue(s): {', '.join('#' + n for n in issue_numbers)}")

    return {
        "pr_number":      pr_number,
        "title":          title,
        "url":            pr["html_url"],
        "merged_at":      pr.get("closed_at", "unknown"),
        "score":          score,
        "reasons":        reasons,
        "testcase_yamls": yaml_paths,
        "platform_allow": list(platform_allow),
        "depends_on":     list(depends_on),
        "linked_issues":  linked_issues,
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Find Zephyr PR candidates for QEMU pipeline")
    parser.add_argument("--count", type=int, default=100,
                        help="Number of candidate PRs to find (default: 100)")
    args = parser.parse_args()
    target_count = args.count

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN environment variable is not set.")
        sys.exit(1)

    start_time = time.time()

    def time_remaining():
        return TIME_LIMIT_SECS - (time.time() - start_time)

    def timed_out():
        return time_remaining() <= 0

    print("=" * 65)
    print("  Zephyr PR Filter — QEMU Pipeline")
    print(f"  Target: {target_count} PRs   Time limit: {TIME_LIMIT_SECS // 60} min")
    print("=" * 65)

    # --- Phase 1: collect candidates via date-range search ---
    print("\nPhase 1: date-range pre-filter (merged Sept 2024+)...")
    candidates = []
    seen = set()

    for date_range in SEARCH_DATE_RANGES:
        if timed_out():
            print("  Time limit reached during Phase 1 — proceeding with candidates so far.")
            break
        print(f"  Searching: merged:{date_range}")
        prs = search_prs_by_date_range(date_range)
        for pr in prs:
            if pr["number"] not in seen:
                seen.add(pr["number"])
                candidates.append(pr)

    print(f"\n  {len(candidates)} unique candidates collected.")
    candidates = candidates[:MAX_CANDIDATES]

    # --- Phase 2: deep filter with threading ---
    print(f"\nPhase 2: deep filtering up to {len(candidates)} PRs "
          f"(target: {target_count}, time left: {time_remaining():.0f}s)...\n")

    results = []
    results_file = Path(__file__).parent / "results" / "candidate_prs.json"
    results_file.parent.mkdir(exist_ok=True)

    # Load any previously saved results so interrupted runs can be resumed
    seen_pr_numbers = set()
    if results_file.exists():
        try:
            existing = json.loads(results_file.read_text())
            results = existing
            seen_pr_numbers = {r["pr_number"] for r in results}
            if results:
                print(f"  Resuming — {len(results)} results already saved.\n")
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {executor.submit(evaluate_pr, pr): pr
                   for pr in candidates if pr["number"] not in seen_pr_numbers}
        for future in as_completed(futures, timeout=time_remaining()):
            if timed_out():
                print("\n  Time limit reached — stopping early with results so far.")
                for f in futures:
                    f.cancel()
                break
            try:
                result = future.result(timeout=max(1, time_remaining()))
            except Exception:
                continue
            if result:
                results.append(result)
                elapsed = time.time() - start_time
                print(f"  PASS  PR #{result['pr_number']:5d} "
                      f"(score {result['score']})  "
                      f"[{elapsed:.0f}s]  "
                      f"{result['title'][:50]}")
                # Save incrementally after every new result
                results_file.write_text(json.dumps(results, indent=2))
                if len(results) >= target_count:
                    for f in futures:
                        f.cancel()
                    break

    elapsed_total = time.time() - start_time
    print(f"\n  Phase 2 complete in {elapsed_total:.1f}s")

    # Print rejection breakdown
    if _reject_counts:
        total_rejected = sum(_reject_counts.values())
        print(f"\n  Rejection breakdown ({total_rejected} PRs rejected):")
        for reason, count in sorted(_reject_counts.items(), key=lambda x: -x[1]):
            print(f"    {count:5d}  {reason}")

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:target_count]

    print(f"\n{'='*65}")
    print(f"  TOP {len(results)} CANDIDATE PRs  (completed in {elapsed_total:.1f}s)")
    print(f"{'='*65}\n")

    for i, r in enumerate(results, 1):
        print(f"{i:2}. PR #{r['pr_number']}  (score: {r['score']})")
        print(f"    Title : {r['title']}")
        print(f"    URL   : {r['url']}")
        for y in r["testcase_yamls"]:
            print(f"    Test  : {y}")
        print(f"    Why   : {', '.join(r['reasons'])}")
        if r["platform_allow"]:
            print(f"    Platforms: {r['platform_allow']}")
        if r["linked_issues"]:
            for issue in r["linked_issues"]:
                print(f"    Issue : {issue}")
        print()

    if not results:
        print("  No candidates found within time limit. Try running again.")
        sys.exit(1)

    print(f"Results saved to {results_file}")


if __name__ == "__main__":
    main()
