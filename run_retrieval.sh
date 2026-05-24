#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/skillsbench_env.sh"
skillsbench_load_api_env "${SKILLSBENCH_LEGACY_API_SCRIPT:-${HOME}/api_yun_key.sh}"
skillsbench_prepare_runtime

RUN_TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR="${OUT_DIR:-${SKILLSBENCH_ARTIFACT_ROOT}/retrieval_codex_${RUN_TS}}"
mkdir -p "$OUT_DIR"

TASKS_FILE="${TASKS_FILE:-${SKILLSBENCH_REPO_ROOT}/tasks_10_diverse.txt}"  #"tasks_full.txt" #
MASTER_LOG="$OUT_DIR/master.log"
SUMMARY_JSON="$OUT_DIR/summary.jsonl"
SUMMARY_TXT="$OUT_DIR/summary.txt"
START_MARKER="$OUT_DIR/.run_start"
RUNS_ROOT="${RUNS_ROOT:-${SKILLSBENCH_RUNS_ROOT}}"
JOBS_DIR="${JOBS_DIR:-${SKILLSBENCH_REPO_ROOT}/jobs}"

mapfile -t TASKS < <(grep -v '^[[:space:]]*#' "$TASKS_FILE" | sed '/^[[:space:]]*$/d')
touch "$MASTER_LOG" "$SUMMARY_JSON" "$SUMMARY_TXT" "$START_MARKER"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$MASTER_LOG" >/dev/null
}

find_run_dir() {
  local task="$1"
  find "$RUNS_ROOT" -maxdepth 1 -type d -name "*__${task}" -newer "$START_MARKER" -printf '%T@ %p\n' \
    | sort -n \
    | tail -n 1 \
    | cut -d' ' -f2-
}

link_task_logs_once() {
  local task run_dir
  for task in "${TASKS[@]}"; do
    run_dir="$(find_run_dir "$task")"
    [ -n "$run_dir" ] || continue
    [ -f "$run_dir/harbor_stdout.log" ] || continue
    ln -sfn "$run_dir/harbor_stdout.log" "$OUT_DIR/${task}.log"
  done
}

watch_task_logs() {
  while true; do
    link_task_logs_once
    sleep 2
  done
}

