import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from skillsbench_private.docker_proxy import get_api_proxy_env, get_docker_run_env_args

REPO_ROOT = Path(os.environ.get("SKILLSBENCH_REPO_ROOT", Path(__file__).resolve().parent))
TASKS_FILE = REPO_ROOT / "skillsbench_phase1_tasks.txt"
RESULTS_FILE = REPO_ROOT / "phase1_claude_results.json"
RUNS_DIR = REPO_ROOT / "phase1_claude_runs"
FAKE_ROOT_BASE = Path("/tmp/skillsbench_root")
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
CLAUDE_BIN = Path(os.environ.get("SKILLSBENCH_CLAUDE_BIN", shutil.which("claude") or "claude"))
CLAUDE_DOCKER_IMAGE = "node:18-alpine"
CLAUDE_TIMEOUT_SEC = int(os.environ.get("CLAUDE_TASK_TIMEOUT_SEC", "600"))
CLAUDE_IDLE_TIMEOUT_SEC = int(os.environ.get("CLAUDE_IDLE_TIMEOUT_SEC", "60"))
CLAUDE_DOCKER_TIMEOUT_SEC = int(os.environ.get("CLAUDE_DOCKER_TIMEOUT_SEC", "300"))
CLAUDE_MAX_RETRIES = int(os.environ.get("CLAUDE_MAX_RETRIES", "3"))
CLAUDE_DOCKER_MEMORY = os.environ.get("CLAUDE_DOCKER_MEMORY", "6g")
CLAUDE_MAX_STEPS = int(os.environ.get("CLAUDE_MAX_STEPS", "20"))
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:3999")

CLAUDE_PROMPT = """You are solving an automated benchmark task from the current working directory.

The task description is in instruction.md.

You may:
- read files
- write files
- execute bash commands
- inspect the repository

Your goal is to produce the exact output artifacts required by the task tests.

Execution strategy:
1. Read instruction.md first.
2. Immediately confirm your current working directory.
3. Immediately check whether environment/skills/ exists.
4. If environment/skills/ exists, list the available skill directories before doing deep exploration elsewhere.
5. Open the most relevant SKILL.md files and use the task-provided skill scripts, code, references, and logic as the preferred solution path.
6. Reuse task-local skill code before inventing a new approach from scratch.
7. Avoid spending early turns reading large raw assets or the full test suite unless needed. Prefer quickly identifying expected output filenames and paths first.
8. After understanding the required outputs, execute the skill scripts or adapt their logic to generate the missing artifacts.
9. If a produced output is missing, incomplete, empty, or in the wrong location, continue working and fix it.

Behavior rules:
- Treat environment/skills/ as the primary source of task-specific solution knowledge.
- If environment/skills/ exists, inspect it before reading large datasets, binaries, videos, or long test files.
- Prefer reading SKILL.md, scripts/, and references/ over raw assets.
- Prefer small targeted reads and focused grep/glob exploration over reading entire large files.
- Do not stop just because you understand the task. Stop only after the required output artifacts have been created in the expected paths.
- If you inspect tests, use them mainly to confirm exact output artifact names, paths, and formats.

Final checklist before stopping:
- What output files are required?
- Does each required file exist now?
- Is each file in the expected path?
- If any required artifact is missing, keep working.
"""


def load_task_instruction(task_path: Path) -> str:
    instruction_path = task_path / "instruction.md"
    return instruction_path.read_text()


def is_citation_chunk_task(task_path: Path) -> bool:
    instruction_text = load_task_instruction(task_path)
    return "/root/test.bib" in instruction_text and "/root/answer.json" in instruction_text


def build_task_prompt(task_path: Path) -> str:
    instruction_text = load_task_instruction(task_path)
    if is_citation_chunk_task(task_path):
        segmented_rules = """

CRITICAL OPERATIONAL RULES:
1. DO NOT read /root/test.bib using the 'Read' tool (it is too large).
2. You MUST process the file in 100-line chunks using the 'Bash' tool:
   - Chunk 1: `sed -n '1,100p' /root/test.bib`
   - Chunk 2: `sed -n '101,200p' /root/test.bib`
   - Chunk 3: `sed -n '201,300p' /root/test.bib`
   - Continue as needed.
3. After each chunk, analyze the citations and maintain a list of fake titles.
4. Continue until you reach the end of the file or find the requested citations.
5. FINAL OUTPUT:
   - ONLY raw JSON: {"fake_citations": ["..."]}
   - No reasoning.
   - No conversational text.
   - No quoting file content.
"""
        return f"{instruction_text}\n{segmented_rules}"
    return instruction_text


