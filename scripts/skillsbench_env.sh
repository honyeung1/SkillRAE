#!/usr/bin/env bash

if [ -n "${SKILLSBENCH_ENV_SH_LOADED:-}" ]; then
  return 0
fi

SKILLSBENCH_ENV_SH_LOADED=1

_skillsbench_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_skillsbench_default_artifact_root=""

if [ -d "/mnt/data/${USER:-}" ] && [ -w "/mnt/data/${USER:-}" ]; then
  _skillsbench_default_artifact_root="/mnt/data/${USER:-}/skillsbench"
elif [ -d "/mnt/data" ] && [ -w "/mnt/data" ]; then
  _skillsbench_default_artifact_root="/mnt/data/skillsbench"
else
  _skillsbench_default_artifact_root="${HOME}/skillsbench-artifacts"
fi

: "${SKILLSBENCH_REPO_ROOT:=$(cd "${_skillsbench_env_dir}/.." && pwd)}"
: "${SKILLSBENCH_PYTHONPATH:=${SKILLSBENCH_REPO_ROOT}}"
: "${SKILLSBENCH_ARTIFACT_ROOT:=${_skillsbench_default_artifact_root}}"
: "${SKILLSBENCH_RUNS_ROOT:=${SKILLSBENCH_ARTIFACT_ROOT}/runs}"
: "${SKILLSBENCH_SHARED_JOBS_DIR:=${SKILLSBENCH_ARTIFACT_ROOT}/jobs}"
: "${SKILLSBENCH_CACHE_ROOT:=${SKILLSBENCH_ARTIFACT_ROOT}/.cache}"
: "${SKILLSBENCH_TMP_ROOT:=${SKILLSBENCH_REPO_ROOT}/tmp}"
: "${SKILLSBENCH_RUN_IN_USER_SCOPE:=${SKILLSBENCH_REPO_ROOT}/run_in_user_scope.sh}"
: "${SKILLSBENCH_DEFAULT_RUNNER_ENV_FILE:=${SKILLSBENCH_REPO_ROOT}/deployment/runner/runner.env}"
: "${SKILLSBENCH_DEFAULT_API_ENV_FILE:=${SKILLSBENCH_REPO_ROOT}/deployment/runner/api.env}"
: "${SKILLSBENCH_RUNTIME_NAME:=harbor312}"

if [ -n "${SKILLSBENCH_RUNNER_CONTAINER:-}" ]; then
  : "${SKILLSBENCH_DOCKER_MODE:=socket}"
  : "${SKILLSBENCH_ENABLE_USER_SCOPE_LAUNCH:=0}"
  : "${IO_CGROUP_ENABLE:=0}"
else
  : "${SKILLSBENCH_DOCKER_MODE:=context}"
  : "${SKILLSBENCH_ENABLE_USER_SCOPE_LAUNCH:=1}"
fi

: "${SKILLSBENCH_DOCKER_CONTEXT:=default}"
: "${SKILLSBENCH_DOCKER_SOCKET:=/var/run/docker.sock}"
: "${SKILLSBENCH_PROXY_SCOPE:=api_only}"
: "${SKILLSBENCH_DEFAULT_DOCKER_PROXY_URL:=http://172.21.160.1:29759}"
: "${SKILLSBENCH_DEFAULT_DOCKER_NO_PROXY:=localhost,127.0.0.1,::1}"

export SKILLSBENCH_REPO_ROOT
export SKILLSBENCH_PYTHONPATH
export SKILLSBENCH_ARTIFACT_ROOT
export SKILLSBENCH_RUNS_ROOT
export SKILLSBENCH_SHARED_JOBS_DIR
export SKILLSBENCH_CACHE_ROOT
export SKILLSBENCH_TMP_ROOT
export SKILLSBENCH_RUN_IN_USER_SCOPE
export SKILLSBENCH_DEFAULT_RUNNER_ENV_FILE
export SKILLSBENCH_DEFAULT_API_ENV_FILE
export SKILLSBENCH_RUNTIME_NAME
export SKILLSBENCH_DOCKER_MODE
export SKILLSBENCH_DOCKER_CONTEXT
export SKILLSBENCH_DOCKER_SOCKET
export SKILLSBENCH_PROXY_SCOPE
export SKILLSBENCH_ENABLE_USER_SCOPE_LAUNCH
export SKILLSBENCH_DEFAULT_DOCKER_PROXY_URL
export SKILLSBENCH_DEFAULT_DOCKER_NO_PROXY

skillsbench_proxy_scope() {
  case "${SKILLSBENCH_PROXY_SCOPE:-api_only}" in
    api_only|container_global)
      printf '%s\n' "${SKILLSBENCH_PROXY_SCOPE:-api_only}"
      ;;
    *)
      printf '%s\n' "api_only"
      ;;
  esac
}

