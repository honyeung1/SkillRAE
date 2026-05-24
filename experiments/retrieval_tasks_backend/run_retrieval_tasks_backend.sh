#!/usr/bin/env bash

set -euo pipefail

SKILLSBENCH_RESPECT_PRESET_API_KEYS="${SKILLSBENCH_RESPECT_PRESET_API_KEYS:-1}"
if [ "$SKILLSBENCH_RESPECT_PRESET_API_KEYS" = "1" ]; then
  if [ -v GOOGLE_API_KEY ]; then
    _SBR_PRESET_GOOGLE_API_KEY="$GOOGLE_API_KEY"
    _SBR_PRESET_GOOGLE_API_KEY_SET=1
  fi
  if [ -v OPENAI_API_KEY ]; then
    _SBR_PRESET_OPENAI_API_KEY="$OPENAI_API_KEY"
    _SBR_PRESET_OPENAI_API_KEY_SET=1
  fi
  if [ -v GEMINI_API_KEY ]; then
    _SBR_PRESET_GEMINI_API_KEY="$GEMINI_API_KEY"
    _SBR_PRESET_GEMINI_API_KEY_SET=1
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/skillsbench_env.sh"
skillsbench_load_api_env "${SKILLSBENCH_LEGACY_API_SCRIPT:-${HOME}/api_yun_key.sh}"

if [ "$SKILLSBENCH_RESPECT_PRESET_API_KEYS" = "1" ]; then
  if [ "${_SBR_PRESET_GOOGLE_API_KEY_SET-0}" = "1" ]; then
    export GOOGLE_API_KEY="$_SBR_PRESET_GOOGLE_API_KEY"
  fi
  if [ "${_SBR_PRESET_OPENAI_API_KEY_SET-0}" = "1" ]; then
    export OPENAI_API_KEY="$_SBR_PRESET_OPENAI_API_KEY"
  fi
  if [ "${_SBR_PRESET_GEMINI_API_KEY_SET-0}" = "1" ]; then
    export GEMINI_API_KEY="$_SBR_PRESET_GEMINI_API_KEY"
  fi
fi

: "${IO_CGROUP_ENABLE:=0}"
: "${IO_CGROUP_STRICT:=1}"
: "${IO_CGROUP_ADAPTIVE_ENABLE:=0}"
: "${IO_ADAPTIVE_INTERVAL_SECONDS:=5}"
: "${IO_ADAPTIVE_MIN_WBPS:=10485760}"
: "${IO_ADAPTIVE_INITIAL_WBPS:=12582912}"
: "${IO_ADAPTIVE_MAX_WBPS:=25165824}"
: "${IO_ADAPTIVE_DOWN_PCT:=25}"
: "${IO_ADAPTIVE_UP_PCT:=10}"
: "${IO_ADAPTIVE_PRESSURE_HIGH_FULL_AVG10:=3.0}"
: "${IO_ADAPTIVE_PRESSURE_EMERGENCY_FULL_AVG10:=5.0}"
: "${IO_ADAPTIVE_W_AWAIT_HIGH_MS:=30}"
: "${IO_ADAPTIVE_W_AWAIT_EMERGENCY_MS:=80}"
: "${IO_ADAPTIVE_HEALTHY_FULL_AVG10:=0.8}"
: "${IO_ADAPTIVE_HEALTHY_W_AWAIT_MS:=8}"
: "${IO_ADAPTIVE_HEALTHY_SAMPLES_FOR_UP:=4}"
: "${IO_ADAPTIVE_HEALTHY_SECONDS_TO_EXIT_EMERGENCY:=30}"
: "${IO_GUARD_ENABLE:=0}"
: "${IO_GUARD_PATH:=/mnt/data}"
: "${IO_GUARD_SAMPLE_SECONDS:=5}"
: "${IO_GUARD_STABLE_SAMPLES:=2}"
: "${IO_GUARD_MAX_WAIT_SECONDS:=0}"
: "${IO_GUARD_SOME_AVG10_MAX:=1.0}"
: "${IO_GUARD_SOME_AVG60_MAX:=2.0}"
: "${IO_GUARD_FULL_AVG10_MAX:=0.2}"
: "${IO_GUARD_FULL_AVG60_MAX:=0.5}"
: "${IO_GUARD_DEVICE_W_AWAIT_MS_MAX:=999999}"
: "${IO_GUARD_DEVICE_UTIL_PCT_MAX:=100}"
: "${IO_GUARD_DEVICE_QDEPTH_MAX:=999999}"
: "${IO_GUARD_HARD_SOME_AVG10_MAX:=999999}"
: "${IO_GUARD_HARD_SOME_AVG60_MAX:=999999}"
: "${IO_GUARD_HARD_FULL_AVG10_MAX:=999999}"
: "${IO_GUARD_HARD_FULL_AVG60_MAX:=999999}"
: "${IO_GUARD_FALLBACK_TO_GLOBAL_IF_DEVICE_MISSING:=1}"
: "${IO_GUARD_MIN_FREE_GB:=50}"
: "${IO_TEARDOWN_COOLDOWN_ENABLE:=1}"
: "${IO_TEARDOWN_HEALTHY_FULL_AVG10:=1.0}"
: "${IO_TEARDOWN_HEALTHY_W_AWAIT_MS:=12}"
: "${IO_TEARDOWN_HEALTHY_SAMPLES:=3}"
: "${IO_TEARDOWN_MAX_WAIT_SECONDS:=120}"
: "${IO_TEST_FORCE_TEARDOWN_FAILURES:=0}"
: "${IO_DEFER_ON_COOLDOWN_FAILURE:=1}"
: "${IO_DEFER_MAX_ATTEMPTS:=3}"
: "${RESUME_EXISTING_ROWS:=1}"
: "${LAUNCH_STAGGER_SECONDS:=10}"
: "${MODE_STARTUP_STAGGER_SECONDS:=0}"
: "${MODE_STARTUP_BATCH_SIZE:=1}"
: "${SKILLSBENCH_ABORT_ON_SETUP_ENVIRONMENT_ISSUE:=0}"
: "${LOCAL_SCRATCH_ROOT:=}"
: "${LOCAL_SCRATCH_CLEANUP:=1}"
: "${CODEX_PROVIDER_PREFLIGHT_TIMEOUT_SECONDS:=20}"
: "${CODEX_PROVIDER_PREFLIGHT_DISABLE:=0}"
: "${TIMEOUT_MULTIPLIER:=1.0}"
: "${COMMAND1_TIMEOUT_SECONDS:=1200}"
: "${COMMAND1_TIMEOUT_KILL_SECONDS:=30}"
: "${COMMAND1_TIMEOUT_RC:=124}"
: "${COORDINATOR_VARIANT:=A0}"
: "${FRONT_PACKET_BUDGET:=384}"
: "${SKILLSBENCH_TASK_CONTRACT_GUARD_ENABLE:=1}"
: "${GEMINI_CLI_AGENT_TIMEOUT_MULTIPLIER:=1.0}"

# ---------------------------------------------------------------------------
# WARNING: Core retrieval-backend stability locks.
#
# These defaults are method-infrastructure fixes, not ordinary experiment knobs:
# 1. state consistency + fixed seed prevents lost/ambiguous command state.
# 2. contract closure improves output-contract/verifier alignment across modes.
#
# Do not let wrappers or ad-hoc shell environments silently override them.  For
# an intentional ablation only, set SKILLSBENCH_ALLOW_CORE_LOCK_OVERRIDE=1 and
# then pass the specific SKILLSBENCH_* value you want to test.
# ---------------------------------------------------------------------------
: "${SKILLSBENCH_ALLOW_CORE_LOCK_OVERRIDE:=0}"
if [ "$SKILLSBENCH_ALLOW_CORE_LOCK_OVERRIDE" = "1" ]; then
  : "${SKILLSBENCH_FIXED_SEED:=42}"
  : "${SKILLSBENCH_STATE_CONSISTENCY_OVERLAY:=1}"
  : "${SKILLSBENCH_STATE_CONSISTENCY_VERSION:=v2_default}"
  : "${SKILLSBENCH_FORCE_STATE_CONSISTENCY_OVERLAY:=${SKILLSBENCH_STATE_CONSISTENCY_OVERLAY}}"
  : "${SKILLSBENCH_FORCE_STATE_CONSISTENCY_VERSION:=${SKILLSBENCH_STATE_CONSISTENCY_VERSION}}"
  : "${SKILLSBENCH_CONTRACT_CLOSURE_ENABLE:=1}"
  : "${SKILLSBENCH_CONTRACT_CLOSURE_VERSION:=v1}"
else
  SKILLSBENCH_FIXED_SEED=42
  SKILLSBENCH_STATE_CONSISTENCY_OVERLAY=1
  SKILLSBENCH_STATE_CONSISTENCY_VERSION=v2_default
  SKILLSBENCH_FORCE_STATE_CONSISTENCY_OVERLAY=1
  SKILLSBENCH_FORCE_STATE_CONSISTENCY_VERSION=v2_default
  SKILLSBENCH_CONTRACT_CLOSURE_ENABLE=1
  SKILLSBENCH_CONTRACT_CLOSURE_VERSION=v1
fi
EXPERIMENT_SEED="$SKILLSBENCH_FIXED_SEED"
SEED="$SKILLSBENCH_FIXED_SEED"
PYTHONHASHSEED="$SKILLSBENCH_FIXED_SEED"
: "${SKILLSBENCH_CONTRACT_CLOSURE_MAX_CAUTIONS:=8}"
: "${SKILLSBENCH_CONTRACT_LINTER_ENABLE:=0}"
: "${SKILLSBENCH_CONTRACT_LINTER_VERSION:=v1}"
: "${SKILLSBENCH_CONTRACT_REPAIR_ENABLE:=1}"
: "${SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH:=200}"

export EXPERIMENT_SEED
export SEED
export PYTHONHASHSEED
export SKILLSBENCH_TASK_CONTRACT_GUARD_ENABLE

variant_startup_delay_seconds() {
  local stagger_seconds="${MODE_STARTUP_STAGGER_SECONDS:-0}"
  local batch_size="${MODE_STARTUP_BATCH_SIZE:-1}"
  local variant_index=0
  local batch_index=0

  if ! [[ "$stagger_seconds" =~ ^[0-9]+$ ]] || [ "$stagger_seconds" -le 0 ]; then
    printf '0\n'
    return 0
  fi

  if ! [[ "$batch_size" =~ ^[0-9]+$ ]] || [ "$batch_size" -le 0 ]; then
    batch_size=1
  fi

  if [[ "${COORDINATOR_VARIANT:-}" =~ ([0-9]+)$ ]]; then
    variant_index="${BASH_REMATCH[1]}"
  else
    printf '0\n'
    return 0
  fi

  batch_index=$((variant_index / batch_size))
  printf '%s\n' $((batch_index * stagger_seconds))
}

maybe_sleep_for_mode_startup_stagger() {
  local delay_seconds=0

  delay_seconds="$(variant_startup_delay_seconds)"
  if ! [[ "$delay_seconds" =~ ^[0-9]+$ ]] || [ "$delay_seconds" -le 0 ]; then
    log "MODE_STARTUP_STAGGER sleeping=0s variant=$COORDINATOR_VARIANT batch_size=$MODE_STARTUP_BATCH_SIZE"
    return 0
  fi

  log "MODE_STARTUP_STAGGER sleeping=${delay_seconds}s variant=$COORDINATOR_VARIANT batch_size=$MODE_STARTUP_BATCH_SIZE"
  sleep "$delay_seconds"
}

resolve_codex_config_path() {
  local resolved

  resolved="$(
    PYTHONPATH="${SKILLSBENCH_PYTHONPATH:-${REPO_ROOT}}" \
      python3 -S -m skillsbench_private.codex_config_resolver \
        --repo-root "${REPO_ROOT}" 2>/dev/null || true
  )"
  printf '%s\n' "$resolved" | sed '/^[[:space:]]*$/d' | tail -n 1
}

uid="$(id -u)"
current_cgroup="$(awk -F: '$1=="0"{print $3}' /proc/self/cgroup)"
expected_prefix="/user.slice/user-${uid}.slice/user@${uid}.service/"
if [ "${SKILLSBENCH_ENABLE_USER_SCOPE_LAUNCH:-1}" = "1" ] && [ "$IO_CGROUP_ENABLE" = "1" ] && [[ "$current_cgroup" != ${expected_prefix}* ]] && [ "${IO_CGROUP_LAUNCHED:-0}" != "1" ]; then
  exec "${SKILLSBENCH_RUN_IN_USER_SCOPE}" "$0" "$@"
fi

warn_io_cgroup() {
  echo "[IO_CGROUP_WARNING] $*" >&2
}

fail_or_warn_io_cgroup() {
  warn_io_cgroup "$*"
  if [ "${IO_CGROUP_STRICT:-0}" = "1" ]; then
    exit 1
  fi
}

