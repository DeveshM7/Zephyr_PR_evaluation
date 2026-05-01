#!/usr/bin/env python3
"""
results_summary.py — Print a table of all EmbedEval validation results.

Usage:
    python3 scripts/results_summary.py
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"


def main() -> None:
    if not RESULTS_DIR.exists():
        print("No results directory found. Run some PR validations first.")
        return

    results = []
    for result_file in sorted(RESULTS_DIR.glob("*/result.json")):
        try:
            data = json.loads(result_file.read_text())
            results.append(data)
        except Exception as e:
            print(f"Warning: could not read {result_file}: {e}")

    if not results:
        print("No results found in results/")
        return

    # Print table
    col_id      = max(len(r["instance_id"]) for r in results)
    col_status  = max(len(r.get("status", "")) for r in results)
    col_id      = max(col_id, len("INSTANCE"))
    col_status  = max(col_status, len("STATUS"))

    header = f"{'INSTANCE':<{col_id}}  {'STATUS':<{col_status}}  {'EXIT':>4}  {'DURATION':>10}  TIMESTAMP"
    print(header)
    print("-" * len(header))

    validated = 0
    for r in results:
        instance   = r.get("instance_id", "?")
        status     = r.get("status", "?")
        exit_code  = r.get("pass_step_exit_code", "?")
        duration   = r.get("duration_seconds", "?")
        timestamp  = r.get("timestamp", "?")[:19]  # trim subseconds

        if isinstance(duration, int):
            mins, secs = divmod(duration, 60)
            duration_str = f"{mins}m{secs:02d}s"
        else:
            duration_str = str(duration)

        status_display = status
        if status == "validated":
            status_display = "VALIDATED"
            validated += 1
        elif status == "error":
            status_display = "ERROR"

        print(f"{instance:<{col_id}}  {status_display:<{col_status}}  {str(exit_code):>4}  {duration_str:>10}  {timestamp}")

    print("-" * len(header))
    print(f"{validated}/{len(results)} validated")
    print()

    # List log paths for any errors
    errors = [r for r in results if r.get("status") != "validated"]
    if errors:
        print("Logs for non-validated runs:")
        for r in errors:
            print(f"  {r.get('log', '?')}")


if __name__ == "__main__":
    main()
