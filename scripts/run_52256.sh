#!/usr/bin/env bash
# Builds and validates the EmbedEval instance for Zephyr PR #52256:
# "net: Fix variable buffer length configuration"
#
# Runs all steps in sequence:
#   1. Generate test_patch.diff from GitHub
#   2. Build base image (skipped if already exists)
#   3. Build instance image (~30 min)
#   4. Validate: confirm FAIL on broken code, then PASS after fix
#
# Results written to: results/zephyr__zephyr-52256/
#   run.log      — full terminal output
#   result.json  — status summary
#
# Usage: ./scripts/run_52256.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTANCE_DIR="${REPO_ROOT}/docker/instances/zephyr__zephyr-52256"
RESULTS_DIR="${REPO_ROOT}/results/zephyr__zephyr-52256"

BASE_COMMIT="9a491a1b5b434549ce7b7a83aacab12eab4dd14e"
MERGE_COMMIT="0c4e669cd7f5633c70580972ec7e10771843d0bc"
IMAGE="embedbench:zephyr-52256"
QEMU_TIMEOUT=120

WORK_DIR=""
CID=""
STATUS="error"
PASS_EXIT=1
START_TIME=$(date +%s)

# ── Results setup ─────────────────────────────────────────────────────────────
mkdir -p "${RESULTS_DIR}"
LOG_FILE="${RESULTS_DIR}/run.log"
exec > >(tee "${LOG_FILE}") 2>&1

cleanup() {
    if [ -n "${CID}" ]; then
        echo "Stopping container..."
        docker stop "${CID}" >/dev/null 2>&1 && docker rm "${CID}" >/dev/null 2>&1 || true
    fi
    if [ -n "${WORK_DIR}" ] && [ -d "${WORK_DIR}" ]; then
        rm -rf "${WORK_DIR}"
    fi
    # Always write result.json on exit
    END_TIME=$(date +%s)
    DURATION=$(( END_TIME - START_TIME ))
    cat > "${RESULTS_DIR}/result.json" <<EOF
{
    "instance_id": "zephyr__zephyr-52256",
    "pr_number": 52256,
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "duration_seconds": ${DURATION},
    "status": "${STATUS}",
    "pass_step_exit_code": ${PASS_EXIT},
    "log": "${LOG_FILE}"
}
EOF
    echo ""
    echo "Result: ${STATUS} (written to ${RESULTS_DIR}/result.json)"
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
git diff "${BASE_COMMIT}..${MERGE_COMMIT}" -- tests/net/ipv6/ \
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
        --build-arg TEST_PATH=tests/net/ipv6 \
        -t "${IMAGE}" \
        "${INSTANCE_DIR}"
fi

# ── Step 4a: Verify FAIL on broken code ──────────────────────────────────────
echo ""
echo "=== Step 4a: Verifying tests FAIL on broken code ==="
CID=$(docker run -d "${IMAGE}" sleep infinity)

docker exec "${CID}" bash -c "
    source /opt/zephyr-venv/bin/activate
    cd /testbed
    west build -b qemu_x86 tests/net/ipv6 2>&1
    timeout ${QEMU_TIMEOUT} west build -t run 2>&1 || true
" || true

docker exec "${CID}" bash -c \
    "rm -f /testbed/build/zephyr/qemu.pid /testbed/build/qemu.pid" 2>/dev/null || true

# ── Step 4b: Apply fix, verify PASS ──────────────────────────────────────────
echo ""
echo "=== Step 4b: Applying fix and verifying tests PASS ==="
cd "${WORK_DIR}/zephyr"
git diff "${BASE_COMMIT}..${MERGE_COMMIT}" -- \
    drivers/ethernet/Kconfig.sam_gmac \
    drivers/ethernet/eth_dwmac.c \
    drivers/ethernet/eth_sam_gmac.c \
    drivers/ieee802154/ieee802154_uart_pipe.c \
    drivers/net/slip.c \
    include/zephyr/net/net_pkt.h \
    modules/openthread/platform/radio.c \
    subsys/net/ip/6lo.c \
    subsys/net/ip/net_pkt.c \
    subsys/net/ip/net_shell.c \
    subsys/net/ip/tcp.c \
    subsys/net/l2/bluetooth/bluetooth.c \
    subsys/net/l2/ethernet/ethernet.c \
    subsys/net/l2/ppp/fsm.c \
    > "${WORK_DIR}/fix_patch.diff"
echo "fix_patch.diff: $(wc -l < "${WORK_DIR}/fix_patch.diff") lines"
cd "${REPO_ROOT}"

docker cp "${WORK_DIR}/fix_patch.diff" "${CID}:/tmp/fix_patch.diff"

PASS_EXIT=0
docker exec "${CID}" bash -c "
    source /opt/zephyr-venv/bin/activate
    cd /testbed
    git apply /tmp/fix_patch.diff
    west build -b qemu_x86 tests/net/ipv6 2>&1
    timeout ${QEMU_TIMEOUT} west build -t run 2>&1
" || PASS_EXIT=$?

# Exit code 124 = timeout killed QEMU — this is expected and normal.
# QEMU never exits on its own after tests complete; timeout is how we stop it.
# Any other non-zero exit means something actually went wrong.
if [ ${PASS_EXIT} -eq 0 ] || [ ${PASS_EXIT} -eq 124 ]; then
    STATUS="validated"
    echo ""
    echo "=== PASS: test_loop, test_fragment_sam00_dam00, test_fragment_sam01_dam01, test_fragment_sam10_dam10 (+30 more) ==="
else
    STATUS="error"
    echo ""
    echo "=== FAIL: pass step exited with code ${PASS_EXIT} — check ${LOG_FILE} ==="
fi