setup_io_cgroup() {
  local uid current_cgroup user_cgroup_root cgroup_name child_cgroup device line wbps
  uid="$(id -u)"
  current_cgroup="$(awk -F: '$1=="0"{print $3}' /proc/self/cgroup)"
  user_cgroup_root="/sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service"
  cgroup_name="${IO_CGROUP_NAME:-retrieval-tasks-backend-$$}"
  child_cgroup="${user_cgroup_root}/${cgroup_name}"

  if [ "${IO_CGROUP_ENABLE:-1}" != "1" ]; then
    return 0
  fi

  if [ ! -d "$user_cgroup_root" ]; then
    fail_or_warn_io_cgroup "user cgroup root not found: $user_cgroup_root"
    return 0
  fi

  if [[ "$current_cgroup" != /user.slice/user-${uid}.slice/user@${uid}.service/* ]]; then
    fail_or_warn_io_cgroup "current cgroup is outside user@ service subtree: $current_cgroup; use ${SKILLSBENCH_RUN_IN_USER_SCOPE}"
    return 0
  fi

  if ! grep -qw io "${user_cgroup_root}/cgroup.controllers"; then
    fail_or_warn_io_cgroup "io controller not available under: $user_cgroup_root"
    return 0
  fi

  if ! grep -qw io "${user_cgroup_root}/cgroup.subtree_control"; then
    if ! echo +io > "${user_cgroup_root}/cgroup.subtree_control"; then
      fail_or_warn_io_cgroup "failed to enable +io in subtree_control: ${user_cgroup_root}/cgroup.subtree_control"
      return 0
    fi
  fi

  mkdir -p "$child_cgroup"

  device="${IO_MAX_DEVICE:-$(findmnt -no MAJ:MIN /mnt/data 2>/dev/null | tr -d '[:space:]')}"
  if [ -z "$device" ]; then
    fail_or_warn_io_cgroup "failed to detect IO device for /mnt/data; set IO_MAX_DEVICE explicitly"
    return 0
  fi

  if [ "${IO_CGROUP_ADAPTIVE_ENABLE:-0}" = "1" ]; then
    wbps="${IO_MAX_WBPS:-${IO_ADAPTIVE_INITIAL_WBPS:-12582912}}"
  else
    wbps="${IO_MAX_WBPS:-10485760}"
  fi

  line="${device} rbps=${IO_MAX_RBPS:-max} wbps=${wbps} riops=${IO_MAX_RIOPS:-max} wiops=${IO_MAX_WIOPS:-max}"
  if ! echo "$line" > "${child_cgroup}/io.max"; then
    fail_or_warn_io_cgroup "failed to write io.max in ${child_cgroup}"
    return 0
  fi

  if ! echo $$ > "${child_cgroup}/cgroup.procs"; then
    fail_or_warn_io_cgroup "failed to move shell $$ into ${child_cgroup}"
    return 0
  fi

  IO_CGROUP_DEVICE="$device"
  IO_CGROUP_PATH="$child_cgroup"

  if [ "${IO_CGROUP_ADAPTIVE_ENABLE:-0}" = "1" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
      fail_or_warn_io_cgroup "python3 is required for adaptive io.max control"
      return 0
    fi
    python3 "${SKILLSBENCH_REPO_ROOT}/io_adaptive_wbps.py" \
      --cgroup-path "$child_cgroup" \
      --device "$device" \
      --state-path "/tmp/${cgroup_name}.io_adaptive.state" \
      --emergency-flag-path "/tmp/${cgroup_name}.io_adaptive.emergency" &
    IO_CGROUP_ADAPTIVE_PID=$!
    trap 'if [ -n "${IO_CGROUP_ADAPTIVE_PID:-}" ]; then kill "${IO_CGROUP_ADAPTIVE_PID}" >/dev/null 2>&1 || true; wait "${IO_CGROUP_ADAPTIVE_PID}" >/dev/null 2>&1 || true; fi' EXIT
    echo "[IO_CGROUP_ADAPTIVE] enabled=1 pid=${IO_CGROUP_ADAPTIVE_PID} state=/tmp/${cgroup_name}.io_adaptive.state"
  fi

  echo "[IO_CGROUP] enabled=1 name=${cgroup_name} path=${child_cgroup} io.max=$(cat "${child_cgroup}/io.max") cgroup=$(awk -F: '$1=="0"{print $3}' /proc/self/cgroup)"
}

setup_io_cgroup "$@"

skillsbench_prepare_runtime

CODEX_CONFIG_PATH="$(resolve_codex_config_path)"
if [ -n "$CODEX_CONFIG_PATH" ]; then
  export CODEX_CONFIG_PATH
  export SKILLSBENCH_CODEX_CONFIG_PATH="${SKILLSBENCH_CODEX_CONFIG_PATH:-$CODEX_CONFIG_PATH}"
else
  export CODEX_CONFIG_PATH=""
fi

RUN_TS=$(date +%Y%m%d_%H%M%S)
ARTIFACT_ROOT="${ARTIFACT_ROOT:-${SKILLSBENCH_ARTIFACT_ROOT}/retrieval_tasks_backend}"
RUNS_DIR="${RUNS_DIR:-$ARTIFACT_ROOT/runs}"
OUT_DIR="${OUT_DIR:-$RUNS_DIR/retrieval_tasks_backend_${RUN_TS}}"
JOBS_DIR="${JOBS_DIR:-$ARTIFACT_ROOT/jobs}"
TASKS_FILE="${TASKS_FILE:-${SKILLSBENCH_REPO_ROOT}/tasks_full.txt}"
CODEX_RUNTIME_PREPARE_SCRIPT="${SKILLSBENCH_REPO_ROOT}/deployment/codex_runtime/prepare_codex_runtime_bundle.py"
RUN_BASENAME="$(basename "$OUT_DIR")"
HOT_OUT_DIR="$OUT_DIR"
HOT_JOBS_DIR="$JOBS_DIR"

translate_runner_artifact_path_if_needed() {
  local candidate="$1"
  local translated

  translated="$(skillsbench_translate_runner_artifact_path "$candidate")" || translated=""
  if [ -n "$translated" ]; then
    printf '%s\n' "$translated"
  else
    printf '%s\n' "$candidate"
  fi
}

if [ -n "${SKILLSBENCH_RUNNER_CONTAINER:-}" ]; then
  ARTIFACT_ROOT="$(translate_runner_artifact_path_if_needed "$ARTIFACT_ROOT")"
  RUNS_DIR="$(translate_runner_artifact_path_if_needed "$RUNS_DIR")"
  OUT_DIR="$(translate_runner_artifact_path_if_needed "$OUT_DIR")"
  JOBS_DIR="$(translate_runner_artifact_path_if_needed "$JOBS_DIR")"
  HOT_OUT_DIR="$(translate_runner_artifact_path_if_needed "$HOT_OUT_DIR")"
  HOT_JOBS_DIR="$(translate_runner_artifact_path_if_needed "$HOT_JOBS_DIR")"
fi

if [ -n "$LOCAL_SCRATCH_ROOT" ]; then
  LOCAL_SCRATCH_ROOT="${LOCAL_SCRATCH_ROOT%/}"
  LOCAL_SCRATCH_RUN_DIR="${LOCAL_SCRATCH_ROOT}/${RUN_BASENAME}"
  HOT_OUT_DIR="${LOCAL_SCRATCH_RUN_DIR}/out"
  HOT_JOBS_DIR="${LOCAL_SCRATCH_RUN_DIR}/jobs"
fi
# Example override:
# TASKS_FILE="${SKILLSBENCH_REPO_ROOT}/experiments/oracle_curated/tasks_oracle_curated_19.txt"
MAX_PARALLEL="${MAX_PARALLEL:-1}"
MIRROR_ROOT="$OUT_DIR/mirrors"
MIRROR_MANIFEST="$OUT_DIR/mirror_manifest.json"
MASTER_LOG="$OUT_DIR/master.log"
SUMMARY_JSON="$OUT_DIR/summary.jsonl"
SUMMARY_TXT="$OUT_DIR/summary.txt"
ROW_DIR="$OUT_DIR/rows"
ROW_TXT_DIR="$OUT_DIR/rows_txt"
SUMMARY_APPEND_MARKER_DIR="$OUT_DIR/.summary_append_markers"
TOP_K="${TOP_K:-5}"
RETRIEVAL_MODE="${RETRIEVAL_MODE:-topk}"
SYNTHESIZED_SKILL_POSITION_MODE="${SYNTHESIZED_SKILL_POSITION_MODE:-synth-last}"
POST_RETRIEVAL_RERANK_ENABLED="${POST_RETRIEVAL_RERANK_ENABLED:-0}"
POST_RETRIEVAL_RERANK_TOP_M="${POST_RETRIEVAL_RERANK_TOP_M:-8}"
POST_RETRIEVAL_RERANK_MODEL="${POST_RETRIEVAL_RERANK_MODEL:-}"
POST_RETRIEVAL_RERANK_TIMEOUT="${POST_RETRIEVAL_RERANK_TIMEOUT:-20}"
POST_RETRIEVAL_RERANK_MAX_KEEP="${POST_RETRIEVAL_RERANK_MAX_KEEP:-}"
SKILLSBENCH_PREBUILT_IMAGE_REGISTRY="${SKILLSBENCH_PREBUILT_IMAGE_REGISTRY:-}"
SKILLSBENCH_PREBUILT_IMAGE_TAG="${SKILLSBENCH_PREBUILT_IMAGE_TAG:-}"
SKILLSBENCH_PREBUILT_IMAGE_MAP="${SKILLSBENCH_PREBUILT_IMAGE_MAP:-}"
SKILLSBENCH_PREBUILT_IMAGE_REQUIRE_LOCAL="${SKILLSBENCH_PREBUILT_IMAGE_REQUIRE_LOCAL:-1}"
SKILLSBENCH_UNIQUE_DOCKER_IMAGE_NAMES="${SKILLSBENCH_UNIQUE_DOCKER_IMAGE_NAMES:-1}"
export SKILLSBENCH_UNIQUE_DOCKER_IMAGE_NAMES
DEFAULT_HARBOR_AGENT="codex"
DEFAULT_CODEX_MODEL="gpt-5.2"
DEFAULT_GEMINI_CLI_MODEL="gemini/gemini-3-flash-preview-all"
DEFAULT_GEMINI_PROXY_MODEL="gemini-3-flash-preview-all"
DEFAULT_GEMINI_PROXY_BASE_URL="https://api.zyai.online/v1"
DEFAULT_GEMINI_CLI_COMPAT_BASE_URL="https://api.zyai.online"
DEFAULT_GEMINI_CLI_MODE="bridge"
DEFAULT_OPENCODE_PROVIDER_ID="zyai"
DEFAULT_OPENCODE_MODEL="${DEFAULT_OPENCODE_PROVIDER_ID}/gpt-5.2"
GEMINI_PROXY_BASE_URL="${GEMINI_PROXY_BASE_URL:-$DEFAULT_GEMINI_PROXY_BASE_URL}"
GEMINI_CLI_COMPAT_ENABLE="${GEMINI_CLI_COMPAT_ENABLE:-1}"
if [ -z "${GEMINI_CLI_COMPAT_BASE_URL:-}" ]; then
  if [ -n "${GEMINI_CLI_UPSTREAM_BASE_URL:-}" ]; then
    GEMINI_CLI_COMPAT_BASE_URL="${GEMINI_CLI_UPSTREAM_BASE_URL}"
  elif [ -n "${GEMINI_CLI_BASE_URL:-}" ]; then
    GEMINI_CLI_COMPAT_BASE_URL="${GEMINI_CLI_BASE_URL}"
  elif [ -n "${OPENAI_BASE_URL:-}" ]; then
    GEMINI_CLI_COMPAT_BASE_URL="${OPENAI_BASE_URL}"
  elif [ -n "${GEMINI_PROXY_BASE_URL:-}" ]; then
    GEMINI_CLI_COMPAT_BASE_URL="${GEMINI_PROXY_BASE_URL}"
  else
    GEMINI_CLI_COMPAT_BASE_URL="${DEFAULT_GEMINI_CLI_COMPAT_BASE_URL}"
  fi
fi
GEMINI_CLI_MODE="${GEMINI_CLI_MODE:-$DEFAULT_GEMINI_CLI_MODE}"
GEMINI_CLI_DIRECT_BASE_URL="${GEMINI_CLI_DIRECT_BASE_URL:-$GEMINI_CLI_COMPAT_BASE_URL}"
GEMINI_CLI_AUTH_MECHANISM="${GEMINI_CLI_AUTH_MECHANISM:-bearer}"
GEMINI_CLI_DIRECT_DISABLE_GOOGLE_API_KEY="${GEMINI_CLI_DIRECT_DISABLE_GOOGLE_API_KEY:-1}"
if [ -z "${GEMINI_CLI_TRUST_WORKSPACE+x}" ]; then
  GEMINI_CLI_TRUST_WORKSPACE=true
else
  case "$GEMINI_CLI_TRUST_WORKSPACE" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
      GEMINI_CLI_TRUST_WORKSPACE=true
      ;;
  esac
fi
: "${SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS:=300000}"
: "${SKILLSBENCH_GEMINI_BRIDGE_DEBUG:=0}"
GEMINI_CLI_TASK_TIMEOUT_SECONDS="${GEMINI_CLI_TASK_TIMEOUT_SECONDS:-$COMMAND1_TIMEOUT_SECONDS}"
if [ -z "$GEMINI_CLI_AGENT_TIMEOUT_MULTIPLIER" ]; then
  GEMINI_CLI_AGENT_TIMEOUT_MULTIPLIER="1.0"
fi
if [ "${GEMINI_CLI_TASK_TIMEOUT_SECONDS%.*}" = "$GEMINI_CLI_TASK_TIMEOUT_SECONDS" ]; then
  GEMINI_CLI_AGENT_TIMEOUT_MS="$((GEMINI_CLI_TASK_TIMEOUT_SECONDS * 1000))"
else
  GEMINI_CLI_AGENT_TIMEOUT_MS="$((1200000))"
fi
if [ "$SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS" -lt 1000 ] 2>/dev/null; then
  SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS=300000
fi
if [ "$SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS" -lt "$GEMINI_CLI_AGENT_TIMEOUT_MS" ] 2>/dev/null; then
  SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS="$((GEMINI_CLI_AGENT_TIMEOUT_MS + 30000))"
fi
OPENCODE_BASE_URL="${OPENCODE_BASE_URL:-$DEFAULT_GEMINI_PROXY_BASE_URL}"
OPENCODE_PROVIDER_ID="${OPENCODE_PROVIDER_ID:-$DEFAULT_OPENCODE_PROVIDER_ID}"
OPENCODE_SMALL_MODEL="${OPENCODE_SMALL_MODEL:-}"
OPENCODE_MODEL_CONTEXT_LIMIT="${OPENCODE_MODEL_CONTEXT_LIMIT:-131072}"
OPENCODE_MODEL_OUTPUT_LIMIT="${OPENCODE_MODEL_OUTPUT_LIMIT:-16384}"
REQUESTED_HARBOR_AGENT="${HARBOR_AGENT:-${AGENT:-$DEFAULT_HARBOR_AGENT}}"
REQUESTED_HARBOR_MODEL="${HARBOR_MODEL:-${MODEL:-}}"
HARBOR_PROFILE="$REQUESTED_HARBOR_AGENT"
HARBOR_AGENT="$REQUESTED_HARBOR_AGENT"
HARBOR_MODEL="$REQUESTED_HARBOR_MODEL"
HARBOR_AGENT_KWARGS=()
HARBOR_RUN_ENV=()

if [ -z "$HARBOR_MODEL" ]; then
  case "$HARBOR_PROFILE" in
    codex)
      HARBOR_MODEL="$DEFAULT_CODEX_MODEL"
      ;;
    gemini-cli)
      HARBOR_MODEL="$DEFAULT_GEMINI_CLI_MODEL"
      ;;
    opencode)
      HARBOR_MODEL="$DEFAULT_OPENCODE_MODEL"
      ;;
    gemini|gemini-proxy|gemini-cline|gemini-qwen|gemini-openhands)
      HARBOR_MODEL="$DEFAULT_GEMINI_PROXY_MODEL"
      ;;
  esac
fi

case "$HARBOR_PROFILE" in
  codex)
    HARBOR_AGENT="codex"
    AGENT_LOG_BASENAME="codex.txt"
    ;;
  gemini-cli)
    HARBOR_AGENT="gemini-cli"
    GEMINI_CLI_MODE="direct"
    GEMINI_CLI_DIRECT_BASE_URL="https://api.vectorengine.ai"
    GEMINI_CLI_AUTH_MECHANISM="bearer"
    HARBOR_MODEL="${HARBOR_MODEL:-gemini/gemini-3-pro-preview}"
    if [ -n "$HARBOR_MODEL" ] && [[ "$HARBOR_MODEL" != */* ]]; then
      HARBOR_MODEL="gemini/$HARBOR_MODEL"
    fi
    MODEL="${MODEL:-${HARBOR_MODEL#gemini/}}"
    GEMINI_CLI_API_KEY="${GOOGLE_API_KEY:-${OPENAI_API_KEY:-}}"
    if [ -z "$GEMINI_CLI_API_KEY" ]; then
      echo "gemini-cli mode requires GOOGLE_API_KEY" >&2
      exit 1
    fi
    GEMINI_CHILD_ENV_KEY_LEN=${#GEMINI_CLI_API_KEY}
    GEMINI_CHILD_ENV_KEY_SHA256_12=$(printf '%s' "$GEMINI_CLI_API_KEY" | sha256sum | cut -d ' ' -f1 | cut -c1-12)
    if [ "$GEMINI_CLI_MODE" = "direct" ]; then
      HARBOR_RUN_ENV+=(
        "GEMINI_API_KEY=$GEMINI_CLI_API_KEY"
        "GOOGLE_GEMINI_BASE_URL=$GEMINI_CLI_DIRECT_BASE_URL"
        "GEMINI_CLI_DIRECT_BASE_URL=$GEMINI_CLI_DIRECT_BASE_URL"
        "GEMINI_API_KEY_AUTH_MECHANISM=$GEMINI_CLI_AUTH_MECHANISM"
        "GEMINI_CLI_MODE=$GEMINI_CLI_MODE"
        "SKILLSBENCH_GEMINI_DIRECT_DISABLE_GOOGLE_API_KEY=1"
        "MODEL=$MODEL"
      )
      HARBOR_RUN_ENV+=(
        "GEMINI_CHILD_ENV_KEY_LEN=$GEMINI_CHILD_ENV_KEY_LEN"
        "GEMINI_CHILD_ENV_KEY_SHA256_12=$GEMINI_CHILD_ENV_KEY_SHA256_12"
        "GEMINI_CHILD_ENV_MODE=$GEMINI_CLI_MODE"
        "GEMINI_CHILD_ENV_BASE_URL=$GEMINI_CLI_DIRECT_BASE_URL"
        "GEMINI_CHILD_ENV_MODEL=$HARBOR_MODEL"
      )
      if [ -n "$HARBOR_MODEL" ]; then
        HARBOR_RUN_ENV+=("GEMINI_MODEL=${HARBOR_MODEL#gemini/}")
      fi
    elif [ "$GEMINI_CLI_MODE" = "bridge" ]; then
      if [ "$GEMINI_CLI_COMPAT_ENABLE" = "1" ]; then
        HARBOR_RUN_ENV+=(
          "SKILLSBENCH_GEMINI_UPSTREAM_API_KEY=$GEMINI_CLI_API_KEY"
          "SKILLSBENCH_GEMINI_UPSTREAM_BASE_URL=$GEMINI_CLI_COMPAT_BASE_URL"
          "SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS=$SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS"
        )
        if [ -n "${GEMINI_CLI_TRUST_WORKSPACE:-}" ]; then
          if [ "$GEMINI_CLI_TRUST_WORKSPACE" = "true" ]; then
            HARBOR_RUN_ENV+=("GEMINI_CLI_TRUST_WORKSPACE=true")
          elif [ "$GEMINI_CLI_TRUST_WORKSPACE" = "skip" ]; then
            HARBOR_RUN_ENV+=("GEMINI_CLI_SKIP_TRUST=true")
          fi
        fi
        if [ "${SKILLSBENCH_GEMINI_BRIDGE_DEBUG}" = "1" ]; then
          HARBOR_RUN_ENV+=("SKILLSBENCH_GEMINI_BRIDGE_DEBUG=1")
        fi
        if [ -z "${SKILLSBENCH_GEMINI_BRIDGE_PREVIEW_MODEL+x}" ] && \
           [ "$HARBOR_MODEL" = "gemini/gemini-3-pro-preview" ]; then
          HARBOR_RUN_ENV+=("SKILLSBENCH_GEMINI_BRIDGE_PREVIEW_MODEL=gemini-3-pro-preview")
        fi
      fi
    else
      echo "unsupported GEMINI_CLI_MODE: $GEMINI_CLI_MODE" >&2
      exit 1
    fi
    AGENT_LOG_BASENAME="gemini-cli.txt"
    ;;
  opencode)
    HARBOR_AGENT="opencode"
    if [ -n "$HARBOR_MODEL" ] && [[ "$HARBOR_MODEL" != */* ]]; then
      HARBOR_MODEL="${OPENCODE_PROVIDER_ID}/$HARBOR_MODEL"
    fi
    OPENCODE_API_KEY="${OPENCODE_API_KEY:-${GOOGLE_API_KEY:-${OPENAI_API_KEY:-}}}"
    if [ -z "$OPENCODE_API_KEY" ]; then
      echo "opencode mode requires OPENAI_API_KEY or GOOGLE_API_KEY" >&2
      exit 1
    fi
    HARBOR_RUN_ENV+=(
      "OPENAI_API_KEY=$OPENCODE_API_KEY"
      "OPENAI_BASE_URL=${OPENAI_BASE_URL:-$OPENCODE_BASE_URL}"
      "OPENAI_API_BASE=${OPENAI_API_BASE:-${OPENAI_BASE_URL:-$OPENCODE_BASE_URL}}"
      "SKILLSBENCH_OPENCODE_COMPAT_PROVIDER=1"
      "SKILLSBENCH_OPENCODE_MODEL_CONTEXT_LIMIT=$OPENCODE_MODEL_CONTEXT_LIMIT"
      "SKILLSBENCH_OPENCODE_MODEL_OUTPUT_LIMIT=$OPENCODE_MODEL_OUTPUT_LIMIT"
    )
    if [ -n "$OPENCODE_SMALL_MODEL" ]; then
      HARBOR_RUN_ENV+=("SKILLSBENCH_OPENCODE_SMALL_MODEL=$OPENCODE_SMALL_MODEL")
    fi
    AGENT_LOG_BASENAME="opencode.txt"
    ;;
  gemini|gemini-proxy|gemini-cline)
    HARBOR_AGENT="cline-cli"
    if [ -n "$HARBOR_MODEL" ] && [[ "$HARBOR_MODEL" == *:* ]]; then
      HARBOR_MODEL="${HARBOR_MODEL#*:}"
    fi
    if [ -n "$HARBOR_MODEL" ] && [[ "$HARBOR_MODEL" != openai:* ]]; then
      HARBOR_MODEL="openai:${HARBOR_MODEL#*/}"
    fi
    GEMINI_PROXY_API_KEY="${GOOGLE_API_KEY:-${OPENAI_API_KEY:-}}"
    if [ -z "$GEMINI_PROXY_API_KEY" ]; then
      echo "gemini-proxy mode requires OPENAI_API_KEY or GOOGLE_API_KEY" >&2
      exit 1
    fi
    HARBOR_RUN_ENV+=(
      "API_KEY=$GEMINI_PROXY_API_KEY"
      "OPENAI_API_KEY=$GEMINI_PROXY_API_KEY"
      "BASE_URL=${BASE_URL:-$GEMINI_PROXY_BASE_URL}"
    )
    AGENT_LOG_BASENAME="cline.txt"
    ;;
  gemini-qwen)
    HARBOR_AGENT="qwen-coder"
    if [ -n "$HARBOR_MODEL" ] && [[ "$HARBOR_MODEL" == */* ]]; then
      HARBOR_MODEL="${HARBOR_MODEL#*/}"
    fi
    GEMINI_PROXY_API_KEY="${GOOGLE_API_KEY:-${OPENAI_API_KEY:-}}"
    if [ -z "$GEMINI_PROXY_API_KEY" ]; then
      echo "gemini-proxy mode requires OPENAI_API_KEY or GOOGLE_API_KEY" >&2
      exit 1
    fi
    HARBOR_AGENT_KWARGS+=(
      --ak "api_key=${GEMINI_PROXY_API_KEY}"
      --ak "base_url=${GEMINI_PROXY_BASE_URL}"
    )
    AGENT_LOG_BASENAME="qwen-code.txt"
    ;;
  gemini-openhands)
    HARBOR_AGENT="openhands"
    if [ -n "$HARBOR_MODEL" ] && [[ "$HARBOR_MODEL" != */* ]]; then
      HARBOR_MODEL="openai/$HARBOR_MODEL"
    fi
    GEMINI_PROXY_API_KEY="${LLM_API_KEY:-${GOOGLE_API_KEY:-}}"
    if [ -z "$GEMINI_PROXY_API_KEY" ]; then
      echo "gemini-proxy mode requires LLM_API_KEY or GOOGLE_API_KEY" >&2
      exit 1
    fi
    HARBOR_AGENT_KWARGS+=(
      --ak "disable_tool_calls=true"
      --ak "api_base=${GEMINI_PROXY_BASE_URL}"
    )
    HARBOR_RUN_ENV+=(
      "LLM_API_KEY=$GEMINI_PROXY_API_KEY"
      "LLM_BASE_URL=${LLM_BASE_URL:-$GEMINI_PROXY_BASE_URL}"
    )
    AGENT_LOG_BASENAME="openhands.txt"
    ;;
  *)
    AGENT_LOG_BASENAME="${HARBOR_AGENT}.txt"
    ;;
esac
IO_GUARD_ENABLE="${IO_GUARD_ENABLE}"
IO_GUARD_PATH="${IO_GUARD_PATH}"
IO_GUARD_SAMPLE_SECONDS="${IO_GUARD_SAMPLE_SECONDS}"
IO_GUARD_STABLE_SAMPLES="${IO_GUARD_STABLE_SAMPLES}"
IO_GUARD_MAX_WAIT_SECONDS="${IO_GUARD_MAX_WAIT_SECONDS}"
IO_GUARD_SOME_AVG10_MAX="${IO_GUARD_SOME_AVG10_MAX}"
IO_GUARD_SOME_AVG60_MAX="${IO_GUARD_SOME_AVG60_MAX}"
IO_GUARD_FULL_AVG10_MAX="${IO_GUARD_FULL_AVG10_MAX}"
IO_GUARD_FULL_AVG60_MAX="${IO_GUARD_FULL_AVG60_MAX}"
IO_GUARD_DEVICE_W_AWAIT_MS_MAX="${IO_GUARD_DEVICE_W_AWAIT_MS_MAX}"
IO_GUARD_DEVICE_UTIL_PCT_MAX="${IO_GUARD_DEVICE_UTIL_PCT_MAX}"
IO_GUARD_DEVICE_QDEPTH_MAX="${IO_GUARD_DEVICE_QDEPTH_MAX}"
IO_GUARD_HARD_SOME_AVG10_MAX="${IO_GUARD_HARD_SOME_AVG10_MAX}"
IO_GUARD_HARD_SOME_AVG60_MAX="${IO_GUARD_HARD_SOME_AVG60_MAX}"
IO_GUARD_HARD_FULL_AVG10_MAX="${IO_GUARD_HARD_FULL_AVG10_MAX}"
IO_GUARD_HARD_FULL_AVG60_MAX="${IO_GUARD_HARD_FULL_AVG60_MAX}"
IO_GUARD_FALLBACK_TO_GLOBAL_IF_DEVICE_MISSING="${IO_GUARD_FALLBACK_TO_GLOBAL_IF_DEVICE_MISSING}"
IO_GUARD_MIN_FREE_GB="${IO_GUARD_MIN_FREE_GB}"
IO_TEARDOWN_COOLDOWN_ENABLE="${IO_TEARDOWN_COOLDOWN_ENABLE}"
IO_TEARDOWN_HEALTHY_FULL_AVG10="${IO_TEARDOWN_HEALTHY_FULL_AVG10}"
IO_TEARDOWN_HEALTHY_W_AWAIT_MS="${IO_TEARDOWN_HEALTHY_W_AWAIT_MS}"
IO_TEARDOWN_HEALTHY_SAMPLES="${IO_TEARDOWN_HEALTHY_SAMPLES}"
IO_TEARDOWN_MAX_WAIT_SECONDS="${IO_TEARDOWN_MAX_WAIT_SECONDS}"
LAUNCH_STAGGER_SECONDS="${LAUNCH_STAGGER_SECONDS}"


mkdir -p "$RUNS_DIR" "$JOBS_DIR"
JOBS_DIR="$(cd "$JOBS_DIR" && pwd)"
mkdir -p "$OUT_DIR"
mkdir -p "$ROW_DIR" "$ROW_TXT_DIR"
mkdir -p "$SUMMARY_APPEND_MARKER_DIR"
rm -f "$SUMMARY_APPEND_MARKER_DIR"/*
if [ -n "$LOCAL_SCRATCH_ROOT" ]; then
  mkdir -p "$HOT_OUT_DIR" "$HOT_JOBS_DIR"
  HOT_OUT_DIR="$(cd "$HOT_OUT_DIR" && pwd)"
  HOT_JOBS_DIR="$(cd "$HOT_JOBS_DIR" && pwd)"
fi
: > "$MASTER_LOG"
: > "$SUMMARY_JSON"
: > "$SUMMARY_TXT"

mapfile -t TASKS < <(grep -v '^[[:space:]]*#' "$TASKS_FILE" | sed '/^[[:space:]]*$/d')

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$MASTER_LOG" >/dev/null
}

ensure_prepared_codex_runtime_bundle() {
  local codex_runtime_exports

  if [ "$HARBOR_PROFILE" != "codex" ]; then
    return 0
  fi

  if [ ! -f "$CODEX_RUNTIME_PREPARE_SCRIPT" ]; then
    echo "Missing Codex runtime helper: $CODEX_RUNTIME_PREPARE_SCRIPT" >&2
    exit 1
  fi

  if ! codex_runtime_exports="$("$CONDA_PREFIX/bin/python" \
    "$CODEX_RUNTIME_PREPARE_SCRIPT" \
    --mode check \
    --format shell 2>>"$MASTER_LOG")"; then
    log "CODEX_RUNTIME_BUNDLE_CHECK_FAILED helper=$CODEX_RUNTIME_PREPARE_SCRIPT"
    log "Prepare the bundle on a source machine with host codex available:"
    log "$CONDA_PREFIX/bin/python deployment/codex_runtime/prepare_codex_runtime_bundle.py --mode prepare --format json"
    exit 1
  fi

  eval "$codex_runtime_exports"
  export SKILLSBENCH_CODEX_RUNTIME_ARCHIVE
  export SKILLSBENCH_CODEX_RUNTIME_MANIFEST
  export SKILLSBENCH_CODEX_RUNTIME_REQUIRE_PREPARED=1

  log "SKILLSBENCH_CODEX_RUNTIME_ARCHIVE=$SKILLSBENCH_CODEX_RUNTIME_ARCHIVE"
  log "SKILLSBENCH_CODEX_RUNTIME_MANIFEST=$SKILLSBENCH_CODEX_RUNTIME_MANIFEST"
  log "SKILLSBENCH_CODEX_RUNTIME_REQUIRE_PREPARED=$SKILLSBENCH_CODEX_RUNTIME_REQUIRE_PREPARED"
}

codex_provider_preflight() {
  local config_exports
  local provider_id
  local provider_base_url
  local provider_env_key
  local provider_api_key
  local provider_model
  local preflight_rc
  local proxy_url
  local no_proxy_value
  local -a preflight_python_cmd

  if [ "$HARBOR_PROFILE" != "codex" ]; then
    log "CODEX_PROVIDER_PREFLIGHT_SKIPPED harbor_profile=$HARBOR_PROFILE"
    return 0
  fi

  if [ "${CODEX_PROVIDER_PREFLIGHT_DISABLE:-0}" = "1" ]; then
    log "CODEX_PROVIDER_PREFLIGHT_SKIPPED disabled=1"
    return 0
  fi

  if [ ! -f "$CODEX_CONFIG_PATH" ]; then
    log "CODEX_PROVIDER_PREFLIGHT_MISSING_CONFIG path=$CODEX_CONFIG_PATH"
    exit 1
  fi

  if ! config_exports="$("$CONDA_PREFIX/bin/python" - "$CODEX_CONFIG_PATH" <<'PY'
import shlex
import sys
import tomllib
from pathlib import Path

config = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
provider_id = str(config.get("model_provider") or "").strip()
providers = config.get("model_providers") or {}
provider = providers.get(provider_id) or {}
base_url = str(provider.get("base_url") or "").strip().rstrip("/")
env_key = str(provider.get("env_key") or "").strip()

print(f"provider_id={shlex.quote(provider_id)}")
print(f"provider_base_url={shlex.quote(base_url)}")
print(f"provider_env_key={shlex.quote(env_key)}")
PY
)"; then
    log "CODEX_PROVIDER_PREFLIGHT_CONFIG_PARSE_FAILED path=$CODEX_CONFIG_PATH"
    exit 1
  fi

  eval "$config_exports"

  provider_env_key="${provider_env_key:-OPENAI_API_KEY}"
  provider_api_key="${!provider_env_key:-}"
  provider_model="${HARBOR_MODEL:-$DEFAULT_CODEX_MODEL}"

  if [ -z "$provider_api_key" ]; then
    log "CODEX_PROVIDER_PREFLIGHT_MISSING_API_KEY env_key=$provider_env_key"
    exit 1
  fi

  if [ -z "$provider_base_url" ]; then
    log "CODEX_PROVIDER_PREFLIGHT_MISSING_BASE_URL provider=$provider_id path=$CODEX_CONFIG_PATH"
    exit 1
  fi

  log "CODEX_PROVIDER_PREFLIGHT_BEGIN provider=$provider_id base_url=$provider_base_url env_key=$provider_env_key model=$provider_model"
  preflight_python_cmd=("$CONDA_PREFIX/bin/python")
  if skillsbench_api_proxy_enabled; then
    proxy_url="$(skillsbench_docker_proxy_url)"
    no_proxy_value="${SKILLSBENCH_DEFAULT_DOCKER_NO_PROXY:-localhost,127.0.0.1,::1}"
    preflight_python_cmd=(
      env
      "HTTP_PROXY=$proxy_url"
      "HTTPS_PROXY=$proxy_url"
      "ALL_PROXY=$proxy_url"
      "http_proxy=$proxy_url"
      "https_proxy=$proxy_url"
      "all_proxy=$proxy_url"
      "NO_PROXY=$no_proxy_value"
      "no_proxy=$no_proxy_value"
      "$CONDA_PREFIX/bin/python"
    )
  fi
  set +e
  "${preflight_python_cmd[@]}" - "$provider_base_url" "$provider_api_key" "$provider_model" "$CODEX_PROVIDER_PREFLIGHT_TIMEOUT_SECONDS" <<'PY' 2>&1 | tee -a "$MASTER_LOG"
import json
import sys
from urllib import error, request

base_url = sys.argv[1].rstrip("/")
api_key = sys.argv[2]
model = sys.argv[3]
timeout = float(sys.argv[4])

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}


def http_call(url, method="GET", payload=None):
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload_text = resp.read().decode("utf-8", errors="replace")
            return resp.status, payload_text
    except error.HTTPError as exc:
        payload_text = exc.read().decode("utf-8", errors="replace")
        return exc.code, payload_text


models_status, models_body = http_call(f"{base_url}/models")
print(f"MODELS_STATUS={models_status}")
if models_status != 200:
    print(models_body[:1000], file=sys.stderr)
    sys.exit(1)

try:
    models_payload = json.loads(models_body)
except json.JSONDecodeError as exc:
    print(f"MODELS_PARSE_ERROR={exc}", file=sys.stderr)
    sys.exit(1)

model_ids = []
for item in models_payload.get("data") or []:
    if isinstance(item, dict):
        model_id = item.get("id") or item.get("name")
        if isinstance(model_id, str) and model_id:
            model_ids.append(model_id)

print(f"MODELS_COUNT={len(model_ids)}")
print(f"MODELS_CONTAINS_TARGET={int(model in model_ids)}")
if model not in model_ids:
    print(f"TARGET_MODEL_MISSING={model}", file=sys.stderr)
    sys.exit(1)

responses_status, responses_body = http_call(
    f"{base_url}/responses",
    method="POST",
    payload={
        "model": model,
        "input": "Reply with OK.",
        "max_output_tokens": 16,
    },
)
print(f"RESPONSES_STATUS={responses_status}")
if responses_status != 200:
    print(responses_body[:1000], file=sys.stderr)
    sys.exit(1)
PY
  preflight_rc=${PIPESTATUS[0]}
  set -e

  if [ "$preflight_rc" -ne 0 ]; then
    log "CODEX_PROVIDER_PREFLIGHT_FAILED provider=$provider_id base_url=$provider_base_url model=$provider_model rc=$preflight_rc"
    exit 1
  fi

  log "CODEX_PROVIDER_PREFLIGHT_OK provider=$provider_id base_url=$provider_base_url model=$provider_model"
}

print_row_txt() {
  local row_json_path="$1"
  python3 -S - "$row_json_path" <<'PY'
import json
import sys

row = json.load(open(sys.argv[1]))
print("------------------------------------------------------------")
print(f"TASK: {row.get('task')}")
print(f"JOB_NAME: {row.get('job_name')}")
print(f"MIRROR_PATH: {row.get('mirror_path')}")
print(f"JOB_PATH: {row.get('job_path')}")
print(f"TRIAL_PATH: {row.get('trial_path')}")
print(f"TRIAL_STARTED_AT: {row.get('trial_started_at')}")
print(f"TRIAL_FINISHED_AT: {row.get('trial_finished_at')}")
print(f"TRIAL_DURATION_SECONDS: {row.get('trial_duration_seconds')}")
print(f"ENVIRONMENT_SETUP_DURATION_SECONDS: {row.get('environment_setup_duration_seconds')}")
print(f"AGENT_SETUP_DURATION_SECONDS: {row.get('agent_setup_duration_seconds')}")
print(f"AGENT_EXECUTION_DURATION_SECONDS: {row.get('agent_execution_duration_seconds')}")
print(f"VERIFIER_DURATION_SECONDS: {row.get('verifier_duration_seconds')}")
print(f"TOKEN_SOURCE: {row.get('token_source')}")
print(f"TOTAL_TOKENS_USED: {row.get('total_tokens_used')}")
print(f"TOKEN_THREAD_COUNT: {row.get('token_thread_count')}")
print(f"AGENT_RESULT_INPUT_TOKENS: {row.get('agent_result_input_tokens')}")
print(f"AGENT_RESULT_CACHE_TOKENS: {row.get('agent_result_cache_tokens')}")
print(f"AGENT_RESULT_OUTPUT_TOKENS: {row.get('agent_result_output_tokens')}")
print(f"SETUP_SUCCESS: {row.get('setup_success')}")
print(f"ENTERED_LOOP: {row.get('entered_loop')}")
print(f"COMMAND_0_RETURN_CODE: {row.get('command_0_return_code')}")
print(f"COMMAND_1_RETURN_CODE: {row.get('command_1_return_code')}")
print(f"AGENT_SUCCESS: {row.get('agent_success')}")
print(f"VERIFIER_STARTED: {row.get('verifier_started')}")
print(f"TRIAL_RESULT: {row.get('trial_result')}")
print(f"JOB_RESULT: {row.get('job_result')}")
print(f"REWARD: {row.get('reward')}")
print(f"VERIFIER_REWARD: {row.get('verifier_reward')}")
print(f"JOB_MEAN: {row.get('job_mean')}")
print(f"VARIANT_ID: {row.get('variant_id')}")
print(f"INJECTED_SKILL_LIST: {row.get('injected_skill_list')}")
print(f"COORDINATOR_PATH: {row.get('coordinator_path')}")
print(f"RETRIEVED_SKILL_PATHS: {row.get('retrieved_skill_paths')}")
print(f"FRONT_PACKET_PATH: {row.get('front_packet_path')}")
print(f"FRONT_PACKET_TOTAL_TOKENS: {row.get('front_packet_total_tokens')}")
print(f"FRONT_PACKET_SECTION_TOKENS: {row.get('front_packet_section_tokens')}")
print(f"FRONT_PACKET_TOKENIZER: {row.get('front_packet_tokenizer')}")
print(f"ACTUAL_SKILL_MD_FILES_OPENED: {row.get('actual_skill_md_files_opened')}")
print(f"FIRST_SKILL_OPENED: {row.get('first_skill_opened')}")
print(f"COORDINATOR_OPENED: {row.get('coordinator_opened')}")
print(f"TARGET_RETRIEVED_SKILLS_OPENED: {row.get('target_retrieved_skills_opened')}")
print(f"OUTPUT_CONTRACT_TARGETS: {row.get('output_contract_targets')}")
print(f"OUTPUT_CONTRACT_MODULE_TARGETS: {row.get('output_contract_module_targets')}")
print(f"OUTPUT_FILES_CREATED: {row.get('output_files_created')}")
print(f"TARGET_OUTPUT_CREATED: {row.get('target_output_created')}")
print(f"TIMEOUT_OR_EXCEPTION: {row.get('timeout_or_exception')}")
print(f"VERIFIER_FAILURE_MESSAGE: {row.get('verifier_failure_message')}")
print(f"FAILURE_BUCKET: {row.get('failure_bucket')}")
print(f"EXCEPTION: {row.get('exception')}")
print("------------------------------------------------------------")
PY
}

write_row_txt() {
  local row_json_path="$1"
  local row_txt_path="$2"
  print_row_txt "$row_json_path" > "$row_txt_path"
}

append_row_summary_to_task_log() {
  local row_json_path="$1"
  local task_log="$2"
  {
    echo
    echo "[TASK_SUMMARY]"
    print_row_txt "$row_json_path"
  } >> "$task_log"
}

summary_marker_for_row_json() {
  local row_json_path="$1"
  local row_basename="${row_json_path##*/}"
  printf '%s/%s.summary_appended' "$SUMMARY_APPEND_MARKER_DIR" "$row_basename"
}

append_row_summary_to_jsonl_once() {
  local row_json_path="$1"
  local marker_path="$2"
  if [ -f "$marker_path" ] || [ ! -f "$row_json_path" ]; then
    return 0
  fi
  cat "$row_json_path" >> "$SUMMARY_JSON"
  touch "$marker_path"
}

copy_file_to_final() {
  local src="$1"
  local dst="$2"

  if [ ! -f "$src" ]; then
    return 0
  fi

  mkdir -p "$(dirname "$dst")"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$src" "$dst"
  else
    cp -a "$src" "$dst"
  fi
}

sync_dir_to_final() {
  local src="$1"
  local dst="$2"

  if [ ! -d "$src" ]; then
    return 0
  fi

  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$src"/ "$dst"/
  else
    rm -rf "$dst"
    mkdir -p "$dst"
    cp -a "$src"/. "$dst"/
  fi
}

sync_task_artifacts_to_final() {
  local job_name="$1"
  local hot_task_log="$2"
  local final_task_log="$3"
  local hot_job_dir="$HOT_JOBS_DIR/$job_name"
  local final_job_dir="$JOBS_DIR/$job_name"

  if [ "$HOT_JOBS_DIR" = "$JOBS_DIR" ] && [ "$hot_task_log" = "$final_task_log" ]; then
    return 0
  fi

  copy_file_to_final "$hot_task_log" "$final_task_log"
  sync_dir_to_final "$hot_job_dir" "$final_job_dir"

  if [ "$LOCAL_SCRATCH_CLEANUP" = "1" ]; then
    rm -f "$hot_task_log"
    rm -rf "$hot_job_dir"
  fi
}

resolve_trial_dir() {
  local job_dir="$1"
  local trial_dir=""
  if [ ! -d "$job_dir" ]; then
    printf ''
    return 0
  fi

  for p in "$job_dir"/*; do
    if [ -d "$p" ] && [[ "${p##*/}" == *"__"* ]]; then
      trial_dir="$p"
      break
    fi
  done
  printf '%s' "$trial_dir"
}

