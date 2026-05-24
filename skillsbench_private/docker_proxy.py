"""Shared proxy configuration for SkillsBench experiment containers."""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping, Sequence

DEFAULT_DOCKER_PROXY_URL = "http://172.21.160.1:29759"
DEFAULT_DOCKER_NO_PROXY = "localhost,127.0.0.1,::1"
DEFAULT_PROXY_SCOPE = "api_only"
VALID_PROXY_SCOPES = ("api_only", "container_global")

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")


def docker_proxy_disabled(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    return env.get("SKILLSBENCH_DISABLE_DOCKER_PROXY") == "1"


def get_proxy_scope(env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    scope = env.get("SKILLSBENCH_PROXY_SCOPE", "").strip() or DEFAULT_PROXY_SCOPE
    if scope not in VALID_PROXY_SCOPES:
        return DEFAULT_PROXY_SCOPE
    return scope


def docker_global_proxy_enabled(env: Mapping[str, str] | None = None) -> bool:
    return not docker_proxy_disabled(env) and get_proxy_scope(env) == "container_global"


def api_proxy_enabled(env: Mapping[str, str] | None = None) -> bool:
    return not docker_proxy_disabled(env) and get_proxy_scope(env) in VALID_PROXY_SCOPES


def get_docker_proxy_url(env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    return env.get("SKILLSBENCH_DOCKER_PROXY_URL", "").strip() or DEFAULT_DOCKER_PROXY_URL


def _merge_no_proxy(extra_no_proxy: Sequence[str] | None = None) -> str:
    values = DEFAULT_DOCKER_NO_PROXY.split(",")
    seen = {item.strip() for item in values if item.strip()}
    for item in extra_no_proxy or ():
        item = item.strip()
        if item and item not in seen:
            values.append(item)
            seen.add(item)
    return ",".join(values)


def get_docker_proxy_env(
    env: Mapping[str, str] | None = None,
    *,
    extra_no_proxy: Sequence[str] | None = None,
) -> dict[str, str]:
    if not docker_global_proxy_enabled(env):
        return {}

    return get_api_proxy_env(env, extra_no_proxy=extra_no_proxy)


def get_api_proxy_env(
    env: Mapping[str, str] | None = None,
    *,
    extra_no_proxy: Sequence[str] | None = None,
) -> dict[str, str]:
    if not api_proxy_enabled(env):
        return {}

    proxy_url = get_docker_proxy_url(env)
    no_proxy = _merge_no_proxy(extra_no_proxy)
    proxy_env = {key: proxy_url for key in PROXY_ENV_KEYS}
    proxy_env.update({key: no_proxy for key in NO_PROXY_ENV_KEYS})
    return proxy_env


def get_docker_run_env_args(env: Mapping[str, str] | None = None) -> list[str]:
    args: list[str] = []
    for key, value in get_docker_proxy_env(env).items():
        args.extend(["-e", f"{key}={value}"])
    return args


def _shell_exports() -> str:
    lines = []
    for key, value in get_docker_proxy_env().items():
        lines.append(f"export {key}={shlex.quote(value)}")
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("json", "shell", "docker-run-args"),
        default="json",
    )
    args = parser.parse_args()

    if args.format == "shell":
        print(_shell_exports())
    elif args.format == "docker-run-args":
        print(" ".join(shlex.quote(part) for part in get_docker_run_env_args()))
    else:
        print(json.dumps(get_docker_proxy_env(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
