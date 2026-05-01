#!/usr/bin/env bash
# Builds and validates the EmbedEval instance for Zephyr PR #102462:
# "kernel: Add K_TIMEOUT_SUM() macro"
#
# Runs all steps in sequence:
#   1. Generate test_patch.diff from GitHub
#   2. Build base image (skipped if already exists)
#   3. Build instance image (~30 min)
#   4. Validate: confirm FAIL on broken code, then PASS after fix
#
# Usage: ./scripts/run_102462.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTANCE_DIR="${REPO_ROOT}/docker/instances/zephyr__zephyr-102462"

BASE_COMMIT="3dfe5a98f6d99d63cab90475bbff5932ee3cc4bf"
MERGE_COMMIT="e18a048c8499d859163a25c89a7cc98b15431833"
IMAGE="embedbench:zephyr-102462"
QEMU_TIMEOUT=60

WORK_DIR=""
CID=""

cleanup() {
    if [ -n "${CID}" ]; then
        echo "Stopping container..."
        docker stop "${CID}" >/dev/null 2>&1 && docker rm "${CID}" >/dev/null 2>&1 || true
    fi
    if [ -n "${WORK_DIR}" ] && [ -d "${WORK_DIR}" ]; then
        rm -rf "${WORK_DIR}"
    fi
}
trap cleanup EXIT

# ── Step 1: Generate test_patch.diff ─────────────────────────────────────────
echo "=== Step 1: Generating test_patch.diff ==="
WORK_DIR="$(mktemp -d)"
git clone --filter=blob:none --no-checkout \
    https://github.com/zephyrproject-rtos/zephyr.git \
    "${WORK_DIR}/zephyr" -q
cd "${WORK_DIR}/zephyr"
git fetch origin "${MERGE_COMMIT}" -q
git diff "${BASE_COMMIT}..${MERGE_COMMIT}" -- tests/kernel/timer/timeout/ \
    > "${INSTANCE_DIR}/test_patch.diff"
echo "test_patch.diff: $(wc -l < "${INSTANCE_DIR}/test_patch.diff") lines"
cd "${REPO_ROOT}"

# ── Step 2: Build base image (skip if already exists) ────────────────────────
echo ""
echo "=== Step 2: Base image ==="
if docker image inspect embedbench-zephyr-base:latest &>/dev/null; then
    echo "Already exists, skipping."
else
    echo "Building embedbench-zephyr-base:latest ..."
    docker build \
        -f "${REPO_ROOT}/docker/bases/zephyr.Dockerfile" \
        -t embedbench-zephyr-base:latest \
        "${REPO_ROOT}/docker/bases/"
fi

# ── Step 3: Build instance image (skip if already exists) ────────────────────
echo ""
echo "=== Step 3: Instance image ==="
if docker image inspect "${IMAGE}" &>/dev/null; then
    echo "Already exists, skipping."
else
    echo "Building (~30 min) ..."
    docker build \
        --build-arg BASE_COMMIT="${BASE_COMMIT}" \
        --build-arg ZEPHYR_SDK_VERSION=0.16.8 \
        --build-arg PLATFORM=qemu_x86 \
        --build-arg TEST_PATH=tests/kernel/timer/timeout \
        -t "${IMAGE}" \
        "${INSTANCE_DIR}"
fi

# ── Step 4a: Verify FAIL on broken code ──────────────────────────────────────
echo ""
echo "=== Step 4a: Verifying tests FAIL on broken code ==="
echo "(expect compile error — K_TIMEOUT_SUM does not exist at base_commit)"
CID=$(docker run -d "${IMAGE}" sleep infinity)

docker exec "${CID}" bash -c "
    source /opt/zephyr-venv/bin/activate
    cd /testbed
    west build -b qemu_x86 tests/kernel/timer/timeout 2>&1
    timeout ${QEMU_TIMEOUT} west build -t run 2>&1 || true
" || true

docker exec "${CID}" bash -c \
    "rm -f /testbed/build/zephyr/qemu.pid /testbed/build/qemu.pid" 2>/dev/null || true

# ── Step 4b: Apply fix, verify PASS ──────────────────────────────────────────
echo ""
echo "=== Step 4b: Applying fix and verifying tests PASS ==="
cd "${WORK_DIR}/zephyr"
git diff "${BASE_COMMIT}..${MERGE_COMMIT}" -- \
    include/zephyr/kernel.h \
    include/zephyr/sys/clock.h \
    > "${WORK_DIR}/fix_patch.diff"
echo "fix_patch.diff: $(wc -l < "${WORK_DIR}/fix_patch.diff") lines"
cd "${REPO_ROOT}"

docker cp "${WORK_DIR}/fix_patch.diff" "${CID}:/tmp/fix_patch.diff"

docker exec "${CID}" bash -c "
    source /opt/zephyr-venv/bin/activate
    cd /testbed
    git apply /tmp/fix_patch.diff
    west build -b qemu_x86 tests/kernel/timer/timeout 2>&1
    timeout ${QEMU_TIMEOUT} west build -t run 2>&1
"

echo ""
echo "=== Validation complete ==="
echo "Expected: compile error before fix, test_timeout_sum_* PASS after fix."