read_return_code_txt() {
  local path="$1"
  local value
  if [ ! -f "$path" ]; then
    printf ''
    return 0
  fi
  value="$(cat "$path" 2>/dev/null | tr -d '[:space:]')"
  if [[ "$value" =~ ^-?[0-9]+$ ]]; then
    printf '%s' "$value"
  else
    printf ''
  fi
}

write_state_consistency_overlay() {
  local trial_dir="$1"
  local job_dir="$2"
  local enabled="$3"
  local version="$4"
  local harbor_rc="$5"
  local fallback_reason="$6"
  local termination_reason="$7"
  local command1_backfill="$8"
  local command1_source="$9"
  local verifier_started="${10}"
  local verifier_state_source="${11}"
  local output_path

  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  "$CONDA_PREFIX/bin/python" -S - \
    "$trial_dir" "$job_dir" "$enabled" "$version" "$harbor_rc" "$fallback_reason" \
    "$termination_reason" "$command1_backfill" "$command1_source" "$verifier_started" \
    "$verifier_state_source" <<'PY'
import json
import sys
from pathlib import Path

(
    trial_dir,
    job_dir,
    enabled,
    version,
    harbor_rc,
    fallback_reason,
    termination_reason,
    command1_backfill,
    command1_source,
    verifier_started,
    verifier_state_source,
) = sys.argv[1:12]


def _to_int_or_none(value: str):
    try:
        return int(str(value).strip())
    except Exception:
        return None


payload = {
    "state_consistency_enabled": str(enabled).strip() == "1",
    "state_consistency_version": version or None,
    "runner_harbor_rc": _to_int_or_none(harbor_rc),
    "runner_fallback_reason": fallback_reason or None,
    "termination_reason": termination_reason or None,
    "termination_reason_source": "runner_state_overlay",
    "command_1_return_code_backfill": _to_int_or_none(command1_backfill),
    "command_1_return_code_source": command1_source or "missing",
    "verifier_started_backfill": str(verifier_started).strip() == "1",
    "verifier_started_source": verifier_state_source or "unknown",
}

destinations = []
if job_dir:
    destinations.append(Path(job_dir) / "state_consistency_overlay.json")
if trial_dir:
    destinations.append(Path(trial_dir) / "state_consistency_overlay.json")

for path in destinations:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
PY
}

