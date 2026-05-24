from __future__ import annotations

import argparse
import os
from pathlib import Path


_EXPLICIT_ENV_KEYS = ("SKILLSBENCH_CODEX_CONFIG_PATH", "CODEX_CONFIG_PATH")


def _normalize_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def iter_default_codex_config_candidates(
    repo_root: str | os.PathLike[str],
) -> tuple[Path, ...]:
    repo_root_path = _normalize_path(repo_root)
    codex_dir = repo_root_path / ".codex"
    return (
        codex_dir / "config.toml",
        codex_dir / "config.thirdparty.toml",
    )


def resolve_codex_config_path(
    repo_root: str | os.PathLike[str],
    *,
    env: dict[str, str] | None = None,
) -> Path | None:
    effective_env = os.environ if env is None else env

    for env_key in _EXPLICIT_ENV_KEYS:
        configured = str(effective_env.get(env_key) or "").strip()
        if configured:
            return _normalize_path(configured)

    for candidate in iter_default_codex_config_candidates(repo_root):
        if candidate.exists():
            return candidate

    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve the Codex config path used by SkillsBench runner flows."
    )
    parser.add_argument("--repo-root", required=True, help="Repository root to inspect")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    resolved = resolve_codex_config_path(args.repo_root)
    if resolved is None:
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
