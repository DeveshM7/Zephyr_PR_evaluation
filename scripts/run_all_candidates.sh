#!/usr/bin/env bash
# run_all_candidates.sh — Run all PRs in candidates/selected_candidates.json sequentially.
#
# For each PR:
#   1. Generate instance files (Dockerfile, metadata.json, run script)
#   2. Run the validation script (builds image, validates FAIL→PASS, saves results)
#   3. Remove the PR's Docker image and prune build cache
#
# Cleanup (docker rmi + docker builder prune) is GUARANTEED to run after each PR,
# even if the run script crashes or you hit Ctrl+C.
#
# Usage:
#   ./scripts/run_all_candidates.sh
#
# To skip PRs that already have a result.json:
#   SKIP_DONE=1 ./scripts/run_all_candidates.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CANDIDATES_FILE="${REPO_ROOT}/candidates/selected_candidates.json"

if [ ! -f "${CANDIDATES_FILE}" ]; then
    echo "ERROR: ${CANDIDATES_FILE} not found"
    exit 1
fi

# ── Cleanup function — always called after each PR (and on Ctrl+C) ─────────────
CURRENT_PR=""

cleanup_pr() {
    local pr="${1:-${CURRENT_PR}}"
    if [ -z "${pr}" ]; then return; fi
    local image="embedbench:zephyr-${pr}"

    echo ""
    echo "--- Cleanup: PR #${pr} ---"
    if docker image inspect "${image}" &>/dev/null 2>&1; then
        echo "  Removing ${image} ..."
        docker rmi "${image}" 2>/dev/null || echo "  WARNING: docker rmi failed — image may still exist"
    else
        echo "  Image ${image} not present — nothing to remove"
    fi
    echo "  Pruning build cache ..."
    docker builder prune -f
    echo "  Cleanup done for PR #${pr}"
}

# On Ctrl+C or unexpected exit, clean up whatever PR is currently running
trap 'echo ""; echo "=== INTERRUPTED — cleaning up PR #${CURRENT_PR} ==="; cleanup_pr "${CURRENT_PR}"; exit 1' INT TERM

# ── Extract PR numbers from JSON ───────────────────────────────────────────────
PR_NUMBERS=$(python3 -c "
import json
data = json.load(open('${CANDIDATES_FILE}'))
for entry in data:
    print(entry['pr_number'])
")

TOTAL=$(echo "${PR_NUMBERS}" | wc -l | tr -d ' ')
echo "======================================================"
echo "  EmbedEval batch runner — ${TOTAL} PRs to process"
echo "======================================================"

IDX=0
for PR in ${PR_NUMBERS}; do
    IDX=$((IDX + 1))
    CURRENT_PR="${PR}"
    RESULT_FILE="${REPO_ROOT}/results/zephyr__zephyr-${PR}/result.json"

    echo ""
    echo "======================================================"
    echo "  [${IDX}/${TOTAL}] PR #${PR}"
    echo "======================================================"

    # Skip if already run and SKIP_DONE is set
    if [ "${SKIP_DONE:-0}" = "1" ] && [ -f "${RESULT_FILE}" ]; then
        STATUS=$(python3 -c "import json; print(json.load(open('${RESULT_FILE}'))['status'])" 2>/dev/null || echo "unknown")
        echo "  Already run (status=${STATUS}) — skipping"
        CURRENT_PR=""
        continue
    fi

    # ── Step 1: Generate instance files ──────────────────────────────────────
    echo ""
    echo "--- Step 1: Generating instance files ---"
    if ! python3 "${SCRIPT_DIR}/generate_instance.py" "${PR}" --no-diff; then
        echo "ERROR: generate_instance.py failed for PR #${PR} — skipping (no Docker built, nothing to clean)"
        CURRENT_PR=""
        continue
    fi

    # ── Step 2: Run validation ────────────────────────────────────────────────
    echo ""
    echo "--- Step 2: Running validation script ---"
    RUN_SCRIPT="${SCRIPT_DIR}/run_${PR}.sh"
    if [ ! -f "${RUN_SCRIPT}" ]; then
        echo "ERROR: ${RUN_SCRIPT} not found — skipping PR #${PR}"
        CURRENT_PR=""
        continue
    fi
    bash "${RUN_SCRIPT}" || true   # errors captured in result.json by the run script's own trap

    # ── Step 3: Cleanup — always runs ────────────────────────────────────────
    cleanup_pr "${PR}"
    CURRENT_PR=""

    # Print result
    if [ -f "${RESULT_FILE}" ]; then
        STATUS=$(python3 -c "import json; print(json.load(open('${RESULT_FILE}'))['status'])" 2>/dev/null || echo "unknown")
        echo ""
        echo "  >>> PR #${PR} result: ${STATUS} <<<"
    fi
done

echo ""
echo "======================================================"
echo "  Batch complete — ${IDX} PRs processed"
echo "======================================================"
echo ""
python3 "${SCRIPT_DIR}/results_summary.py"