emit_fallback_row_json() {
  local task="$1"
  local idx="$2"
  local job_name="$3"
  local mirror_path="$4"
  local command1_return_code="$5"
  local command0_return_code="$6"
  local setup_success="$7"
  local entered_loop="$8"
  local verifier_started="$9"
  local trial_dir="${10}"
  local job_dir="${11}"
  local harbor_rc="${12}"
  local reason="${13:-runner_fallback}"

  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  "$CONDA_PREFIX/bin/python" -S - \
    "$task" "$idx" "$job_name" "$mirror_path" "$command1_return_code" \
    "$command0_return_code" "$setup_success" "$entered_loop" "$verifier_started" \
    "$trial_dir" "$job_dir" "$harbor_rc" "$reason" <<'PY'
import json
import sys
from pathlib import Path

(
    task,
    idx,
    job_name,
    mirror_path,
    command1_return_code,
    command0_return_code,
    setup_success,
    entered_loop,
    verifier_started,
    trial_dir,
    job_dir,
    harbor_rc,
    reason,
) = sys.argv[1:14]


def _to_bool(value: str) -> bool:
    return str(value).strip() == "1"


def _to_int_or_none(value: str):
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


mirror_path_obj = Path(mirror_path)
variant_manifest = _read_manifest(mirror_path_obj / "coordinator_variant_manifest.json")
trial_path = Path(trial_dir) if trial_dir else None
job_path = Path(job_dir) if job_dir else None
row = {
    "task": task,
    "job_name": job_name,
    "mirror_path": str(mirror_path_obj),
    "job_path": str(job_path) if job_path else None,
    "trial_path": str(trial_path) if trial_path else None,
    "trial_started_at": None,
    "trial_finished_at": None,
    "trial_duration_seconds": None,
    "environment_setup_duration_seconds": None,
    "agent_setup_duration_seconds": None,
    "agent_execution_duration_seconds": None,
    "verifier_duration_seconds": None,
    "token_source": None,
    "total_tokens_used": None,
    "token_thread_count": None,
    "agent_result_input_tokens": None,
    "agent_result_cache_tokens": None,
    "agent_result_output_tokens": None,
    "setup_success": _to_bool(setup_success),
    "entered_loop": _to_bool(entered_loop),
    "command_0_return_code": _to_int_or_none(command0_return_code),
    "command_1_return_code": _to_int_or_none(command1_return_code),
    "command1_return_code": _to_int_or_none(command1_return_code),
    "agent_success": False,
    "verifier_started": _to_bool(verifier_started),
    "trial_result": False,
    "job_result": False,
    "reward": None,
    "verifier_reward": None,
    "job_mean": None,
    "exception": reason,
    "variant_id": variant_manifest.get("variant_id"),
    "injected_skill_list": variant_manifest.get("injected_skill_list", []),
    "coordinator_path": variant_manifest.get("coordinator_task_local_path"),
    "retrieved_skill_paths": variant_manifest.get("retrieved_skill_paths", []),
    "front_packet_path": variant_manifest.get("front_packet_task_local_path"),
    "front_packet_total_tokens": variant_manifest.get("front_packet_total_tokens"),
    "front_packet_section_tokens": variant_manifest.get("front_packet_section_tokens", {}),
    "front_packet_tokenizer": variant_manifest.get("front_packet_tokenizer"),
    "actual_skill_md_files_opened": [],
    "first_skill_opened": None,
    "coordinator_opened": False,
    "target_retrieved_skills_opened": [],
    "output_contract_targets": variant_manifest.get("output_contract_targets", []),
    "output_contract_module_targets": variant_manifest.get("output_contract_module_targets", []),
    "contract_probe_targets": (
        variant_manifest.get("contract_probe_targets")
        if isinstance(variant_manifest.get("contract_probe_targets"), list)
        else variant_manifest.get("output_contract_targets", [])
    ),
    "contract_probe_state": "missing",
    "output_files_created": [],
    "target_output_created": False,
    "timeout_or_exception": True,
    "verifier_failure_message": reason,
    "context_consumed": False,
    "state_consistency_enabled": bool(variant_manifest.get("state_consistency_enabled")),
    "state_consistency_version": variant_manifest.get("state_consistency_version"),
    "state_consistency_backfill_applied": False,
    "state_consistency_overlay_path": None,
    "command_1_return_code_source": (
        "agent/command-1/return-code.txt" if _to_int_or_none(command1_return_code) is not None else "missing"
    ),
    "verifier_started_source": "runner_fallback",
    "target_output_created_source": "runner_fallback_default_false",
    "termination_reason": reason,
    "termination_reason_source": "runner_fallback",
    "failure_bucket": "agent_command_timeout_or_no_result",
}
print(json.dumps(row, ensure_ascii=False))
PY
}

