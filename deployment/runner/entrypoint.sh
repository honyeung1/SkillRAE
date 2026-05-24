#!/usr/bin/env bash

set -euo pipefail

: "${SKILLSBENCH_REPO_ROOT:=/workspace/skillsbench-private}"
: "${SKILLSBENCH_ARTIFACT_ROOT:=/workspace/artifacts}"
: "${SKILLSBENCH_RUNS_ROOT:=${SKILLSBENCH_ARTIFACT_ROOT}/runs}"
: "${SKILLSBENCH_SHARED_JOBS_DIR:=${SKILLSBENCH_ARTIFACT_ROOT}/jobs}"
: "${SKILLSBENCH_CACHE_ROOT:=${SKILLSBENCH_ARTIFACT_ROOT}/.cache}"
: "${SKILLSBENCH_RUNTIME_PYTHON_BIN:=/opt/skillsbench-runner/bin/python}"
: "${SKILLSBENCH_DOCKER_MODE:=socket}"
: "${SKILLSBENCH_DOCKER_SOCKET:=/var/run/docker.sock}"
: "${SKILLSBENCH_ENV_FILE:=/workspace/skillsbench-private/deployment/runner/runner.env}"
: "${SKILLSBENCH_API_ENV_FILE:=/workspace/skillsbench-private/deployment/runner/api.env}"
: "${CODEX_HOME:=/root/.codex}"

export SKILLSBENCH_RUNNER_CONTAINER=1
export SKILLSBENCH_REPO_ROOT
export SKILLSBENCH_ARTIFACT_ROOT
export SKILLSBENCH_RUNS_ROOT
export SKILLSBENCH_SHARED_JOBS_DIR
export SKILLSBENCH_CACHE_ROOT
export SKILLSBENCH_RUNTIME_PYTHON_BIN
export SKILLSBENCH_DOCKER_MODE
export SKILLSBENCH_DOCKER_SOCKET
export SKILLSBENCH_ENV_FILE
export SKILLSBENCH_API_ENV_FILE
export CODEX_HOME
export PATH="/opt/skillsbench-runner/bin:/root/.local/bin:${PATH}"

resolve_codex_config_path() {
  local resolved

  resolved="$(
    PYTHONPATH="${SKILLSBENCH_REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
      python3 -S -m skillsbench_private.codex_config_resolver \
        --repo-root "${SKILLSBENCH_REPO_ROOT}" 2>/dev/null || true
  )"
  printf '%s\n' "$resolved" | sed '/^[[:space:]]*$/d' | tail -n 1
}

mkdir -p "$SKILLSBENCH_ARTIFACT_ROOT" "$SKILLSBENCH_RUNS_ROOT" "$SKILLSBENCH_SHARED_JOBS_DIR" "$SKILLSBENCH_CACHE_ROOT"
RESOLVED_CODEX_CONFIG_PATH="$(resolve_codex_config_path)"
if [ -n "${RESOLVED_CODEX_CONFIG_PATH}" ]; then
  export SKILLSBENCH_CODEX_CONFIG_PATH="${RESOLVED_CODEX_CONFIG_PATH}"
  export CODEX_CONFIG_PATH="${RESOLVED_CODEX_CONFIG_PATH}"
fi
if [ ! -f "${CODEX_HOME}/config.toml" ] && [ -n "${RESOLVED_CODEX_CONFIG_PATH}" ] && [ -f "${RESOLVED_CODEX_CONFIG_PATH}" ]; then
  mkdir -p "${CODEX_HOME}"
  cp "${RESOLVED_CODEX_CONFIG_PATH}" "${CODEX_HOME}/config.toml"
fi
cd "$SKILLSBENCH_REPO_ROOT"

exec "$@"