skillsbench_docker_proxy_enabled() {
  [ "${SKILLSBENCH_DISABLE_DOCKER_PROXY:-}" != "1" ] && \
    [ "$(skillsbench_proxy_scope)" = "container_global" ]
}

skillsbench_api_proxy_enabled() {
  [ "${SKILLSBENCH_DISABLE_DOCKER_PROXY:-}" != "1" ]
}

skillsbench_docker_proxy_url() {
  if [ -n "${SKILLSBENCH_DOCKER_PROXY_URL:-}" ]; then
    printf '%s\n' "$SKILLSBENCH_DOCKER_PROXY_URL"
  else
    printf '%s\n' "$SKILLSBENCH_DEFAULT_DOCKER_PROXY_URL"
  fi
}

skillsbench_export_docker_proxy_env() {
  local proxy_url

  if ! skillsbench_docker_proxy_enabled; then
    return 0
  fi

  proxy_url="$(skillsbench_docker_proxy_url)"
  export SKILLSBENCH_DOCKER_PROXY_URL="$proxy_url"
  export HTTP_PROXY="$proxy_url"
  export HTTPS_PROXY="$proxy_url"
  export ALL_PROXY="$proxy_url"
  export http_proxy="$proxy_url"
  export https_proxy="$proxy_url"
  export all_proxy="$proxy_url"
  export NO_PROXY="$SKILLSBENCH_DEFAULT_DOCKER_NO_PROXY"
  export no_proxy="$SKILLSBENCH_DEFAULT_DOCKER_NO_PROXY"
}

skillsbench_clear_global_proxy_env_for_api_only() {
  if [ "$(skillsbench_proxy_scope)" != "container_global" ]; then
    unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
    unset NO_PROXY no_proxy
  fi
}

skillsbench_resolve_mount_source() {
  local target_path="$1"

  if [ -z "$target_path" ]; then
    return 1
  fi

  PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
  python3 -m skillsbench_private.runner_path_resolver \
    --target "$target_path" 2>/dev/null
}

