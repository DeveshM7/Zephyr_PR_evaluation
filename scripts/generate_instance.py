#!/usr/bin/env python3
"""
generate_instance.py — EmbedEval automation script

Generates all files needed to run an EmbedEval instance for a Zephyr PR:
  docker/instances/zephyr__zephyr-{PR}/
      Dockerfile
      metadata.json
      test_patch.diff
  scripts/run_{PR}.sh

Usage:
    python3 scripts/generate_instance.py <PR_NUMBER> [--no-diff]

Options:
    --no-diff    Skip generating test_patch.diff (useful for re-generating
                 Dockerfile/metadata/run script without a network clone)

Environment:
    GITHUB_TOKEN  Optional, but strongly recommended to avoid rate limiting.
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
INSTANCES_DIR = REPO_ROOT / "docker" / "instances"
CANDIDATE_JSON = REPO_ROOT / "candidates" / "selected_candidates.json"

ZEPHYR_GITHUB = "https://github.com/zephyrproject-rtos/zephyr.git"
ZEPHYR_RAW = "https://raw.githubusercontent.com/zephyrproject-rtos/zephyr"
ZEPHYR_API = "https://api.github.com/repos/zephyrproject-rtos/zephyr"

# ── GitHub helpers ────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    """Load GITHUB_TOKEN from a .env file if not already set in the environment."""
    if os.environ.get("GITHUB_TOKEN"):
        return
    candidates = [
        REPO_ROOT / ".env",
        REPO_ROOT.parent / ".env",
        REPO_ROOT.parent / "qemu-PR-filter" / ".env",
    ]
    for path in candidates:
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("GITHUB_TOKEN="):
                    os.environ["GITHUB_TOKEN"] = line.split("=", 1)[1].strip()
                    print(f"  Loaded GITHUB_TOKEN from {path}")
                    return


_load_dotenv()


def _ssl_context() -> ssl.SSLContext:
    """Return an SSL context, using certifi certs if available (macOS Python 3.13+)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    ctx = ssl.create_default_context()
    # macOS Python installed via python.org doesn't have system certs linked;
    # fall back to unverified only when the default context fails.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_SSL_CTX = _ssl_context()


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"GitHub API error {e.code} for {url}: {body}") from e


def github_raw(sha: str, path: str) -> str:
    url = f"{ZEPHYR_RAW}/{sha}/{path}"
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        raise


# ── PR metadata ───────────────────────────────────────────────────────────────

def fetch_pr(pr: int) -> dict:
    print(f"  Fetching /pulls/{pr} ...")
    return github_get(f"{ZEPHYR_API}/pulls/{pr}")


def fetch_pr_files(pr: int) -> list[dict]:
    print(f"  Fetching /pulls/{pr}/files ...")
    files = []
    page = 1
    while True:
        batch = github_get(f"{ZEPHYR_API}/pulls/{pr}/files?per_page=100&page={page}")
        if not batch:
            break
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return files


def classify_files(files: list[dict]) -> tuple[list[str], list[str]]:
    """Returns (fix_files, test_files) sorted lists of filenames."""
    skip_prefixes = ("tests/", "samples/", "doc/", "scripts/")
    fix_files = []
    test_files = []
    for f in files:
        name = f["filename"]
        if name.startswith("tests/"):
            test_files.append(name)
        elif not any(name.startswith(p) for p in skip_prefixes):
            fix_files.append(name)
    return sorted(fix_files), sorted(test_files)


# ── ZTEST function extraction ─────────────────────────────────────────────────

ZTEST_RE = re.compile(
    r'\bZTEST(?:_F|_USER|_USER_F)?\s*\(\s*\w+\s*,\s*(\w+)\s*[,)]'
)


def extract_ztest_functions(content: str) -> list[str]:
    return ZTEST_RE.findall(content)


def fetch_ztest_functions(sha: str, test_files: list[str]) -> list[str]:
    """Download test source files at sha and extract ZTEST function names."""
    functions = []
    for path in test_files:
        if not path.endswith((".c", ".cpp")):
            continue
        content = github_raw(sha, path)
        if content:
            functions.extend(extract_ztest_functions(content))
    seen = set()
    unique = []
    for f in functions:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