summarize_one() {
  local task="$1"
  python3 - <<PY
import json
from pathlib import Path

task = "$task"
run_dir_str = r"""$(find_run_dir "$task")"""
run_dir = Path(run_dir_str) if run_dir_str else None
job_name = run_dir.name if run_dir else None
job = Path("$JOBS_DIR") / job_name if job_name else None
trial_dirs = [p for p in job.iterdir() if p.is_dir() and "__" in p.name] if job and job.exists() else []
trial = trial_dirs[0] if len(trial_dirs) == 1 else None

reward = None
job_mean = None
exception = None
setup_success = bool(trial and (trial / "agent/setup/return-code.txt").exists())
entered_loop = bool(trial and (trial / "agent/codex.txt").exists())
command1_rc = bool(trial and (trial / "agent/command-1/return-code.txt").exists())
verifier_started = bool(trial and (trial / "verifier").exists() and any((trial / "verifier").iterdir()))
trial_result = bool(trial and (trial / "result.json").exists())
job_result = bool(job and (job / "result.json").exists())

if trial and (trial / "exception.txt").exists():
    exception = (trial / "exception.txt").read_text(errors="replace")[:1200]
elif run_dir and (run_dir / "harbor_stderr.log").exists():
    stderr_text = (run_dir / "harbor_stderr.log").read_text(errors="replace")
    if stderr_text.strip():
        exception = stderr_text[-1200:]

if trial_result:
    try:
        data = json.loads((trial / "result.json").read_text())
        reward = ((data.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    except Exception as e:
        reward = f"PARSE_ERROR: {e}"

if job_result:
    try:
        data = json.loads((job / "result.json").read_text())
        job_mean = (data.get("stats") or {}).get("mean")
    except Exception as e:
        job_mean = f"PARSE_ERROR: {e}"

row = {
    "task": task,
    "run_dir": str(run_dir) if run_dir else None,
    "job_name": job_name,
    "job_path": str(job) if job else None,
    "trial_path": str(trial) if trial else None,
    "setup_success": setup_success,
    "entered_loop": entered_loop,
    "command1_return_code": command1_rc,
    "verifier_started": verifier_started,
    "trial_result": trial_result,
    "job_result": job_result,
    "reward": reward,
    "job_mean": job_mean,
    "exception": exception,
}
print(json.dumps(row, ensure_ascii=False))
PY
}

log "RUN_TS=$RUN_TS"
log "OUT_DIR=$OUT_DIR"
log "MASTER_LOG=$MASTER_LOG"
log "SUMMARY_JSON=$SUMMARY_JSON"
log "SUMMARY_TXT=$SUMMARY_TXT"
log "TASK_COUNT=${#TASKS[@]}"

watch_task_logs &
WATCHER_PID=$!
trap 'kill "$WATCHER_PID" 2>/dev/null || true' EXIT

set +e
PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
"$CONDA_PREFIX/bin/python" batch_run_with_retrieval.py \
  --tasks-file "$TASKS_FILE" \
  --agent codex \
  --jobs 1 \
  2>&1 | tee -a "$MASTER_LOG"
BATCH_RC=$?
set -e

link_task_logs_once

for task in "${TASKS[@]}"; do
  one_json="$(summarize_one "$task")"
  echo "$one_json" >> "$SUMMARY_JSON"

  python3 - <<PY >> "$SUMMARY_TXT"
import json
row = json.loads('''$one_json''')
print("------------------------------------------------------------")
print(f"TASK: {row['task']}")
print(f"RUN_DIR: {row['run_dir']}")
print(f"JOB_NAME: {row['job_name']}")
print(f"JOB_PATH: {row['job_path']}")
print(f"TRIAL_PATH: {row['trial_path']}")
print(f"SETUP_SUCCESS: {row['setup_success']}")
print(f"ENTERED_LOOP: {row['entered_loop']}")
print(f"COMMAND1_RETURN_CODE: {row['command1_return_code']}")
print(f"VERIFIER_STARTED: {row['verifier_started']}")
print(f"TRIAL_RESULT: {row['trial_result']}")
print(f"JOB_RESULT: {row['job_result']}")
print(f"REWARD: {row['reward']}")
print(f"JOB_MEAN: {row['job_mean']}")
print(f"EXCEPTION: {row['exception']}")
print("------------------------------------------------------------")
PY
done

python3 - <<PY >> "$SUMMARY_TXT"
import json
from pathlib import Path

summary_file = Path("$SUMMARY_JSON")
rows = [json.loads(line) for line in summary_file.read_text().splitlines() if line.strip()]

reward_ones = sum(1 for r in rows if r.get("reward") == 1.0)
reward_zeros = sum(1 for r in rows if r.get("reward") == 0.0)
reward_partial = sum(1 for r in rows if isinstance(r.get("reward"), (int, float)) and r.get("reward") not in (0.0, 1.0))
reward_missing = sum(1 for r in rows if r.get("reward") is None)
setup_fail = sum(1 for r in rows if not r.get("setup_success"))
loop_fail = sum(1 for r in rows if not r.get("entered_loop"))
cmd1_missing = sum(1 for r in rows if not r.get("command1_return_code"))
verifier_missing = sum(1 for r in rows if not r.get("verifier_started"))

print("=============== FINAL SUMMARY ===============")
print("BATCH_EXIT_CODE: $BATCH_RC")
print(f"TOTAL_TASKS: {len(rows)}")
print(f"REWARD_EQ_1: {reward_ones}")
print(f"REWARD_EQ_0: {reward_zeros}")
print(f"REWARD_PARTIAL: {reward_partial}")
print(f"REWARD_NULL_OR_MISSING: {reward_missing}")
print(f"SETUP_FAIL_COUNT: {setup_fail}")
print(f"ENTER_LOOP_FAIL_COUNT: {loop_fail}")
print(f"COMMAND1_RC_MISSING_COUNT: {cmd1_missing}")
print(f"VERIFIER_MISSING_COUNT: {verifier_missing}")
print()
print("PER_TASK:")
for r in rows:
    print(
        f"- {r['task']}: "
        f"run_dir={r['run_dir']}, "
        f"setup={r['setup_success']}, "
        f"loop={r['entered_loop']}, "
        f"cmd1={r['command1_return_code']}, "
        f"verifier={r['verifier_started']}, "
        f"reward={r['reward']}, "
        f"exception={r['exception']}"
    )
PY

echo
echo "DONE"
echo "BATCH_EXIT_CODE=$BATCH_RC"
echo "MASTER_LOG=$MASTER_LOG"
echo "SUMMARY_JSON=$SUMMARY_JSON"
echo "SUMMARY_TXT=$SUMMARY_TXT"
echo "OUT_DIR=$OUT_DIR"

exit "$BATCH_RC"