skillsbench_resolve_runner_host_artifact_root() {
  local candidate="${SKILLSBENCH_RUNNER_HOST_ARTIFACT_ROOT:-}"

  if [ -n "$candidate" ] && [ -d "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  if [ -z "${SKILLSBENCH_RUNNER_CONTAINER:-}" ]; then
    return 1
  fi

  candidate="$(skillsbench_resolve_mount_source "${SKILLSBENCH_ARTIFACT_ROOT}")" || candidate=""
  if [ -n "$candidate" ] && [ -d "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  return 1
}

skillsbench_translate_runner_artifact_path() {
  local target_path="$1"
  local host_root="${2:-${SKILLSBENCH_RUNNER_HOST_ARTIFACT_ROOT:-}}"

  if [ -z "$target_path" ]; then
    return 1
  fi

  if [ -z "$host_root" ]; then
    host_root="$(skillsbench_resolve_runner_host_artifact_root)" || host_root=""
  fi

  if [ -z "$host_root" ]; then
    printf '%s\n' "$target_path"
    return 0
  fi

  if [ -n "${SKILLSBENCH_RUNNER_CONTAINER:-}" ] && [ -d "$host_root" ]; then
    PYTHONPATH="${SKILLSBENCH_PYTHONPATH}" \
    python3 -m skillsbench_private.runner_path_resolver \
      --target "$target_path" \
      --container-root "${SKILLSBENCH_ARTIFACT_ROOT}" \
      --host-root "$host_root" 2>/dev/null || printf '%s\n' "$target_path"
    return 0
  fi

  printf '%s\n' "$target_path"
}

skillsbench_source_env_file() {
  local env_file="$1"

  if [ -z "$env_file" ] || [ ! -f "$env_file" ]; then
    return 1
  fi

  set -a
  # shellcheck source=/dev/null
  . "$env_file"
  set +a
  return 0
}

skillsbench_load_runner_env() {
  if [ -n "${SKILLSBENCH_ENV_FILE:-}" ]; then
    skillsbench_source_env_file "$SKILLSBENCH_ENV_FILE" || return 1
    export SKILLSBENCH_ENV_FILE_RESOLVED="$SKILLSBENCH_ENV_FILE"
    return 0
  fi

  if [ -f "$SKILLSBENCH_DEFAULT_RUNNER_ENV_FILE" ]; then
    skillsbench_source_env_file "$SKILLSBENCH_DEFAULT_RUNNER_ENV_FILE" || return 1
    export SKILLSBENCH_ENV_FILE_RESOLVED="$SKILLSBENCH_DEFAULT_RUNNER_ENV_FILE"
  fi

  return 0
}

skillsbench_load_api_env() {
  local legacy_script="${1:-}"
  local candidate
  local -a candidates=()

  if [ -n "${SKILLSBENCH_API_ENV_FILE:-}" ]; then
    candidates+=("$SKILLSBENCH_API_ENV_FILE")
  fi
  candidates+=("$SKILLSBENCH_DEFAULT_API_ENV_FILE")
  if [ -n "$legacy_script" ]; then
    candidates+=("$legacy_script")
  fi

  for candidate in "${candidates[@]}"; do
    if [ -f "$candidate" ]; then
      skillsbench_source_env_file "$candidate" || return 1
      export SKILLSBENCH_API_ENV_FILE_RESOLVED="$candidate"
      return 0
    fi
  done

  return 0
}

skillsbench_activate_runtime() {
  local runtime_python
  local runtime_bin_dir
  local conda_profile
  local conda_bin

  if [ -n "${SKILLSBENCH_RUNTIME_PYTHON_BIN:-}" ]; then
    runtime_python="$SKILLSBENCH_RUNTIME_PYTHON_BIN"
    if [ ! -x "$runtime_python" ]; then
      echo "SKILLSBENCH_RUNTIME_PYTHON_BIN is not executable: $runtime_python" >&2
      return 1
    fi
    runtime_bin_dir="$(cd "$(dirname "$runtime_python")" && pwd)"
    export CONDA_PREFIX="$(cd "${runtime_bin_dir}/.." && pwd)"
    export PATH="${runtime_bin_dir}:${PATH}"
    return 0
  fi

  conda_profile="${SKILLSBENCH_CONDA_PROFILE_SH:-}"
  if [ -z "$conda_profile" ]; then
    if [ -n "${CONDA_EXE:-}" ]; then
      conda_profile="$(cd "$(dirname "$CONDA_EXE")/../etc/profile.d" && pwd)/conda.sh"
    elif command -v conda >/dev/null 2>&1; then
      conda_bin="$(command -v conda)"
      conda_profile="$(cd "$(dirname "$conda_bin")/../etc/profile.d" && pwd)/conda.sh"
    fi
  fi

  if [ -z "$conda_profile" ] || [ ! -f "$conda_profile" ]; then
    echo "Unable to resolve conda.sh; set SKILLSBENCH_RUNTIME_PYTHON_BIN or SKILLSBENCH_CONDA_PROFILE_SH" >&2
    return 1
  fi

  # shellcheck source=/dev/null
  . "$conda_profile"
  conda activate "$SKILLSBENCH_RUNTIME_NAME"
}

skillsbench_configure_docker() {
  case "${SKILLSBENCH_DOCKER_MODE}" in
    context)
      unset DOCKER_HOST
      docker context use "${SKILLSBENCH_DOCKER_CONTEXT}" >/dev/null
      ;;
    socket)
      export DOCKER_HOST="${SKILLSBENCH_DOCKER_HOST:-unix://${SKILLSBENCH_DOCKER_SOCKET}}"
      ;;
    inherit)
      ;;
    *)
      echo "Unsupported SKILLSBENCH_DOCKER_MODE: ${SKILLSBENCH_DOCKER_MODE}" >&2
      return 1
      ;;
  esac
}

skillsbench_prepare_runtime() {
  skillsbench_load_runner_env
  if [ -n "${SKILLSBENCH_RUNNER_CONTAINER:-}" ]; then
    local resolved_runner_host_artifact_root
    resolved_runner_host_artifact_root="$(
      skillsbench_resolve_runner_host_artifact_root
    )" || resolved_runner_host_artifact_root=""
    if [ -n "$resolved_runner_host_artifact_root" ]; then
      export SKILLSBENCH_RUNNER_HOST_ARTIFACT_ROOT="$resolved_runner_host_artifact_root"
    fi
  fi
  skillsbench_activate_runtime
  skillsbench_configure_docker
  skillsbench_clear_global_proxy_env_for_api_only
  skillsbench_export_docker_proxy_env

  mkdir -p \
    "${SKILLSBENCH_ARTIFACT_ROOT}" \
    "${SKILLSBENCH_RUNS_ROOT}" \
    "${SKILLSBENCH_SHARED_JOBS_DIR}" \
    "${SKILLSBENCH_CACHE_ROOT}" \
    "${SKILLSBENCH_TMP_ROOT}"

  export UV_CACHE_DIR="${UV_CACHE_DIR:-${SKILLSBENCH_CACHE_ROOT}/uv}"
  mkdir -p "$UV_CACHE_DIR"

  cd "${SKILLSBENCH_REPO_ROOT}"
}