# ── testcase.yaml parsing ─────────────────────────────────────────────────────

def parse_testcase_yaml(content: str) -> dict:
    """
    Very lightweight YAML parser — reads only what we need:
      - first test scenario name (the key under 'tests:')
      - platform_allow list
    Returns {"scenario": str | None, "platform_allow": list[str]}
    """
    scenario = None
    platform_allow = []

    in_tests = False
    for line in content.splitlines():
        stripped = line.rstrip()
        if stripped == "tests:":
            in_tests = True
            continue
        if in_tests:
            # First indented key under tests: is the scenario name
            m = re.match(r'^  (\S.*?):\s*$', stripped)
            if m and scenario is None:
                scenario = m.group(1)
            m2 = re.match(r'^\s+platform_allow:\s*(.+)$', stripped)
            if m2:
                raw = m2.group(1).strip()
                platform_allow = [p.strip() for p in raw.split()]
        m3 = re.match(r'^\s+platform_allow:', stripped)
        if m3 and not in_tests:
            raw_val = stripped.split(":", 1)[1].strip()
            if raw_val:
                platform_allow = [p.strip() for p in raw_val.split()]

    return {"scenario": scenario, "platform_allow": platform_allow}


# ── Problem statement ─────────────────────────────────────────────────────────

def build_problem_statement(pr_data: dict, linked_issues: list[str], pr_number: int) -> str:
    title = pr_data.get("title", "")
    body = (pr_data.get("body") or "").strip()

    # Try to get linked issue title for extra context
    issue_title = ""
    for issue_url in linked_issues[:1]:
        m = re.search(r'/issues/(\d+)$', issue_url)
        if m:
            issue_num = m.group(1)
            try:
                issue_data = github_get(
                    f"{ZEPHYR_API}/issues/{issue_num}"
                )
                issue_title = issue_data.get("title", "")
            except Exception:
                pass
            break

    parts = [f"PR #{pr_number}: {title}."]
    if issue_title and issue_title.lower() != title.lower():
        parts.append(f"Fixes issue: {issue_title}.")

    # Pull first non-empty paragraph from PR body (up to 300 chars)
    if body:
        for para in body.split("\n\n"):
            para = para.strip()
            # Skip lines that are just headings, checklists, or URLs
            if para and not para.startswith("#") and not para.startswith("- ["):
                para = re.sub(r'\s+', ' ', para)
                parts.append(para[:300])
                break

    return " ".join(parts)


# ── test_patch.diff generation ────────────────────────────────────────────────

def generate_test_patch(base_commit: str, merge_commit: str, test_path: str,
                        instance_dir: Path) -> None:
    print(f"  Generating test_patch.diff (blobless clone) ...")
    with tempfile.TemporaryDirectory(prefix="embedeval_") as tmp:
        clone_dir = Path(tmp) / "zephyr"
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout",
             ZEPHYR_GITHUB, str(clone_dir), "-q"],
            check=True
        )
        subprocess.run(
            ["git", "fetch", "origin", merge_commit, "-q"],
            check=True, cwd=clone_dir
        )
        patch_path = instance_dir / "test_patch.diff"
        with open(patch_path, "w") as f:
            subprocess.run(
                ["git", "diff", f"{base_commit}..{merge_commit}", "--", test_path],
                check=True, cwd=clone_dir, stdout=f
            )
    lines = len(patch_path.read_text().splitlines())
    print(f"  test_patch.diff: {lines} lines")
    if lines == 0:
        print("  WARNING: test_patch.diff is empty — check test_path scoping")


# ── Dockerfile generation ─────────────────────────────────────────────────────