summarize_one() {
  local task="$1"
  local job_name="$2"
  local mirror_path="$3"
  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  "$CONDA_PREFIX/bin/python" -S \
    experiments/retrieval_tasks_backend/summarize_one_retrieval_task.py \
    --task "$task" \
    --job-name "$job_name" \
    --mirror-path "$mirror_path" \
    --jobs-dir "$JOBS_DIR" \
    --agent-log-basename "$AGENT_LOG_BASENAME"
}

prepare_contract_closure_overlay() {
  local task="$1"
  local mirror_path="$2"
  local input_overlay="$3"
  local output_overlay="$mirror_path/READ_FIRST.contract_closure.md"
  local manifest_out="$mirror_path/contract_closure_manifest.json"
  local generated_overlay

  if [ "${SKILLSBENCH_CONTRACT_CLOSURE_ENABLE:-0}" != "1" ]; then
    printf '%s\n' "$input_overlay"
    return 0
  fi
  if [ -z "$input_overlay" ] || [ ! -f "$input_overlay" ]; then
    log "CONTRACT_CLOSURE_SKIP task=$task reason=missing_input_overlay input=$input_overlay"
    printf '%s\n' "$input_overlay"
    return 0
  fi

  set +e
  generated_overlay="$(
    PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
    "$CONDA_PREFIX/bin/python" -S \
      experiments/retrieval_tasks_backend/contract_closure_overlay.py \
      --mirror-path "$mirror_path" \
      --input-overlay "$input_overlay" \
      --output-overlay "$output_overlay" \
      --manifest-out "$manifest_out" \
      --version "$SKILLSBENCH_CONTRACT_CLOSURE_VERSION" \
      --max-cautions "$SKILLSBENCH_CONTRACT_CLOSURE_MAX_CAUTIONS"
  )"
  local rc=$?
  set -e
  if [ "$rc" -ne 0 ] || [ -z "$generated_overlay" ] || [ ! -f "$generated_overlay" ]; then
    log "CONTRACT_CLOSURE_GENERATE_FAILED task=$task rc=$rc input=$input_overlay"
    printf '%s\n' "$input_overlay"
    return 0
  fi

  log "CONTRACT_CLOSURE_OVERLAY task=$task overlay=$generated_overlay manifest=$manifest_out"
  printf '%s\n' "$generated_overlay"
}

row_has_setup_environment_issue() {
  local row_json_path="$1"
  python3 -S - "$row_json_path" <<'PY'
import json
import sys
from pathlib import Path

row = json.loads(Path(sys.argv[1]).read_text())
sys.exit(0 if row.get("failure_bucket") == "setup_environment_issue" else 1)
PY
}

abort_if_requested() {
  local where="$1"
  local abort_file="$OUT_DIR/.abort_requested"
  if [ -f "$abort_file" ]; then
    log "ABORT_REQUESTED where=$where reason=$(cat "$abort_file" 2>/dev/null || true)"
    exit 86
  fi
}

log "RUN_TS=$RUN_TS"
ensure_prepared_codex_runtime_bundle
log "OUT_DIR=$OUT_DIR"
log "SKILLSBENCH_ARTIFACT_ROOT=${SKILLSBENCH_ARTIFACT_ROOT}"
log "SKILLSBENCH_RUNNER_HOST_ARTIFACT_ROOT=${SKILLSBENCH_RUNNER_HOST_ARTIFACT_ROOT:-}"
log "TASKS_FILE=$TASKS_FILE"
log "TASK_COUNT=${#TASKS[@]}"
log "MAX_PARALLEL=$MAX_PARALLEL"
log "MIRROR_ROOT=$MIRROR_ROOT"
log "JOBS_DIR=$JOBS_DIR"
log "HOT_OUT_DIR=$HOT_OUT_DIR"
log "HOT_JOBS_DIR=$HOT_JOBS_DIR"
log "LOCAL_SCRATCH_ROOT=$LOCAL_SCRATCH_ROOT"
log "LOCAL_SCRATCH_CLEANUP=$LOCAL_SCRATCH_CLEANUP"
log "HARBOR_PROFILE=$HARBOR_PROFILE"
log "REQUESTED_HARBOR_AGENT=$REQUESTED_HARBOR_AGENT"
log "REQUESTED_HARBOR_MODEL=$REQUESTED_HARBOR_MODEL"
log "HARBOR_AGENT=$HARBOR_AGENT"
log "HARBOR_MODEL=$HARBOR_MODEL"
log "AGENT_LOG_BASENAME=$AGENT_LOG_BASENAME"
log "HARBOR_AGENT_KWARG_COUNT=${#HARBOR_AGENT_KWARGS[@]}"
log "HARBOR_RUN_ENV_COUNT=${#HARBOR_RUN_ENV[@]}"
log "SKILLSBENCH_PROXY_SCOPE=$(skillsbench_proxy_scope)"
if skillsbench_docker_proxy_enabled; then
  log "SKILLSBENCH_DOCKER_PROXY_ENABLED=1"
else
  log "SKILLSBENCH_DOCKER_PROXY_ENABLED=0"
fi
if skillsbench_api_proxy_enabled; then
  log "SKILLSBENCH_API_PROXY_ENABLED=1"
else
  log "SKILLSBENCH_API_PROXY_ENABLED=0"