def load_tasks() -> list[str]:
    return [
        line.strip()
        for line in TASKS_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def ensure_task_runtime_layout(task_path: Path) -> None:
    (task_path / "logs" / "verifier").mkdir(parents=True, exist_ok=True)
    (task_path / "tests").mkdir(parents=True, exist_ok=True)
    env_dir = task_path / "environment"
    if env_dir.exists():
        for item in env_dir.iterdir():
            if item.name == "skills":
                continue
            target = task_path / item.name
            if target.exists():
                continue
            target.symlink_to(item, target_is_directory=item.is_dir())


def prepare_fake_root_workspace(source_task_path: Path) -> Path:
    fake_root = FAKE_ROOT_BASE / source_task_path.name
    if fake_root.exists():
        shutil.rmtree(fake_root)
    fake_root.mkdir(parents=True, exist_ok=True)

    for item in source_task_path.iterdir():
        if item.name == "logs":
            continue
        target = fake_root / item.name
        target.symlink_to(item, target_is_directory=item.is_dir())

    env_dir = source_task_path / "environment"
    if env_dir.exists():
        for item in env_dir.iterdir():
            if item.name in {"skills", "Dockerfile"}:
                continue
            target = fake_root / item.name
            if target.exists():
                continue
            target.symlink_to(item, target_is_directory=item.is_dir())

    (fake_root / "logs" / "verifier").mkdir(parents=True, exist_ok=True)
    return fake_root


def normalize_event_name(event: dict) -> str:
    event_type = str(event.get("type") or "").strip()
    subtype = str(event.get("subtype") or "").strip()
    if event_type and subtype:
        return f"{event_type}.{subtype}"
    return event_type


def parse_metrics(stdout: str) -> tuple[int, int, int]:
    llm_calls = 0
    tool_calls = 0
    reasoning_steps = 0

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_name = normalize_event_name(event)
        if event_name == "assistant.message":
            llm_calls += 1
        elif event.get("type") == "assistant":
            llm_calls += 1

        if event_name == "tool.start":
            tool_calls += 1
        elif event.get("type") in {"tool_start", "tool-use", "tool_use"}:
            tool_calls += 1
        elif event_name == "content_block_start":
            block = event.get("content_block") or {}
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls += 1

        if event_name == "plan.step":
            reasoning_steps += 1
        elif event.get("type") == "plan" and event.get("subtype") == "step":
            reasoning_steps += 1

    return llm_calls, tool_calls, reasoning_steps


def update_metrics_from_event(event: dict, metrics: dict[str, int]) -> None:
    event_name = normalize_event_name(event)
    if event_name == "assistant.message":
        metrics["llm_calls"] += 1
    elif event.get("type") == "assistant":
        metrics["llm_calls"] += 1

    if event_name == "tool.start":
        metrics["tool_calls"] += 1
    elif event.get("type") in {"tool_start", "tool-use", "tool_use"}:
        metrics["tool_calls"] += 1
    elif event_name == "content_block_start":
        block = event.get("content_block") or {}
        if isinstance(block, dict) and block.get("type") == "tool_use":
            metrics["tool_calls"] += 1
    elif event.get("type") == "assistant":
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                metrics["tool_calls"] += 1

    if event_name == "plan.step":
        metrics["reasoning_steps"] += 1
    elif event.get("type") == "plan" and event.get("subtype") == "step":
        metrics["reasoning_steps"] += 1
    elif event.get("type") == "assistant":
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                metrics["reasoning_steps"] += 1
                break


def _reader_thread(stream, stream_name: str, out_queue: queue.Queue) -> None:
    try:
        for line in stream:
            out_queue.put((stream_name, line))
    finally:
        out_queue.put((stream_name, None))


def build_claude_env() -> dict[str, str]:
    runtime_bin = CLAUDE_BIN.parent if CLAUDE_BIN.parent != Path(".") else Path("/usr/local/bin")
    env = {
        "PATH": f"{runtime_bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "CLAUDE_CONFIG_DIR": "/tmp/claude_clean",
        "ANTHROPIC_BASE_URL": ANTHROPIC_BASE_URL,
        "ANTHROPIC_API_KEY": "local",
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }
    env.update(get_api_proxy_env())
    for key in ("LANG", "LC_ALL", "USER", "LOGNAME"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def build_host_claude_cmd(prompt: str) -> list[str]:
    return [
        str(CLAUDE_BIN),
        "--dangerously-skip-permissions",
        "--setting-sources",
        "local",
        "--model",
        CLAUDE_MODEL,
        "--output-format",
        "stream-json",
        "--verbose",
        "-p",
        prompt,
    ]


def build_docker_claude_cmd(task_dir: str, prompt: str, image: str, run_id: str) -> list[str]:
    claude_mjs = str(CLAUDE_BIN.resolve())
    claude_cmd = [
        "node",
        "/usr/local/bin/claude.mjs",
        "--dangerously-skip-permissions",
        "--setting-sources",
        "local",
        "--model",
        CLAUDE_MODEL,
        "--output-format",
        "stream-json",
        "--verbose",
        "-p",
        prompt,
    ]
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        f"--memory={CLAUDE_DOCKER_MEMORY}",
        f"--memory-swap={CLAUDE_DOCKER_MEMORY}",
        "-u",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{Path(task_dir) / 'environment'}:/root",
        "-v",
        f"{claude_mjs}:/usr/local/bin/claude.mjs:ro",
        "-w",
        "/root",
        "--network",
        "host",
        *get_docker_run_env_args(),
        "-e",
        f"ANTHROPIC_BASE_URL={ANTHROPIC_BASE_URL}",
        "-e",
        "ANTHROPIC_API_KEY=local",
        "-e",
        f"CLAUDE_CONFIG_DIR=/tmp/claude_{run_id}",
        image,
    ] + claude_cmd
    return docker_cmd


def ensure_valid_answer_json(task_path: Path, runtime: str) -> Path:
    if runtime == "docker":
        answer_path = task_path / "environment" / "answer.json"
    else:
        answer_path = task_path / "answer.json"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    if not answer_path.exists() or not answer_path.read_text().strip():
        answer_path.write_text('{"fake_citations":[]}\n')
    try:
        json.loads(answer_path.read_text())
    except json.JSONDecodeError:
        answer_path.write_text('{"fake_citations":[]}\n')
    return answer_path


def mirror_docker_answer(task_path: Path) -> None:
    docker_answer = task_path / "environment" / "answer.json"
    host_answer = task_path / "answer.json"
    if docker_answer.exists():
        host_answer.write_text(docker_answer.read_text())


def get_artifact_answer_path(task_path: Path, runtime: str) -> Path:
    if runtime == "docker":
        return task_path / "environment" / "answer.json"
    return task_path / "answer.json"


def is_valid_json_file(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        json.loads(path.read_text())
        return True
    except Exception:
        return False


def detect_read_test_bib(stdout: str) -> bool:
    return (
        '"file_path":"/root/test.bib"' in stdout
        or '"file_path": "/root/test.bib"' in stdout
        or "Read(/root/test.bib)" in stdout
        or "Read(\"/root/test.bib\")" in stdout
    )


def evaluate_task_outcome(
    task_path: Path,
    runtime: str,
    return_code: int,
    stdout: str,
    stderr: str,
    verifier_passed: bool,
) -> tuple[bool, dict[str, object]]:
    artifact_path = get_artifact_answer_path(task_path, runtime)
    artifact_valid = is_valid_json_file(artifact_path)
    rc_nonzero = return_code != 0
    task_success_override = artifact_valid and verifier_passed
    execution_flags: dict[str, object] = {
        "return_code": return_code,
        "artifact_path": str(artifact_path),
        "artifact_valid": artifact_valid,
        "verifier_passed": verifier_passed,
        "rc_nonzero": rc_nonzero,
        "rc_nonzero_overridden": bool(task_success_override and rc_nonzero),
        "read_test_bib_detected": detect_read_test_bib(stdout),
        "execution_health": "degraded" if rc_nonzero else "healthy",
        "retry_signal_detected": should_retry(stdout, stderr, return_code),
    }
    return task_success_override, execution_flags


def should_retry(stdout: str, stderr: str, return_code: int) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return (
        "api error: terminated" in text
        or "connection refused" in text
        or "connection reset" in text
        or "timed out" in text
        or return_code == 124
    )


def run_claude_once(
    task_path: Path,
    run_dir: Path,
    *,
    prompt: str | None = None,
    model_name: str | None = None,
    runtime: str = "host",
    docker_image: str = CLAUDE_DOCKER_IMAGE,
) -> tuple[int, str, str, float, int, int, int]:
    del model_name
    config_dir = Path("/tmp/claude_clean")
    if config_dir.exists():
        shutil.rmtree(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "debug").mkdir(parents=True, exist_ok=True)
    (config_dir / "projects" / "-app").mkdir(parents=True, exist_ok=True)
    (config_dir / "shell-snapshots").mkdir(parents=True, exist_ok=True)
    (config_dir / "statsig").mkdir(parents=True, exist_ok=True)
    (config_dir / "todos").mkdir(parents=True, exist_ok=True)
    env = build_claude_env()
    prompt = prompt or build_task_prompt(task_path)

    if runtime == "docker":
        cmd = build_docker_claude_cmd(str(task_path), prompt, docker_image, run_dir.name)
        proc_env = None
        proc_cwd = None
    else:
        cmd = build_host_claude_cmd(prompt)
        proc_env = env
        proc_cwd = task_path
    print(f"[claude] cmd={' '.join(cmd[:-1])} -p <FULL_PROMPT>", flush=True)
    print(f"[claude] ANTHROPIC_BASE_URL={env['ANTHROPIC_BASE_URL']}", flush=True)
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=proc_cwd,
        env=proc_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_path = run_dir / "claude_stdout.txt"
    stderr_path = run_dir / "claude_stderr.txt"
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    metrics = {"llm_calls": 0, "tool_calls": 0, "reasoning_steps": 0}
    output_queue: queue.Queue = queue.Queue()
    threads = [
        threading.Thread(target=_reader_thread, args=(proc.stdout, "stdout", output_queue), daemon=True),
        threading.Thread(target=_reader_thread, args=(proc.stderr, "stderr", output_queue), daemon=True),
    ]
    for thread in threads:
        thread.start()

    stdout_open = 0
    stderr_open = 0
    last_event_at = time.monotonic()
    timed_out = False
    idle_timed_out = False
    timeout_limit = CLAUDE_DOCKER_TIMEOUT_SEC if runtime == "docker" else CLAUDE_TIMEOUT_SEC

    with stdout_path.open("w") as stdout_file, stderr_path.open("w") as stderr_file:
        stdout_open = 1
        stderr_open = 1
        while stdout_open or stderr_open:
            now = time.monotonic()
            if now - started > timeout_limit:
                timed_out = True
                proc.terminate()
                break
            if now - last_event_at > CLAUDE_IDLE_TIMEOUT_SEC:
                idle_timed_out = True
                proc.terminate()
                break

            try:
                stream_name, line = output_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if line is None:
                if stream_name == "stdout":
                    stdout_open = 0
                else:
                    stderr_open = 0
                continue

            if stream_name == "stdout":
                stdout_lines.append(line)
                stdout_file.write(line)
                stdout_file.flush()
                stripped = line.strip()
                if stripped:
                    last_event_at = time.monotonic()
                if stripped.startswith("{"):
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    update_metrics_from_event(event, metrics)
            else:
                stderr_lines.append(line)
                stderr_file.write(line)
                stderr_file.flush()

        if timed_out or idle_timed_out:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return_code = proc.wait()

    runtime_sec = time.monotonic() - started
    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines).strip()
    if timed_out:
        stderr = (stderr + f"\nClaude timed out after {timeout_limit} seconds.\n").strip()
        return_code = 124
    elif idle_timed_out:
        stderr = (stderr + f"\nClaude received no events for {CLAUDE_IDLE_TIMEOUT_SEC} seconds.\n").strip()
        return_code = 124

    return (
        return_code,
        stdout,
        stderr,
        runtime_sec,
        metrics["llm_calls"],
        metrics["tool_calls"],
        metrics["reasoning_steps"],
    )


def run_claude(
    task_path: Path,
    run_dir: Path,
    *,
    prompt: str | None = None,
    model_name: str | None = None,
    runtime: str = "host",
    docker_image: str = CLAUDE_DOCKER_IMAGE,
) -> tuple[int, str, str, float, int, int, int]:
    last_result: tuple[int, str, str, float, int, int, int] | None = None
    for attempt in range(CLAUDE_MAX_RETRIES + 1):
        if attempt:
            time.sleep(2 ** (attempt - 1))
        last_result = run_claude_once(
            task_path,
            run_dir,
            prompt=prompt,
            model_name=model_name,
            runtime=runtime,
            docker_image=docker_image,
        )
        return_code, stdout, stderr, *_ = last_result
        if runtime == "docker" and (task_path / "environment" / "answer.json").exists():
            mirror_docker_answer(task_path)
        if not should_retry(stdout, stderr, return_code):
            break
    ensure_valid_answer_json(task_path, runtime)
    if runtime == "docker":
        mirror_docker_answer(task_path)
    assert last_result is not None
    return last_result


def run_verification(task_path: Path) -> tuple[bool, str, str, str]:
    env = os.environ.copy()
    env["TASK_ROOT"] = str(task_path)
    verifier_code = r"""
import builtins
import os
import sys
from pathlib import Path

import pytest

task_root = Path(os.environ["TASK_ROOT"]).resolve()

def remap(path):
    raw = os.fspath(path)
    if raw == "/root" or raw.startswith("/root/"):
        suffix = raw[len("/root"):].lstrip("/")
        return str(task_root / suffix) if suffix else str(task_root)
    if raw == "/app" or raw.startswith("/app/"):
        suffix = raw[len("/app"):].lstrip("/")
        return str(task_root / suffix) if suffix else str(task_root)
    if raw == "/outputs" or raw.startswith("/outputs/"):
        suffix = raw[len("/outputs"):].lstrip("/")
        base = task_root / "outputs"
        return str(base / suffix) if suffix else str(base)
    if raw == "/tests" or raw.startswith("/tests/"):
        suffix = raw[len("/tests"):].lstrip("/")
        base = task_root / "tests"
        return str(base / suffix) if suffix else str(base)
    if raw == "/logs" or raw.startswith("/logs/"):
        suffix = raw[len("/logs"):].lstrip("/")
        base = task_root / "logs"
        return str(base / suffix) if suffix else str(base)
    return raw

_open = builtins.open
_exists = os.path.exists
_isfile = os.path.isfile
_isdir = os.path.isdir
_stat = os.stat

def patched_open(file, *args, **kwargs):
    return _open(remap(file), *args, **kwargs)

builtins.open = patched_open
os.path.exists = lambda p: _exists(remap(p))
os.path.isfile = lambda p: _isfile(remap(p))
os.path.isdir = lambda p: _isdir(remap(p))
os.stat = lambda p, *args, **kwargs: _stat(remap(p), *args, **kwargs)

test_file = task_root / "tests" / "test_outputs.py"
log_file = task_root / "logs" / "verifier" / "reward.txt"
rc = pytest.main([str(test_file), "-rA", "-v"])
log_file.write_text("1" if rc == 0 else "0")
raise SystemExit(rc)
"""
    proc = subprocess.run(
        ["python", "-c", verifier_code],
        cwd=task_path,
        env=env,
        capture_output=True,
        text=True,
    )

    reward_path = task_path / "logs" / "verifier" / "reward.txt"
    if reward_path.exists():
        reward = reward_path.read_text().strip()
        success = reward == "1"
    else:
        success = proc.returncode == 0

    return success, proc.stdout, proc.stderr, "pytest"


def write_logs(run_dir: Path, stdout: str, stderr: str, verification: tuple[bool, str, str, str]) -> None:
    success, verify_stdout, verify_stderr, verify_mode = verification
    if not (run_dir / "claude_stdout.txt").exists():
        (run_dir / "claude_stdout.txt").write_text(stdout)
    if not (run_dir / "claude_stderr.txt").exists():
        (run_dir / "claude_stderr.txt").write_text(stderr)
    interaction_log = "\n".join(
        [
            "== Claude stdout ==",
            stdout,
            "",
            "== Claude stderr ==",
            stderr,
            "",
            f"== Verification ({verify_mode}) ==",
            f"success={success}",
            "",
            "== Verification stdout ==",
            verify_stdout,
            "",
            "== Verification stderr ==",
            verify_stderr,
        ]
    )
    (run_dir / "interaction_log.txt").write_text(interaction_log)


def run_task(task_name: str) -> dict:
    task_path = REPO_ROOT / "tasks" / task_name
    run_dir = RUNS_DIR / task_name
    run_dir.mkdir(parents=True, exist_ok=True)
    ensure_task_runtime_layout(task_path)

    (
        claude_exit_code,
        stdout,
        stderr,
        runtime_sec,
        llm_calls,
        tool_calls,
        reasoning_steps,
    ) = run_claude(task_path, run_dir)
    verification = run_verification(task_path)
    write_logs(run_dir, stdout, stderr, verification)

    return {
        "task": task_name,
        "success": verification[0],
        "runtime_sec": runtime_sec,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "reasoning_steps": reasoning_steps,
        "claude_exit_code": claude_exit_code,
    }


def main() -> int:
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks()[:1]
    results = []
    for task_name in tasks:
        print(f"[run] {task_name}", flush=True)
        results.append(run_task(task_name))

    RESULTS_FILE.write_text(json.dumps(results, indent=2))

    total_tasks = len(results)
    success_count = sum(1 for item in results if item["success"])
    success_rate = success_count / total_tasks if total_tasks else 0.0
    print(json.dumps({"total_tasks": total_tasks, "success_count": success_count, "success_rate": success_rate}, indent=2))
    if results:
        print(json.dumps(results[0], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