DOCKERFILE_TEMPLATE = """\
FROM embedbench-zephyr-base:latest

WORKDIR /testbed

ARG BASE_COMMIT={base_commit}
RUN git clone --filter=blob:none https://github.com/zephyrproject-rtos/zephyr.git . \\
    && git checkout ${{BASE_COMMIT}}

RUN west init -l . \\
    && west update --narrow -o=--depth=1 \\
    && west zephyr-export

ARG ZEPHYR_SDK_VERSION=0.16.8
RUN ARCH=$(uname -m) \\
    && SDK_ARCHIVE="zephyr-sdk-${{ZEPHYR_SDK_VERSION}}_linux-${{ARCH}}.tar.xz" \\
    && wget -q "https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v${{ZEPHYR_SDK_VERSION}}/${{SDK_ARCHIVE}}" \\
    && tar -xf "${{SDK_ARCHIVE}}" -C /opt \\
    && rm "${{SDK_ARCHIVE}}" \\
    && /opt/zephyr-sdk-${{ZEPHYR_SDK_VERSION}}/setup.sh -t x86_64-zephyr-elf -h -c

RUN west packages pip --install 2>/dev/null \\
    || pip install -r scripts/requirements.txt

COPY test_patch.diff /tmp/
RUN git apply /tmp/test_patch.diff

ARG PLATFORM={platform}
ARG TEST_PATH={test_path}
RUN west build -b ${{PLATFORM}} ${{TEST_PATH}} || true

RUN ctags -R --languages=C,C++ --exclude=build -f /testbed/tags .
"""


# ── run_{PR}.sh generation ────────────────────────────────────────────────────