fi
log "SKILLSBENCH_DOCKER_PROXY_URL=$(skillsbench_docker_proxy_url)"
log "SKILLSBENCH_FIXED_SEED=$SKILLSBENCH_FIXED_SEED"
log "CORE_LOCK_WARNING state_consistency_and_fixed_seed_locked allow_override=$SKILLSBENCH_ALLOW_CORE_LOCK_OVERRIDE reason=prevents_lost_or_ambiguous_command_state"
log "EXPERIMENT_SEED=$EXPERIMENT_SEED"
log "SEED=$SEED"
log "PYTHONHASHSEED=$PYTHONHASHSEED"
log "CODEX_CONFIG_PATH=$CODEX_CONFIG_PATH"
log "CODEX_PROVIDER_PREFLIGHT_TIMEOUT_SECONDS=$CODEX_PROVIDER_PREFLIGHT_TIMEOUT_SECONDS"
log "CODEX_PROVIDER_PREFLIGHT_DISABLE=$CODEX_PROVIDER_PREFLIGHT_DISABLE"
log "GEMINI_PROXY_BASE_URL=$GEMINI_PROXY_BASE_URL"
log "GEMINI_CLI_MODE=$GEMINI_CLI_MODE"
log "GEMINI_CLI_COMPAT_BASE_URL=$GEMINI_CLI_COMPAT_BASE_URL"
log "GEMINI_CLI_DIRECT_BASE_URL=$GEMINI_CLI_DIRECT_BASE_URL"
log "GEMINI_CLI_COMPAT_ENABLE=$GEMINI_CLI_COMPAT_ENABLE"
log "RETRIEVAL_MODE=$RETRIEVAL_MODE"
log "SYNTHESIZED_SKILL_POSITION_MODE=$SYNTHESIZED_SKILL_POSITION_MODE"
log "POST_RETRIEVAL_RERANK_ENABLED=$POST_RETRIEVAL_RERANK_ENABLED"
log "POST_RETRIEVAL_RERANK_TOP_M=$POST_RETRIEVAL_RERANK_TOP_M"
log "POST_RETRIEVAL_RERANK_MODEL=$POST_RETRIEVAL_RERANK_MODEL"
log "POST_RETRIEVAL_RERANK_TIMEOUT=$POST_RETRIEVAL_RERANK_TIMEOUT"
log "POST_RETRIEVAL_RERANK_MAX_KEEP=$POST_RETRIEVAL_RERANK_MAX_KEEP"
log "SKILLSBENCH_PREBUILT_IMAGE_REGISTRY=$SKILLSBENCH_PREBUILT_IMAGE_REGISTRY"
log "SKILLSBENCH_PREBUILT_IMAGE_TAG=$SKILLSBENCH_PREBUILT_IMAGE_TAG"
log "SKILLSBENCH_PREBUILT_IMAGE_MAP=$SKILLSBENCH_PREBUILT_IMAGE_MAP"
log "SKILLSBENCH_PREBUILT_IMAGE_REQUIRE_LOCAL=$SKILLSBENCH_PREBUILT_IMAGE_REQUIRE_LOCAL"
log "SKILLSBENCH_UNIQUE_DOCKER_IMAGE_NAMES=$SKILLSBENCH_UNIQUE_DOCKER_IMAGE_NAMES"
log "COORDINATOR_VARIANT=$COORDINATOR_VARIANT"
log "FRONT_PACKET_BUDGET=$FRONT_PACKET_BUDGET"
log "SKILLSBENCH_TASK_CONTRACT_GUARD_ENABLE=$SKILLSBENCH_TASK_CONTRACT_GUARD_ENABLE"
log "SKILLSBENCH_STATE_CONSISTENCY_OVERLAY=$SKILLSBENCH_STATE_CONSISTENCY_OVERLAY"
log "SKILLSBENCH_STATE_CONSISTENCY_VERSION=$SKILLSBENCH_STATE_CONSISTENCY_VERSION"
log "SKILLSBENCH_FORCE_STATE_CONSISTENCY_OVERLAY=$SKILLSBENCH_FORCE_STATE_CONSISTENCY_OVERLAY"
log "SKILLSBENCH_FORCE_STATE_CONSISTENCY_VERSION=$SKILLSBENCH_FORCE_STATE_CONSISTENCY_VERSION"
log "SKILLSBENCH_CONTRACT_CLOSURE_ENABLE=$SKILLSBENCH_CONTRACT_CLOSURE_ENABLE"
log "SKILLSBENCH_CONTRACT_CLOSURE_VERSION=$SKILLSBENCH_CONTRACT_CLOSURE_VERSION"
log "SKILLSBENCH_CONTRACT_CLOSURE_MAX_CAUTIONS=$SKILLSBENCH_CONTRACT_CLOSURE_MAX_CAUTIONS"
log "CORE_LOCK_WARNING contract_closure_locked allow_override=$SKILLSBENCH_ALLOW_CORE_LOCK_OVERRIDE reason=protects_output_contract_and_verifier_alignment"
log "SKILLSBENCH_CONTRACT_LINTER_ENABLE=$SKILLSBENCH_CONTRACT_LINTER_ENABLE"
log "SKILLSBENCH_CONTRACT_LINTER_VERSION=$SKILLSBENCH_CONTRACT_LINTER_VERSION"
log "SKILLSBENCH_CONTRACT_REPAIR_ENABLE=$SKILLSBENCH_CONTRACT_REPAIR_ENABLE"
log "SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH=$SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH"
log "IO_GUARD_ENABLE=$IO_GUARD_ENABLE"
log "IO_GUARD_PATH=$IO_GUARD_PATH"
log "IO_GUARD_SAMPLE_SECONDS=$IO_GUARD_SAMPLE_SECONDS"
log "IO_GUARD_STABLE_SAMPLES=$IO_GUARD_STABLE_SAMPLES"
log "IO_GUARD_MAX_WAIT_SECONDS=$IO_GUARD_MAX_WAIT_SECONDS"
log "IO_GUARD_PRELAUNCH_DEVICE_THRESHOLDS w_await_ms<=$IO_GUARD_DEVICE_W_AWAIT_MS_MAX util_pct<=$IO_GUARD_DEVICE_UTIL_PCT_MAX qdepth<=$IO_GUARD_DEVICE_QDEPTH_MAX free_gb>=$IO_GUARD_MIN_FREE_GB device_fallback_to_global=$IO_GUARD_FALLBACK_TO_GLOBAL_IF_DEVICE_MISSING"
log "IO_GUARD_PRELAUNCH_HARD_GLOBAL_THRESHOLDS some.avg10<=$IO_GUARD_HARD_SOME_AVG10_MAX some.avg60<=$IO_GUARD_HARD_SOME_AVG60_MAX full.avg10<=$IO_GUARD_HARD_FULL_AVG10_MAX full.avg60<=$IO_GUARD_HARD_FULL_AVG60_MAX"
log "IO_GUARD_FALLBACK_GLOBAL_THRESHOLDS some.avg10<=$IO_GUARD_SOME_AVG10_MAX some.avg60<=$IO_GUARD_SOME_AVG60_MAX full.avg10<=$IO_GUARD_FULL_AVG10_MAX full.avg60<=$IO_GUARD_FULL_AVG60_MAX"
log "IO_TEARDOWN_COOLDOWN_ENABLE=$IO_TEARDOWN_COOLDOWN_ENABLE"
log "IO_TEARDOWN_THRESHOLDS full.avg10<=$IO_TEARDOWN_HEALTHY_FULL_AVG10 w_await_ms<=$IO_TEARDOWN_HEALTHY_W_AWAIT_MS stable_samples=$IO_TEARDOWN_HEALTHY_SAMPLES max_wait_s=$IO_TEARDOWN_MAX_WAIT_SECONDS"
log "IO_TEST_FORCE_TEARDOWN_FAILURES=$IO_TEST_FORCE_TEARDOWN_FAILURES"
log "IO_DEFER_ON_COOLDOWN_FAILURE=$IO_DEFER_ON_COOLDOWN_FAILURE"
log "IO_DEFER_MAX_ATTEMPTS=$IO_DEFER_MAX_ATTEMPTS"
log "RESUME_EXISTING_ROWS=$RESUME_EXISTING_ROWS"
log "LAUNCH_STAGGER_SECONDS=$LAUNCH_STAGGER_SECONDS"
log "MODE_STARTUP_STAGGER_SECONDS=$MODE_STARTUP_STAGGER_SECONDS"
log "MODE_STARTUP_BATCH_SIZE=$MODE_STARTUP_BATCH_SIZE"
log "SKILLSBENCH_ABORT_ON_SETUP_ENVIRONMENT_ISSUE=$SKILLSBENCH_ABORT_ON_SETUP_ENVIRONMENT_ISSUE"
log "TIMEOUT_MULTIPLIER=$TIMEOUT_MULTIPLIER"
log "GEMINI_CLI_AGENT_TIMEOUT_MULTIPLIER=$GEMINI_CLI_AGENT_TIMEOUT_MULTIPLIER"
maybe_sleep_for_mode_startup_stagger
codex_provider_preflight

PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
"$CONDA_PREFIX/bin/python" \
  experiments/retrieval_tasks_backend/prepare_retrieval_task_mirrors.py \
  --tasks-file "$TASKS_FILE" \
  --mirror-root "$MIRROR_ROOT" \
  --manifest-out "$MIRROR_MANIFEST" \
  --k "$TOP_K" \
  --retrieval-mode "$RETRIEVAL_MODE" \
  --synthesized-skill-position-mode "$SYNTHESIZED_SKILL_POSITION_MODE" \
  --coordinator-variant "$COORDINATOR_VARIANT" \
  --front-packet-budget "$FRONT_PACKET_BUDGET" \
  $([ "$POST_RETRIEVAL_RERANK_ENABLED" = "1" ] && printf '%s ' --post-retrieval-rerank-enabled) \
  --post-retrieval-rerank-top-m "$POST_RETRIEVAL_RERANK_TOP_M" \
  --post-retrieval-rerank-model "$POST_RETRIEVAL_RERANK_MODEL" \
  --post-retrieval-rerank-timeout "$POST_RETRIEVAL_RERANK_TIMEOUT" \
  $([ -n "$POST_RETRIEVAL_RERANK_MAX_KEEP" ] && printf '%s %s ' --post-retrieval-rerank-max-keep "$POST_RETRIEVAL_RERANK_MAX_KEEP") \
  2>&1 | tee -a "$MASTER_LOG"

if [ -n "$SKILLSBENCH_PREBUILT_IMAGE_REGISTRY" ] || [ -n "$SKILLSBENCH_PREBUILT_IMAGE_MAP" ]; then
  prebuilt_args=(
    --tasks-file "$TASKS_FILE"
    --mirror-root "$MIRROR_ROOT"
  )
  if [ -n "$SKILLSBENCH_PREBUILT_IMAGE_REGISTRY" ]; then
    prebuilt_args+=(--registry "$SKILLSBENCH_PREBUILT_IMAGE_REGISTRY")
  fi
  if [ -n "$SKILLSBENCH_PREBUILT_IMAGE_TAG" ]; then
    prebuilt_args+=(--tag "$SKILLSBENCH_PREBUILT_IMAGE_TAG")
  fi
  if [ -n "$SKILLSBENCH_PREBUILT_IMAGE_MAP" ]; then
    prebuilt_args+=(--image-map "$SKILLSBENCH_PREBUILT_IMAGE_MAP")
  fi
  if [ "$SKILLSBENCH_PREBUILT_IMAGE_REQUIRE_LOCAL" = "1" ]; then
    prebuilt_args+=(--require-local-images)
  fi

  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  "$CONDA_PREFIX/bin/python" \
    experiments/retrieval_tasks_backend/apply_prebuilt_docker_images.py \
    "${prebuilt_args[@]}" \
    2>&1 | tee -a "$MASTER_LOG"
fi