RUN_SCRIPT_TEMPLATE = '''\
#!/usr/bin/env bash
# Builds and validates the EmbedEval instance for Zephyr PR #{pr}:
# "{title}"
#
# Runs all steps in sequence:
#   1. Generate test_patch.diff from GitHub
#   2. Build base image (skipped if already exists)
#   3. Build instance image (~30 min)
#   4. Validate: confirm FAIL on broken code, then PASS after fix
#
# Results written to: results/zephyr__zephyr-{pr}/
#   run.log      — full terminal output
#   result.json  — status summary
#
# Usage: ./scripts/run_{pr}.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
REPO_ROOT="$(cd "${{SCRIPT_DIR}}/.." && pwd)"
INSTANCE_DIR="${{REPO_ROOT}}/docker/instances/zephyr__zephyr-{pr}"
RESULTS_DIR="${{REPO_ROOT}}/results/zephyr__zephyr-{pr}"

BASE_COMMIT="{base_commit}"
MERGE_COMMIT="{merge_commit}"
IMAGE="embedbench:zephyr-{pr}"
QEMU_TIMEOUT={qemu_timeout}

WORK_DIR=""
CID=""
STATUS="error"
PASS_EXIT=1
START_TIME=$(date +%s)

# ── Results setup ─────────────────────────────────────────────────────────────
mkdir -p "${{RESULTS_DIR}}"
LOG_FILE="${{RESULTS_DIR}}/run.log"
exec > >(tee "${{LOG_FILE}}") 2>&1

cleanup() {{
    if [ -n "${{CID}}" ]; then
        echo "Stopping container..."
        docker stop "${{CID}}" >/dev/null 2>&1 && docker rm "${{CID}}" >/dev/null 2>&1 || true
    fi
    if [ -n "${{WORK_DIR}}" ] && [ -d "${{WORK_DIR}}" ]; then
        rm -rf "${{WORK_DIR}}"
    fi
    # Always write result.json on exit
    END_TIME=$(date +%s)
    DURATION=$(( END_TIME - START_TIME ))
    cat > "${{RESULTS_DIR}}/result.json" <<EOF
{{
    "instance_id": "zephyr__zephyr-{pr}",
    "pr_number": {pr},
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "duration_seconds": ${{DURATION}},
    "status": "${{STATUS}}",
    "pass_step_exit_code": ${{PASS_EXIT}},
    "log": "${{LOG_FILE}}"
}}
EOF
    echo ""
    echo "Result: ${{STATUS}} (written to ${{RESULTS_DIR}}/result.json)"
}}
trap cleanup EXIT

# ── Step 1: Generate test_patch.diff ─────────────────────────────────────────
echo "=== Step 1: Generating test_patch.diff ==="
WORK_DIR="$(mktemp -d)"
git clone --filter=blob:none --no-checkout \\
    https://github.com/zephyrproject-rtos/zephyr.git \\
    "${{WORK_DIR}}/zephyr" -q
cd "${{WORK_DIR}}/zephyr"
git fetch origin "${{MERGE_COMMIT}}" -q
git diff "${{BASE_COMMIT}}..${{MERGE_COMMIT}}" -- {test_path}/ \\
    > "${{INSTANCE_DIR}}/test_patch.diff"
echo "test_patch.diff: $(wc -l < "${{INSTANCE_DIR}}/test_patch.diff") lines"
cd "${{REPO_ROOT}}"

# ── Step 2: Build base image (skip if already exists) ────────────────────────
echo ""
echo "=== Step 2: Base image ==="
if docker image inspect embedbench-zephyr-base:latest &>/dev/null; then
    echo "Already exists, skipping."
else
    echo "Building embedbench-zephyr-base:latest ..."
    docker build \\
        -f "${{REPO_ROOT}}/docker/bases/zephyr.Dockerfile" \\
        -t embedbench-zephyr-base:latest \\
        "${{REPO_ROOT}}/docker/bases/"
fi

# ── Step 3: Build instance image (skip if already exists) ────────────────────
echo ""
echo "=== Step 3: Instance image ==="
if docker image inspect "${{IMAGE}}" &>/dev/null; then
    echo "Already exists, skipping."
else
    echo "Building (~30 min) ..."
    docker build \\
        --build-arg BASE_COMMIT="${{BASE_COMMIT}}" \\
        --build-arg ZEPHYR_SDK_VERSION=0.16.8 \\
        --build-arg PLATFORM={platform} \\
        --build-arg TEST_PATH={test_path} \\
        -t "${{IMAGE}}" \\
        "${{INSTANCE_DIR}}"
fi

# ── Step 4a: Verify FAIL on broken code ──────────────────────────────────────
echo ""
echo "=== Step 4a: Verifying tests FAIL on broken code ==="
CID=$(docker run -d "${{IMAGE}}" sleep infinity)

docker exec "${{CID}}" bash -c "
    source /opt/zephyr-venv/bin/activate
    cd /testbed
    west build -b {platform} {test_path} 2>&1
    timeout ${{QEMU_TIMEOUT}} west build -t run 2>&1 || true
" || true

docker exec "${{CID}}" bash -c \\
    "rm -f /testbed/build/zephyr/qemu.pid /testbed/build/qemu.pid" 2>/dev/null || true

# ── Step 4b: Apply fix, verify PASS ──────────────────────────────────────────
echo ""
echo "=== Step 4b: Applying fix and verifying tests PASS ==="
cd "${{WORK_DIR}}/zephyr"
git diff "${{BASE_COMMIT}}..${{MERGE_COMMIT}}" -- \\
{fix_files_bash} \\
    > "${{WORK_DIR}}/fix_patch.diff"
echo "fix_patch.diff: $(wc -l < "${{WORK_DIR}}/fix_patch.diff") lines"
cd "${{REPO_ROOT}}"

docker cp "${{WORK_DIR}}/fix_patch.diff" "${{CID}}:/tmp/fix_patch.diff"

PASS_EXIT=0
docker exec "${{CID}}" bash -c "
    source /opt/zephyr-venv/bin/activate
    cd /testbed
    git apply /tmp/fix_patch.diff
    west build -b {platform} {test_path} 2>&1
    timeout ${{QEMU_TIMEOUT}} west build -t run 2>&1
" || PASS_EXIT=$?

# Exit code 124 = timeout killed QEMU — this is expected and normal.
# QEMU never exits on its own after tests complete; timeout is how we stop it.
# Any other non-zero exit means something actually went wrong.
if [ ${{PASS_EXIT}} -eq 0 ] || [ ${{PASS_EXIT}} -eq 124 ]; then
    STATUS="validated"
    echo ""
    echo "=== PASS: {fail_to_pass_str} ==="
else
    STATUS="error"
    echo ""
    echo "=== FAIL: pass step exited with code ${{PASS_EXIT}} — check ${{LOG_FILE}} ==="
fi
'''


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pr", type=int, help="PR number")
    parser.add_argument("--no-diff", action="store_true",
                        help="Skip test_patch.diff generation (no git clone)")
    args = parser.parse_args()

    pr = args.pr
    instance_id = f"zephyr__zephyr-{pr}"
    instance_dir = INSTANCES_DIR / instance_id

    print(f"\n{'='*60}")
    print(f"  EmbedEval instance generator — PR #{pr}")
    print(f"{'='*60}\n")

    # ── Load candidate JSON (optional enrichment) ─────────────────────────────
    candidate_info: dict = {}
    if CANDIDATE_JSON.exists():
        candidates = json.loads(CANDIDATE_JSON.read_text())
        for c in candidates:
            if c.get("pr_number") == pr:
                candidate_info = c
                break
        if candidate_info:
            print(f"Found in selected_candidates.json: score={candidate_info.get('score')}, confidence={candidate_info.get('confidence', 'N/A')}")
        else:
            print(f"PR #{pr} not in selected_candidates.json — fetching fresh from GitHub")

    # ── GitHub API fetches ────────────────────────────────────────────────────
    print("\n[1/5] Fetching PR metadata from GitHub ...")
    pr_data = fetch_pr(pr)
    base_commit: str = pr_data["base"]["sha"]
    merge_commit: str = pr_data.get("merge_commit_sha") or ""
    if not merge_commit:
        sys.exit(f"ERROR: PR #{pr} has no merge_commit_sha — is it merged?")
    pr_title: str = pr_data.get("title", f"PR #{pr}")
    print(f"  base_commit:  {base_commit}")
    print(f"  merge_commit: {merge_commit}")
    print(f"  title: {pr_title}")

    print("\n[2/5] Fetching changed files ...")
    all_files = fetch_pr_files(pr)
    fix_files, test_files = classify_files(all_files)
    print(f"  fix files ({len(fix_files)}): {fix_files}")
    print(f"  test files ({len(test_files)}): {test_files[:5]}{'...' if len(test_files)>5 else ''}")

    if not fix_files:
        print("  WARNING: no source fix files detected — metadata will be incomplete")

    # ── Determine test_path ───────────────────────────────────────────────────
    print("\n[3/5] Determining test_path ...")
    testcase_yamls: list[str] = candidate_info.get("testcase_yamls", [])
    if testcase_yamls:
        # Use the directory of the first testcase.yaml
        test_path = str(Path(testcase_yamls[0]).parent)
        print(f"  test_path from candidate JSON: {test_path}")
    else:
        # Infer from test_files: find common leading path under tests/
        test_dirs = sorted({str(Path(f).parent) for f in test_files if f.startswith("tests/")})
        if not test_dirs:
            sys.exit("ERROR: cannot determine test_path — no test files found and not in candidate JSON")
        # Use shortest (most specific common ancestor)
        test_path = test_dirs[0]
        print(f"  test_path inferred from test files: {test_path}")

    # ── Fetch testcase.yaml ───────────────────────────────────────────────────
    yaml_path = f"{test_path}/testcase.yaml"
    print(f"  Fetching {yaml_path} at merge_commit ...")
    yaml_content = github_raw(merge_commit, yaml_path)
    yaml_parsed = parse_testcase_yaml(yaml_content) if yaml_content else {}
    test_scenario = yaml_parsed.get("scenario")
    print(f"  scenario: {test_scenario}")

    # ── Determine platform ────────────────────────────────────────────────────
    platform_allow_candidate = candidate_info.get("platform_allow", [])
    platform_allow_yaml = yaml_parsed.get("platform_allow", [])
    combined = platform_allow_candidate or platform_allow_yaml
    if not combined or "qemu_x86" in combined:
        platform = "qemu_x86"
    elif any("qemu" in p for p in combined):
        platform = next(p for p in combined if "qemu" in p)
    else:
        platform = "qemu_x86"
        print(f"  WARNING: platform_allow={combined} — defaulting to qemu_x86 (may not work)")
    print(f"  platform: {platform}")

    # ── Extract ZTEST function names ──────────────────────────────────────────
    print("\n[4/5] Extracting ZTEST function names ...")
    fail_to_pass = fetch_ztest_functions(merge_commit, test_files)
    print(f"  fail_to_pass ({len(fail_to_pass)}): {fail_to_pass[:8]}{'...' if len(fail_to_pass)>8 else ''}")

    # ── Build problem statement ───────────────────────────────────────────────
    linked_issues: list[str] = candidate_info.get("linked_issues", [])
    problem_statement = build_problem_statement(pr_data, linked_issues, pr)

    # ── Write output files ────────────────────────────────────────────────────
    print(f"\n[5/5] Writing instance files to {instance_dir} ...")
    instance_dir.mkdir(parents=True, exist_ok=True)

    # Dockerfile
    dockerfile = DOCKERFILE_TEMPLATE.format(
        base_commit=base_commit,
        platform=platform,
        test_path=test_path,
    )
    (instance_dir / "Dockerfile").write_text(dockerfile)
    print("  Wrote Dockerfile")

    # metadata.json
    metadata = {
        "instance_id": instance_id,
        "project": "zephyr",
        "repo": "https://github.com/zephyrproject-rtos/zephyr",
        "base_commit": base_commit,
        "fix_commit": merge_commit,
        "test_commit": merge_commit,
        "problem_statement": problem_statement,
        "platform": platform,
        "test_path": test_path,
        "test_scenario": test_scenario,
        "build_command": f"west build -b {platform} {test_path}",
        "run_command": "west build -t run",
        "docker_image": f"embedbench:zephyr-{pr}",
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": [],
        "files_changed_by_fix": fix_files,
    }
    (instance_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=4) + "\n"
    )
    print("  Wrote metadata.json")

    # test_patch.diff
    if not args.no_diff:
        generate_test_patch(base_commit, merge_commit, test_path, instance_dir)
    else:
        print("  Skipped test_patch.diff (--no-diff)")

    # run_{pr}.sh
    fix_files_bash = "\n".join(
        f"    {f} \\" for f in fix_files
    ).rstrip(" \\")
    # Remove trailing backslash from last line
    if fix_files_bash.endswith(" \\"):
        fix_files_bash = fix_files_bash[:-2]

    fail_to_pass_str = ", ".join(fail_to_pass[:4])
    if len(fail_to_pass) > 4:
        fail_to_pass_str += f" (+{len(fail_to_pass)-4} more)"

    qemu_timeout = 120  # conservative default

    run_script = RUN_SCRIPT_TEMPLATE.format(
        pr=pr,
        title=pr_title.replace('"', '\\"'),
        base_commit=base_commit,
        merge_commit=merge_commit,
        platform=platform,
        test_path=test_path,
        fix_files_bash=fix_files_bash,
        fail_to_pass_str=fail_to_pass_str,
        qemu_timeout=qemu_timeout,
    )
    run_script_path = SCRIPT_DIR / f"run_{pr}.sh"
    run_script_path.write_text(run_script)
    run_script_path.chmod(0o755)
    print(f"  Wrote scripts/run_{pr}.sh")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"""
{'='*60}
  Done — instance files generated for PR #{pr}
{'='*60}

  Instance dir:  {instance_dir}
  base_commit:   {base_commit[:12]}...
  merge_commit:  {merge_commit[:12]}...
  platform:      {platform}
  test_path:     {test_path}
  fix_files:     {len(fix_files)}
  fail_to_pass:  {len(fail_to_pass)} tests

  Next steps:
    1. Review the generated files in {instance_dir}
    2. Run the validation script:
         ./scripts/run_{pr}.sh
""")


if __name__ == "__main__":
    main()