run_one_task() {
  local idx="$1"
  local task="$2"
  local mirror_path="$MIRROR_ROOT/$task"
  local job_name
  local mode_slug
  local variant_slug
  local task_log
  local hot_task_log
  local row_json_path
  local row_txt_path
  local rc
  local summarize_rc
  local row_txt_rc
  local task_log_summary_rc
  local sync_rc
  local one_json
  local overlay_file
  local summary_marker
  local final_job_dir
  local job_dir_for_results
  local trial_dir
  local setup_return_code_file
  local command0_return_code_file
  local command1_return_code_file
  local entered_loop=0
  local verifier_started=0
  local command0_return_code
  local command1_return_code
  local setup_return_code
  local setup_success=0
  local need_fallback=0
  local fallback_reason=""
  local job_result_path
  local harbor_rc
  local state_consistency_enabled=0
  local state_consistency_version=""
  local termination_reason=""
  local command1_backfill=""
  local command1_source="missing"
  local verifier_state_source="unknown"
  local -a harbor_run_cmd
  local -a harbor_env_cmd
  mode_slug="${RETRIEVAL_MODE//[^a-zA-Z0-9_-]/_}"
  variant_slug="${COORDINATOR_VARIANT//[^a-zA-Z0-9_-]/_}"
  job_name=$(printf "retrieval-tasks-backend-%02d-%s-%s-%s-%s" "$idx" "$variant_slug" "$mode_slug" "$task" "$RUN_TS")
  task_log="$OUT_DIR/${job_name}.log"
  hot_task_log="$HOT_OUT_DIR/${job_name}.log"
  row_json_path="$ROW_DIR/$(printf '%02d' "$idx")-${task}.json"
  row_txt_path="$ROW_TXT_DIR/$(printf '%02d' "$idx")-${task}.txt"

  log "============================================================"
  log "[$idx/${#TASKS[@]}] START TASK=$task"
  log "JOB_NAME=$job_name"
  log "MIRROR_PATH=$mirror_path"
  if [ "$HARBOR_PROFILE" = "gemini-cli" ]; then
    log "GEMINI_CHILD_FINGERPRINT task=${task} key_len=${GEMINI_CHILD_ENV_KEY_LEN:-0} key_sha256_12=${GEMINI_CHILD_ENV_KEY_SHA256_12:-unknown} mode=${GEMINI_CLI_MODE:-unknown} base_url=${GEMINI_CLI_DIRECT_BASE_URL:-unknown} model=${HARBOR_MODEL:-unknown}"
  fi
  log "TASK_LOG=$task_log"
  if [ "$hot_task_log" != "$task_log" ]; then
    log "TASK_LOG_SCRATCH=$hot_task_log"
  fi
  overlay_file=""
  if [ -f "$mirror_path/READ_FIRST.md" ]; then
    overlay_file="$mirror_path/READ_FIRST.md"
    log "TASK_OVERLAY_FILE=$overlay_file"
  fi
  overlay_file="$(prepare_contract_closure_overlay "$task" "$mirror_path" "$overlay_file")"
  if [ -n "$overlay_file" ]; then
    log "TASK_EFFECTIVE_OVERLAY_FILE=$overlay_file"
  fi

  harbor_run_cmd=(
    harbor run
    -p "$mirror_path"
    -e docker
    -a "$HARBOR_AGENT"
  )
  if [ -n "$HARBOR_MODEL" ]; then
    harbor_run_cmd+=(-m "$HARBOR_MODEL")
  fi
  if [ "${#HARBOR_AGENT_KWARGS[@]}" -gt 0 ]; then
    harbor_run_cmd+=("${HARBOR_AGENT_KWARGS[@]}")
  fi
  harbor_run_cmd+=(
    --job-name "$job_name"
    --timeout-multiplier "$TIMEOUT_MULTIPLIER"
    -o "$HOT_JOBS_DIR"
  )
  if [ "$HARBOR_PROFILE" = "gemini-cli" ]; then
    harbor_run_cmd+=(--agent-timeout-multiplier "$GEMINI_CLI_AGENT_TIMEOUT_MULTIPLIER")
  fi

  harbor_env_cmd=(
    env
    "PYTHONPATH=${SKILLSBENCH_PYTHONPATH}"
    "SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_FILE=${overlay_file}"
    "SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_TEXT="
    "SKILLSBENCH_OUTPUT_CONTRACT_FILE=${mirror_path}/OUTPUT_CONTRACT.json"
    "SKILLSBENCH_CONTRACT_LINTER_ENABLE=${SKILLSBENCH_CONTRACT_LINTER_ENABLE}"
    "SKILLSBENCH_CONTRACT_LINTER_VERSION=${SKILLSBENCH_CONTRACT_LINTER_VERSION}"
    "SKILLSBENCH_CONTRACT_REPAIR_ENABLE=${SKILLSBENCH_CONTRACT_REPAIR_ENABLE}"
    "SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH=${SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH}"
  )
  if [ "$HARBOR_PROFILE" = "gemini-cli" ]; then
    harbor_env_cmd=(env -u SKILLSBENCH_GEMINI_UPSTREAM_BASE_URL -u OPENAI_BASE_URL -u OPENAI_API_BASE -u OPENAI_API_KEY -u VECTORENGINE_API_KEY)
    harbor_env_cmd+=(
      "PYTHONPATH=${SKILLSBENCH_PYTHONPATH}"
      "SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_FILE=${overlay_file}"
      "SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_TEXT="
      "SKILLSBENCH_OUTPUT_CONTRACT_FILE=${mirror_path}/OUTPUT_CONTRACT.json"
      "SKILLSBENCH_CONTRACT_LINTER_ENABLE=${SKILLSBENCH_CONTRACT_LINTER_ENABLE}"
      "SKILLSBENCH_CONTRACT_LINTER_VERSION=${SKILLSBENCH_CONTRACT_LINTER_VERSION}"
      "SKILLSBENCH_CONTRACT_REPAIR_ENABLE=${SKILLSBENCH_CONTRACT_REPAIR_ENABLE}"
      "SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH=${SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH}"
    )
  fi
  if [ "${#HARBOR_RUN_ENV[@]}" -gt 0 ]; then
    harbor_env_cmd+=("${HARBOR_RUN_ENV[@]}")
  fi

  set +e
  timeout --preserve-status --kill-after "${COMMAND1_TIMEOUT_KILL_SECONDS}s" "${COMMAND1_TIMEOUT_SECONDS}s" \
    "${harbor_env_cmd[@]}" \
    "${harbor_run_cmd[@]}" \
    >> "$hot_task_log" 2>&1
  rc=$?
  set -e
  harbor_rc="$rc"
  log "TASK_FINISHED task=$task rc=$rc"

  if [ "$rc" -eq "$COMMAND1_TIMEOUT_RC" ]; then
    need_fallback=1
    fallback_reason="harbor_run_timeout"
  elif [ "$rc" -eq 137 ] || [ "$rc" -eq 143 ]; then
    need_fallback=1
    fallback_reason="harbor_run_killed"
  fi

  if [[ "$COORDINATOR_VARIANT" == *_state_consistent ]]; then
    state_consistency_enabled=1
    state_consistency_version="v1"
  elif [ "$SKILLSBENCH_FORCE_STATE_CONSISTENCY_OVERLAY" = "1" ]; then
    state_consistency_enabled=1
    state_consistency_version="$SKILLSBENCH_FORCE_STATE_CONSISTENCY_VERSION"
  fi

  set +e
  sync_task_artifacts_to_final "$job_name" "$hot_task_log" "$task_log"
  sync_rc=$?
  set -e
  if [ "$sync_rc" -ne 0 ]; then
    log "TASK_ARTIFACT_SYNC_FAILED task=$task job_name=$job_name rc=$sync_rc"
  fi

  final_job_dir="$JOBS_DIR/$job_name"
  job_dir_for_results="$final_job_dir"
  trial_dir="$(resolve_trial_dir "$final_job_dir")"
  if [ -z "$trial_dir" ] && [ "$HOT_JOBS_DIR" != "$JOBS_DIR" ]; then
    trial_dir="$(resolve_trial_dir "$HOT_JOBS_DIR/$job_name")"
  fi
  setup_return_code_file="$trial_dir/agent/setup/return-code.txt"
  command0_return_code_file="$trial_dir/agent/command-0/return-code.txt"
  command1_return_code_file="$trial_dir/agent/command-1/return-code.txt"
  if [ -n "$trial_dir" ] && [ -f "${trial_dir}/agent/${AGENT_LOG_BASENAME}" ]; then
    entered_loop=1
  fi
  if [ -n "$trial_dir" ] && [ -d "${trial_dir}/verifier" ] && [ -n "$(find "${trial_dir}/verifier" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    verifier_started=1
  fi
  if [ -n "$trial_dir" ] && [ -f "${trial_dir}/result.json" ]; then
    job_result_path="$trial_dir/result.json"
  elif [ -f "$job_dir_for_results/result.json" ]; then
    job_result_path="$job_dir_for_results/result.json"
  else
    job_result_path=""
  fi
  setup_return_code="$(read_return_code_txt "$setup_return_code_file")"
  command0_return_code="$(read_return_code_txt "$command0_return_code_file")"
  command1_return_code="$(read_return_code_txt "$command1_return_code_file")"
  if [ "$setup_return_code" = "0" ]; then
    setup_success=1
  fi
  if [ "$verifier_started" = "1" ]; then
    verifier_state_source="trial/verifier_dir"
  elif [ -n "$trial_dir" ]; then
    verifier_state_source="trial/verifier_absent"
  else
    verifier_state_source="missing_trial_dir"
  fi
  if [ -n "$command1_return_code" ]; then
    command1_backfill="$command1_return_code"
    command1_source="agent/command-1/return-code.txt"
  elif [[ "$harbor_rc" =~ ^-?[0-9]+$ ]]; then
    command1_backfill="$harbor_rc"
    command1_source="runner_harbor_rc"
  fi
  if [ "$rc" -eq "$COMMAND1_TIMEOUT_RC" ]; then
    termination_reason="runner_timeout"
  elif [ "$rc" -eq 137 ] || [ "$rc" -eq 143 ]; then
    termination_reason="runner_killed"
  elif [ -n "$setup_return_code" ] && [ "$setup_return_code" != "0" ]; then
    termination_reason="agent_setup_nonzero_return"
  elif [ -n "$command1_return_code" ] && [ "$command1_return_code" = "0" ]; then
    termination_reason="agent_command_completed"
  elif [ -n "$command1_return_code" ]; then
    termination_reason="agent_command_nonzero_return"
  elif [ -z "$trial_dir" ]; then
    termination_reason="missing_trial_dir"
  elif [ -z "$job_result_path" ]; then
    termination_reason="missing_result_json"
  else
    termination_reason="missing_command1_return_code"
  fi
  if [ "$state_consistency_enabled" -eq 1 ]; then
    set +e
    write_state_consistency_overlay \
      "$trial_dir" "$final_job_dir" "$state_consistency_enabled" "$state_consistency_version" \
      "$harbor_rc" "$fallback_reason" "$termination_reason" "$command1_backfill" \
      "$command1_source" "$verifier_started" "$verifier_state_source"
    set -e
  fi
  if [ "$need_fallback" -eq 0 ] && { [ -z "$command1_return_code" ] || [ -z "$job_result_path" ]; }; then
    need_fallback=1
    fallback_reason="missing_command1_return_or_result"
  fi
  if [ "$state_consistency_enabled" -eq 1 ]; then
    need_fallback=0
    if [ -z "$fallback_reason" ] && { [ -z "$command1_return_code" ] || [ -z "$job_result_path" ]; }; then
      fallback_reason="state_consistency_backfill_required"
    fi
  fi

  set +e
  if [ "$need_fallback" -eq 1 ]; then
    one_json="$(emit_fallback_row_json "$task" "$idx" "$job_name" "$mirror_path" "$command1_return_code" "$command0_return_code" "$setup_success" "$entered_loop" "$verifier_started" "$trial_dir" "$final_job_dir" "$harbor_rc" "$fallback_reason")"
    summarize_rc=0
  else
    one_json="$(summarize_one "$task" "$job_name" "$mirror_path")"
    summarize_rc=$?
  fi
  set -e

  if [ "$summarize_rc" -ne 0 ] || [ -z "$one_json" ]; then
    log "ROW_SUMMARY_FAILED task=$task summarize_rc=$summarize_rc"
    one_json="$(python3 -S - <<PY
import json
print(json.dumps({
    "task": "$task",
    "job_name": "$job_name",
    "mirror_path": r"""$mirror_path""",
    "job_path": r"""$JOBS_DIR/$job_name""",
    "trial_path": None,
    "trial_started_at": None,
    "trial_finished_at": None,
    "trial_duration_seconds": None,
    "environment_setup_duration_seconds": None,
    "agent_setup_duration_seconds": None,
    "agent_execution_duration_seconds": None,
    "verifier_duration_seconds": None,
    "token_source": None,
    "total_tokens_used": None,
    "token_thread_count": None,
    "agent_result_input_tokens": None,
    "agent_result_cache_tokens": None,
    "agent_result_output_tokens": None,
    "setup_success": False,
    "entered_loop": False,
    "command_0_return_code": None,
    "command_1_return_code": None,
    "command1_return_code": None,
    "command_1_return_code_source": "runner_summary_failed",
    "agent_success": None,
    "verifier_started": False,
    "verifier_started_source": "runner_summary_failed",
    "trial_result": False,
    "job_result": False,
    "reward": None,
    "verifier_reward": None,
    "job_mean": None,
    "variant_id": None,
    "injected_skill_list": [],
    "coordinator_path": None,
    "retrieved_skill_paths": [],
    "front_packet_path": None,
    "front_packet_total_tokens": None,
    "front_packet_section_tokens": {},
    "front_packet_tokenizer": None,
    "actual_skill_md_files_opened": [],
    "first_skill_opened": None,
    "coordinator_opened": False,
    "target_retrieved_skills_opened": [],
    "output_contract_targets": [],
    "output_contract_module_targets": [],
    "output_files_created": [],
    "target_output_created": False,
    "target_output_created_source": "runner_summary_failed",
    "timeout_or_exception": True,
    "verifier_failure_message": None,
    "termination_reason": "runner_summary_failed",
    "termination_reason_source": "runner_fallback",
    "state_consistency_enabled": bool(str("$COORDINATOR_VARIANT").endswith("_state_consistent")),
    "state_consistency_version": "v1" if str("$COORDINATOR_VARIANT").endswith("_state_consistent") else None,
    "state_consistency_backfill_applied": False,
    "state_consistency_overlay_path": None,
    "failure_bucket": None,
    "exception": f"RUNNER_SUMMARY_FAILED rc=$summarize_rc",
}, ensure_ascii=False))
PY
)"
  fi

  printf '%s\n' "$one_json" > "$row_json_path"

  set +e
  write_row_txt "$row_json_path" "$row_txt_path"
  row_txt_rc=$?
  set -e
  if [ "$row_txt_rc" -ne 0 ]; then
    log "ROW_TXT_FAILED task=$task row_txt_rc=$row_txt_rc"
    cat > "$row_txt_path" <<EOF
------------------------------------------------------------
TASK: $task
JOB_NAME: $job_name
ROW_JSON_PATH: $row_json_path
EXCEPTION: RUNNER_ROW_TXT_FAILED rc=$row_txt_rc
------------------------------------------------------------
EOF
  fi

  set +e
  append_row_summary_to_task_log "$row_json_path" "$task_log"
  task_log_summary_rc=$?
  set -e
  if [ "$task_log_summary_rc" -ne 0 ]; then
    log "TASK_LOG_SUMMARY_APPEND_FAILED task=$task rc=$task_log_summary_rc task_log=$task_log"
  else
    log "TASK_LOG_SUMMARY_APPENDED task=$task task_log=$task_log"
  fi
  summary_marker="$(summary_marker_for_row_json "$row_json_path")"
  if [ "$need_fallback" -eq 1 ]; then
    set +e
    append_row_summary_to_jsonl_once "$row_json_path" "$summary_marker"
    task_log_summary_rc=$?
    set -e
    if [ "$task_log_summary_rc" -ne 0 ]; then
      log "SUMMARY_APPEND_DIRECT_FAILED task=$task row_json=$row_json_path rc=$task_log_summary_rc"
    else
      log "SUMMARY_APPEND_DIRECT task=$task row_json=$row_json_path"
    fi
  fi

  log "ROW_WRITTEN task=$task row_json=$row_json_path row_txt=$row_txt_path"
  if [ "$SKILLSBENCH_ABORT_ON_SETUP_ENVIRONMENT_ISSUE" = "1" ] && row_has_setup_environment_issue "$row_json_path"; then
    log "INFRA_SETUP_FAILURE_ABORT task=$task row_json=$row_json_path reason=setup_environment_issue"
    printf 'setup_environment_issue task=%s row=%s\n' "$task" "$row_json_path" > "$OUT_DIR/.abort_requested"
    return 86
  elif row_has_setup_environment_issue "$row_json_path"; then
    log "INFRA_SETUP_FAILURE_SKIP task=$task row_json=$row_json_path reason=setup_environment_issue"
  fi
  return 0
}

wait_for_safe_launch_window() {
  local task="$1"
  local guard_rc

  if [ "$IO_GUARD_ENABLE" != "1" ]; then
    return 0
  fi

  log "IO_GUARD_BEGIN task=$task"
  set +e
  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  "$CONDA_PREFIX/bin/python" \
    experiments/retrieval_tasks_backend/io_pressure_guard.py \
    --mode prelaunch \
    --label "$task" \
    --path "$IO_GUARD_PATH" \
    --sample-seconds "$IO_GUARD_SAMPLE_SECONDS" \
    --stable-samples "$IO_GUARD_STABLE_SAMPLES" \
    --max-wait-seconds "$IO_GUARD_MAX_WAIT_SECONDS" \
    --max-some-avg10 "$IO_GUARD_SOME_AVG10_MAX" \
    --max-some-avg60 "$IO_GUARD_SOME_AVG60_MAX" \
    --max-full-avg10 "$IO_GUARD_FULL_AVG10_MAX" \
    --max-full-avg60 "$IO_GUARD_FULL_AVG60_MAX" \
    --device "${IO_CGROUP_DEVICE:-}" \
    --max-device-w-await-ms "$IO_GUARD_DEVICE_W_AWAIT_MS_MAX" \
    --max-device-util-pct "$IO_GUARD_DEVICE_UTIL_PCT_MAX" \
    --max-device-qdepth "$IO_GUARD_DEVICE_QDEPTH_MAX" \
    --hard-max-some-avg10 "$IO_GUARD_HARD_SOME_AVG10_MAX" \
    --hard-max-some-avg60 "$IO_GUARD_HARD_SOME_AVG60_MAX" \
    --hard-max-full-avg10 "$IO_GUARD_HARD_FULL_AVG10_MAX" \
    --hard-max-full-avg60 "$IO_GUARD_HARD_FULL_AVG60_MAX" \
    --fallback-to-global-if-device-missing "$IO_GUARD_FALLBACK_TO_GLOBAL_IF_DEVICE_MISSING" \
    --min-free-gb "$IO_GUARD_MIN_FREE_GB" \
    2>&1 | tee -a "$MASTER_LOG"
  guard_rc=${PIPESTATUS[0]}
  set -e

  if [ "$guard_rc" -ne 0 ]; then
    log "IO_GUARD_FAILED task=$task rc=$guard_rc"
    set +e
    return "$guard_rc"
  fi

  log "IO_GUARD_END task=$task"
  return 0
}

wait_for_teardown_cooldown() {
  local next_task="$1"
  local guard_rc

  if [ "$IO_TEARDOWN_COOLDOWN_ENABLE" != "1" ]; then
    return 0
  fi

  log "IO_TEARDOWN_BEGIN next_task=$next_task"
  if [ "${IO_TEST_FORCE_TEARDOWN_FAILURES:-0}" -gt 0 ]; then
    IO_TEST_FORCE_TEARDOWN_FAILURES=$((IO_TEST_FORCE_TEARDOWN_FAILURES-1))
    log "IO_TEARDOWN_TEST_FORCED next_task=$next_task remaining=$IO_TEST_FORCE_TEARDOWN_FAILURES"
    set +e
    return 1
  fi
  set +e
  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  "$CONDA_PREFIX/bin/python" \
    experiments/retrieval_tasks_backend/io_pressure_guard.py \
    --mode teardown \
    --label "teardown->$next_task" \
    --path "$IO_GUARD_PATH" \
    --sample-seconds "$IO_GUARD_SAMPLE_SECONDS" \
    --stable-samples "$IO_TEARDOWN_HEALTHY_SAMPLES" \
    --max-wait-seconds "$IO_TEARDOWN_MAX_WAIT_SECONDS" \
    --max-some-avg10 999 \
    --max-some-avg60 999 \
    --max-full-avg10 "$IO_TEARDOWN_HEALTHY_FULL_AVG10" \
    --max-full-avg60 999 \
    --device "${IO_CGROUP_DEVICE:-}" \
    --max-w-await-ms "$IO_TEARDOWN_HEALTHY_W_AWAIT_MS" \
    --min-free-gb 0 \
    2>&1 | tee -a "$MASTER_LOG"
  guard_rc=${PIPESTATUS[0]}
  set -e

  if [ "$guard_rc" -ne 0 ]; then
    log "IO_TEARDOWN_FAILED next_task=$next_task rc=$guard_rc"
    set +e
    return "$guard_rc"
  fi

  log "IO_TEARDOWN_END next_task=$next_task"
  return 0
}

defer_task() {
  local task_idx="$1"
  local reason="$2"
  local rc="$3"
  local attempts task

  task="${TASKS[$task_idx]-}"
  attempts="${TASK_DEFER_COUNTS[$task_idx]-0}"
  attempts=$((attempts+1))
  TASK_DEFER_COUNTS[$task_idx]="$attempts"
  log "TASK_DEFER_ATTEMPT task=${task:-UNKNOWN} idx=$((task_idx+1)) attempts=$attempts/$IO_DEFER_MAX_ATTEMPTS reason=$reason rc=$rc"

  if [ "$attempts" -gt "$IO_DEFER_MAX_ATTEMPTS" ]; then
    log "TASK_DEFER_EXHAUSTED task=$task idx=$((task_idx+1)) attempts=$attempts reason=$reason rc=$rc"
    return 1
  fi

  log "TASK_DEFERRED task=$task idx=$((task_idx+1)) attempts=$attempts/$IO_DEFER_MAX_ATTEMPTS reason=$reason rc=$rc"
  TASK_QUEUE+=("$task_idx")
  return 0
}

maybe_defer_task() {
  local task_idx="$1"
  local reason="$2"
  local rc="$3"

  log "TASK_DEFER_ENTER task=${TASKS[$task_idx]-UNKNOWN} idx=$((task_idx+1)) reason=$reason rc=$rc enabled=$IO_DEFER_ON_COOLDOWN_FAILURE"
  if [ "$IO_DEFER_ON_COOLDOWN_FAILURE" != "1" ]; then
    log "TASK_DEFER_BYPASS task=${TASKS[$task_idx]-UNKNOWN} idx=$((task_idx+1)) reason=$reason rc=$rc"
    return 1
  fi

  defer_task "$task_idx" "$reason" "$rc"
}

row_json_path_for_task() {
  local idx="$1"
  local task="$2"
  printf "%s/%02d-%s.json" "$ROW_DIR" "$idx" "$task"
}

row_txt_path_for_task() {
  local idx="$1"
  local task="$2"
  printf "%s/%02d-%s.txt" "$ROW_TXT_DIR" "$idx" "$task"
}

declare -A TASK_DEFER_COUNTS=()
TASK_QUEUE=()
for i in "${!TASKS[@]}"; do
  idx=$((i+1))
  task="${TASKS[$i]}"
  row_json_path="$(row_json_path_for_task "$idx" "$task")"
  if [ "$RESUME_EXISTING_ROWS" = "1" ] && [ -f "$row_json_path" ]; then
    log "SKIP_EXISTING_ROW task=$task idx=$idx row_json=$row_json_path"
    continue
  fi
  TASK_QUEUE+=("$i")
done

log "PENDING_TASK_COUNT=${#TASK_QUEUE[@]}"

if [ "$MAX_PARALLEL" -le 1 ]; then
  need_teardown_cooldown=0
  while [ "${#TASK_QUEUE[@]}" -gt 0 ]; do
    abort_if_requested "serial_loop"
    i="${TASK_QUEUE[0]}"
    TASK_QUEUE=("${TASK_QUEUE[@]:1}")
    idx=$((i+1))
    if [ "$need_teardown_cooldown" -eq 1 ]; then
      set +e
      wait_for_teardown_cooldown "${TASKS[$i]}"
      rc=$?
      set -e
      if [ "$rc" -ne 0 ]; then
        log "TASK_CONTROL teardown_failed task=${TASKS[$i]} idx=$idx rc=$rc queue_len=${#TASK_QUEUE[@]} running=0"
        set +e
        maybe_defer_task "$i" "teardown" "$rc"
        defer_rc=$?
        set -e
        log "TASK_CONTROL teardown_defer_result task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc queue_len=${#TASK_QUEUE[@]}"
        if [ "$defer_rc" -eq 0 ]; then
          need_teardown_cooldown=1
          log "TASK_CONTROL teardown_continue task=${TASKS[$i]} idx=$idx queue_len=${#TASK_QUEUE[@]}"
          continue
        fi
        log "TASK_CONTROL teardown_abort task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc"
        exit 1
      fi
    fi
    set +e
    wait_for_safe_launch_window "${TASKS[$i]}"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      log "TASK_CONTROL launch_failed task=${TASKS[$i]} idx=$idx rc=$rc queue_len=${#TASK_QUEUE[@]} running=0"
      set +e
      maybe_defer_task "$i" "launch_window" "$rc"
      defer_rc=$?
      set -e
      log "TASK_CONTROL launch_defer_result task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc queue_len=${#TASK_QUEUE[@]}"
      if [ "$defer_rc" -eq 0 ]; then
        need_teardown_cooldown=0
        log "TASK_CONTROL launch_continue task=${TASKS[$i]} idx=$idx queue_len=${#TASK_QUEUE[@]}"
        continue
      fi
      log "TASK_CONTROL launch_abort task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc"
      exit 1
    fi
    run_one_task "$idx" "${TASKS[$i]}"
    need_teardown_cooldown=1
  done
else
  running=0
  need_teardown_cooldown=0
  while [ "${#TASK_QUEUE[@]}" -gt 0 ]; do
    abort_if_requested "parallel_loop"
    i="${TASK_QUEUE[0]}"
    TASK_QUEUE=("${TASK_QUEUE[@]:1}")
    idx=$((i+1))
    if [ "$need_teardown_cooldown" -eq 1 ]; then
      set +e
      wait_for_teardown_cooldown "${TASKS[$i]}"
      rc=$?
      set -e
      if [ "$rc" -ne 0 ]; then
        log "TASK_CONTROL teardown_failed task=${TASKS[$i]} idx=$idx rc=$rc queue_len=${#TASK_QUEUE[@]} running=$running"
        set +e
        maybe_defer_task "$i" "teardown" "$rc"
        defer_rc=$?
        set -e
        log "TASK_CONTROL teardown_defer_result task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc queue_len=${#TASK_QUEUE[@]} running=$running"
        if [ "$defer_rc" -eq 0 ]; then
          need_teardown_cooldown=1
          log "TASK_CONTROL teardown_continue task=${TASKS[$i]} idx=$idx queue_len=${#TASK_QUEUE[@]} running=$running"
          continue
        fi
        log "TASK_CONTROL teardown_abort task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc running=$running"
        exit 1
      fi
      need_teardown_cooldown=0
    fi
    if [ "$running" -gt 0 ] && [ "$LAUNCH_STAGGER_SECONDS" -gt 0 ]; then
      log "LAUNCH_STAGGER sleeping=${LAUNCH_STAGGER_SECONDS}s next_task=${TASKS[$i]}"
      sleep "$LAUNCH_STAGGER_SECONDS"
    fi
    set +e
    wait_for_safe_launch_window "${TASKS[$i]}"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      log "TASK_CONTROL launch_failed task=${TASKS[$i]} idx=$idx rc=$rc queue_len=${#TASK_QUEUE[@]} running=$running"
      set +e
      maybe_defer_task "$i" "launch_window" "$rc"
      defer_rc=$?
      set -e
      log "TASK_CONTROL launch_defer_result task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc queue_len=${#TASK_QUEUE[@]} running=$running"
      if [ "$defer_rc" -eq 0 ]; then
        log "TASK_CONTROL launch_continue task=${TASKS[$i]} idx=$idx queue_len=${#TASK_QUEUE[@]} running=$running"
        continue
      fi
      log "TASK_CONTROL launch_abort task=${TASKS[$i]} idx=$idx rc=$rc defer_rc=$defer_rc running=$running"
      exit 1
    fi
    run_one_task "$idx" "${TASKS[$i]}" &
    running=$((running+1))
    if [ "$running" -ge "$MAX_PARALLEL" ]; then
      if wait -n; then
        :
      else
        log "WAIT_N_NONZERO running=$running"
      fi
      running=$((running-1))
      abort_if_requested "parallel_wait"
      need_teardown_cooldown=1
    fi
  done
  while [ "$running" -gt 0 ]; do
    if wait -n; then
      :
    else
      log "WAIT_N_NONZERO running=$running"
    fi
    running=$((running-1))
    abort_if_requested "parallel_drain"
  done
fi

 
for i in "${!TASKS[@]}"; do
  idx=$((i+1))
  task="${TASKS[$i]}"
  row_json_path="$(row_json_path_for_task "$idx" "$task")"
  row_txt_path="$(row_txt_path_for_task "$idx" "$task")"
  summary_marker="$(summary_marker_for_row_json "$row_json_path")"
  if [ -f "$row_json_path" ]; then
    if [ ! -f "$summary_marker" ]; then
      cat "$row_json_path" >> "$SUMMARY_JSON"
    fi
  else
    log "SUMMARY_MISSING_ROW_JSON task=$task path=$row_json_path"
  fi
  if [ -f "$row_txt_path" ]; then
    cat "$row_txt_path" >> "$SUMMARY_TXT"
  else
    log "SUMMARY_MISSING_ROW_TXT task=$task path=$row_txt_path"
  fi
done

python3 -S - <<PY >> "$SUMMARY_TXT"
import json
from collections import Counter
from pathlib import Path

def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def format_seconds(value):
    if not is_number(value):
        return "NA"
    return f"{value:.3f}"

summary_file = Path("$SUMMARY_JSON")
rows = [json.loads(line) for line in summary_file.read_text().splitlines() if line.strip()]

reward_ones = sum(1 for r in rows if r.get("reward") == 1.0)
reward_zeros = sum(1 for r in rows if r.get("reward") == 0.0)
reward_partial = sum(1 for r in rows if isinstance(r.get("reward"), (int, float)) and r.get("reward") not in (0.0, 1.0))
reward_missing = sum(1 for r in rows if r.get("reward") is None)
agent_success_rows = sum(1 for r in rows if r.get("agent_success") is True)
agent_failure_rows = sum(1 for r in rows if r.get("agent_success") is False)
agent_unknown_rows = sum(1 for r in rows if r.get("agent_success") is None)
failure_bucket_counts = Counter(str(r.get("failure_bucket") or "success_or_unbucketed") for r in rows)
setup_environment_issue_rows = failure_bucket_counts.get("setup_environment_issue", 0)
token_values = [int(r["total_tokens_used"]) for r in rows if is_number(r.get("total_tokens_used"))]
trial_duration_values = [float(r["trial_duration_seconds"]) for r in rows if is_number(r.get("trial_duration_seconds"))]
agent_execution_duration_values = [float(r["agent_execution_duration_seconds"]) for r in rows if is_number(r.get("agent_execution_duration_seconds"))]

print("=============== FINAL SUMMARY ===============")
print(f"TOTAL_TASKS: {len(rows)}")
print(f"REWARD_EQ_1: {reward_ones}")
print(f"REWARD_EQ_0: {reward_zeros}")
print(f"REWARD_PARTIAL: {reward_partial}")
print(f"REWARD_NULL_OR_MISSING: {reward_missing}")
print(f"AGENT_SUCCESS_ROWS: {agent_success_rows}")
print(f"AGENT_FAILURE_ROWS: {agent_failure_rows}")
print(f"AGENT_SUCCESS_UNKNOWN_ROWS: {agent_unknown_rows}")
print(f"SETUP_ENVIRONMENT_ISSUE_ROWS: {setup_environment_issue_rows}")
print(f"TOKEN_METRIC_ROWS: {len(token_values)}")
print(f"TOTAL_TOKENS_USED: {sum(token_values) if token_values else 'NA'}")
print(f"AVG_TOKENS_PER_TASK: {round(sum(token_values) / len(token_values), 3) if token_values else 'NA'}")
print(f"TRIAL_DURATION_ROWS: {len(trial_duration_values)}")
print(f"TOTAL_TRIAL_DURATION_SECONDS: {round(sum(trial_duration_values), 3) if trial_duration_values else 'NA'}")
print(f"AVG_TRIAL_DURATION_SECONDS: {round(sum(trial_duration_values) / len(trial_duration_values), 3) if trial_duration_values else 'NA'}")
print(f"AGENT_EXECUTION_DURATION_ROWS: {len(agent_execution_duration_values)}")
print(f"TOTAL_AGENT_EXECUTION_DURATION_SECONDS: {round(sum(agent_execution_duration_values), 3) if agent_execution_duration_values else 'NA'}")
print(f"AVG_AGENT_EXECUTION_DURATION_SECONDS: {round(sum(agent_execution_duration_values) / len(agent_execution_duration_values), 3) if agent_execution_duration_values else 'NA'}")
print("FAILURE_BUCKET_COUNTS:")
for bucket, count in sorted(failure_bucket_counts.items()):
    print(f"  {bucket}: {count}")
print()
for r in rows:
    print(
        f"- {r.get('task')}: "
        f"mirror={r.get('mirror_path')}, "
        f"setup_success={r.get('setup_success')}, "
        f"cmd0={r.get('command_0_return_code')}, "
        f"cmd1={r.get('command_1_return_code')}, "
        f"agent_success={r.get('agent_success')}, "
        f"reward={r.get('reward')}, "
        f"failure_bucket={r.get('failure_bucket')}, "
        f"tokens={r.get('total_tokens_used')}, "
        f"trial_seconds={format_seconds(r.get('trial_duration_seconds'))}, "
        f"agent_exec_seconds={format_seconds(r.get('agent_execution_duration_seconds'))}, "
        f"trial={r.get('trial_path')}"
    )
PY

echo
echo "DONE"
echo "MASTER_LOG=$MASTER_LOG"
echo "SUMMARY_JSON=$SUMMARY_JSON"
echo "SUMMARY_TXT=$SUMMARY_TXT"
echo "MIRROR_MANIFEST=$MIRROR_MANIFEST"
echo "OUT_DIR=$OUT_DIR"
