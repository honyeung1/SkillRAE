"""Repo-local Python startup hook for Harbor agent monkeypatching."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from jinja2 import Environment

from skillsbench_private.codex_config_resolver import resolve_codex_config_path


def _load_optional_codex_instruction_overlay() -> tuple[str | None, str | None]:
    overlay_path = os.environ.get("SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_FILE")
    overlay_text = os.environ.get("SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_TEXT")

    if overlay_path:
        try:
            text = Path(overlay_path).read_text(encoding="utf-8").strip()
        except Exception:
            text = ""
        if text:
            return text, overlay_path

    if overlay_text and overlay_text.strip():
        return overlay_text.strip(), "env:SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_TEXT"

    return None, None


def _compose_instruction_with_overlay(instruction: str, overlay_text: str | None) -> str:
    if overlay_text and overlay_text.strip():
        return f"{overlay_text.strip()}\n\n{instruction.rstrip()}\n"
    return instruction


def _resolve_container_agent_runtime_exports() -> tuple[str, dict[str, str]]:
    runtime_root = os.environ.get("SKILLSBENCH_AGENT_RUNTIME_ROOT", "").strip()
    if not runtime_root:
        return "", {}

    runtime_root = runtime_root.rstrip("/")
    env_map = {
        "CODEX_HOME": f"{runtime_root}/codex-home",
        "TMPDIR": f"{runtime_root}/tmp",
        "XDG_CACHE_HOME": f"{runtime_root}/xdg-cache",
        "XDG_STATE_HOME": f"{runtime_root}/xdg-state",
        "XDG_CONFIG_HOME": f"{runtime_root}/xdg-config",
    }
    export_lines = [f"export {key}={shlex.quote(value)}" for key, value in env_map.items()]
    export_lines.append(
        'mkdir -p "$CODEX_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$XDG_CONFIG_HOME"'
    )
    return "\n".join(export_lines), env_map


def _normalize_node_runtime_dir(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser().resolve()
    if path.name == "node" and path.parent.name == "bin":
        return path.parent.parent
    if path.name == "bin" and (path / "node").exists():
        return path.parent
    return path


def _node_runtime_major(runtime_dir: Path) -> int | None:
    node_bin = runtime_dir / "bin" / "node"
    npm_bin = runtime_dir / "bin" / "npm"
    if not node_bin.exists() or not npm_bin.exists():
        return None
    try:
        result = subprocess.run(
            [str(node_bin), "-p", "process.versions.node"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    version_text = result.stdout.strip()
    try:
        return int(version_text.split(".", 1)[0].lstrip("v"))
    except Exception:
        return None


def _find_host_node_runtime_dir(
    min_major: int = 20,
    tool_env_var: str | None = None,
) -> Path | None:
    env_vars = [name for name in (tool_env_var, "SKILLSBENCH_NODE_RUNTIME_DIR") if name]
    for env_var in env_vars:
        runtime_dir = _normalize_node_runtime_dir(os.environ.get(env_var))
        if runtime_dir is None:
            continue
        major = _node_runtime_major(runtime_dir)
        if major is not None and major >= min_major:
            return runtime_dir

    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_root.exists():
        return None

    candidates = sorted(nvm_root.glob("v*/bin/node"), reverse=True)
    for node_bin in candidates:
        runtime_dir = node_bin.parent.parent
        major = _node_runtime_major(runtime_dir)
        if major is not None and major >= min_major:
            return runtime_dir
    return None


def _resolve_gemini_cli_runtime_paths(
    version: str | None,
) -> tuple[str, Path, Path]:
    repo_root = Path(__file__).resolve().parent
    requested_version = (
        version
        or os.environ.get("SKILLSBENCH_GEMINI_CLI_VERSION")
        or "latest"
    )
    explicit_runtime_dir = os.environ.get("SKILLSBENCH_GEMINI_CLI_RUNTIME_DIR")
    if explicit_runtime_dir:
        runtime_dir = Path(explicit_runtime_dir).expanduser().resolve()
    else:
        runtime_root = repo_root / ".cache" / "gemini-cli"
        slug = (
            requested_version.replace("/", "_")
            .replace("@", "")
            .replace(":", "_")
        )
        runtime_dir = runtime_root / f"runtime-{slug}"
    archive_path = runtime_dir.parent / f"{runtime_dir.name}.tar.gz"
    return requested_version, runtime_dir, archive_path


def _ensure_host_gemini_cli_runtime(version: str | None = None) -> tuple[Path, Path]:
    requested_version, runtime_dir, archive_path = _resolve_gemini_cli_runtime_paths(
        version
    )
    manifest_path = runtime_dir / ".skillsbench-runtime.json"

    if (
        runtime_dir.exists()
        and (runtime_dir / "bin" / "gemini").exists()
        and (runtime_dir / "bin" / "node").exists()
        and manifest_path.exists()
        and archive_path.exists()
    ):
        return runtime_dir, archive_path

    node_runtime_dir = _find_host_node_runtime_dir(
        tool_env_var="SKILLSBENCH_GEMINI_CLI_NODE_RUNTIME_DIR"
    )
    if node_runtime_dir is None:
        raise RuntimeError(
            "No host Node.js runtime >= 20 found. Set "
            "SKILLSBENCH_GEMINI_CLI_NODE_RUNTIME_DIR or SKILLSBENCH_NODE_RUNTIME_DIR, "
            "or run scripts/prepare_gemini_cli_runtime.sh to prepare ~/.nvm first. "
            "Cannot prepare injected Gemini CLI runtime."
        )

    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    build_dir = runtime_dir.parent / f".{runtime_dir.name}.build-{os.getpid()}"
    temp_archive_path = Path(f"{archive_path}.tmp")

    if build_dir.exists():
        shutil.rmtree(build_dir)
    if temp_archive_path.exists():
        temp_archive_path.unlink()

    try:
        shutil.copytree(node_runtime_dir, build_dir, symlinks=True)

        env = os.environ.copy()
        env["PATH"] = f"{build_dir / 'bin'}:{env.get('PATH', '')}"
        env["npm_config_update_notifier"] = "false"
        env["npm_config_fund"] = "false"
        env["npm_config_audit"] = "false"
        env["npm_config_loglevel"] = env.get("npm_config_loglevel", "warn")

        package_ref = "@google/gemini-cli@latest"
        if requested_version != "latest":
            package_ref = f"@google/gemini-cli@{requested_version}"

        install_result = subprocess.run(
            [
                str(build_dir / "bin" / "npm"),
                "install",
                "-g",
                "--prefix",
                str(build_dir),
                package_ref,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        version_result = subprocess.run(
            [str(build_dir / "bin" / "gemini"), "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        manifest = {
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "requested_cli_version": requested_version,
            "node_runtime_source": str(node_runtime_dir),
            "install_strategy": "host-prepared-runtime-injection",
            "gemini_version": version_result.stdout.strip(),
            "npm_install_output_tail": install_result.stdout.strip().splitlines()[-10:],
        }
        manifest_path_build = build_dir / ".skillsbench-runtime.json"
        manifest_path_build.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with tarfile.open(temp_archive_path, "w:gz") as archive:
            for child in sorted(build_dir.iterdir()):
                archive.add(child, arcname=child.name, recursive=True)

        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        build_dir.rename(runtime_dir)
        build_dir = None
        temp_archive_path.replace(archive_path)
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        raise RuntimeError(
            "Failed to prepare host Gemini CLI runtime. "
            f"Command: {' '.join(exc.cmd)}\n{output.strip()}"
        ) from exc
    finally:
        if build_dir and build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        if temp_archive_path.exists():
            temp_archive_path.unlink(missing_ok=True)

    return runtime_dir, archive_path


def _resolve_opencode_runtime_paths(version: str | None) -> tuple[str, Path, Path]:
    repo_root = Path(__file__).resolve().parent
    requested_version = (
        version
        or os.environ.get("SKILLSBENCH_OPENCODE_VERSION")
        or "latest"
    )
    explicit_runtime_dir = os.environ.get("SKILLSBENCH_OPENCODE_RUNTIME_DIR")
    if explicit_runtime_dir:
        runtime_dir = Path(explicit_runtime_dir).expanduser().resolve()
    else:
        runtime_root = repo_root / ".cache" / "opencode"
        slug = (
            requested_version.replace("/", "_")
            .replace("@", "")
            .replace(":", "_")
        )
        runtime_dir = runtime_root / f"runtime-{slug}"
    archive_path = runtime_dir.parent / f"{runtime_dir.name}.tar.gz"
    return requested_version, runtime_dir, archive_path


def _ensure_host_opencode_runtime(version: str | None = None) -> tuple[Path, Path]:
    requested_version, runtime_dir, archive_path = _resolve_opencode_runtime_paths(
        version
    )
    manifest_path = runtime_dir / ".skillsbench-runtime.json"

    if (
        runtime_dir.exists()
        and (runtime_dir / "bin" / "opencode").exists()
        and (runtime_dir / "bin" / "node").exists()
        and manifest_path.exists()
        and archive_path.exists()
    ):
        return runtime_dir, archive_path

    node_runtime_dir = _find_host_node_runtime_dir(
        tool_env_var="SKILLSBENCH_OPENCODE_NODE_RUNTIME_DIR"
    )
    if node_runtime_dir is None:
        raise RuntimeError(
            "No host Node.js runtime >= 20 found. Set "
            "SKILLSBENCH_OPENCODE_NODE_RUNTIME_DIR or SKILLSBENCH_NODE_RUNTIME_DIR, "
            "or prepare ~/.nvm first. Cannot prepare injected OpenCode runtime."
        )

    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    build_dir = runtime_dir.parent / f".{runtime_dir.name}.build-{os.getpid()}"
    temp_archive_path = Path(f"{archive_path}.tmp")

    if build_dir.exists():
        shutil.rmtree(build_dir)
    if temp_archive_path.exists():
        temp_archive_path.unlink()

    try:
        shutil.copytree(node_runtime_dir, build_dir, symlinks=True)

        env = os.environ.copy()
        env["PATH"] = f"{build_dir / 'bin'}:{env.get('PATH', '')}"
        env["npm_config_update_notifier"] = "false"
        env["npm_config_fund"] = "false"
        env["npm_config_audit"] = "false"
        env["npm_config_loglevel"] = env.get("npm_config_loglevel", "warn")

        package_ref = "opencode-ai@latest"
        if requested_version != "latest":
            package_ref = f"opencode-ai@{requested_version}"

        install_result = subprocess.run(
            [
                str(build_dir / "bin" / "npm"),
                "install",
                "-g",
                "--prefix",
                str(build_dir),
                package_ref,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        version_result = subprocess.run(
            [str(build_dir / "bin" / "opencode"), "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        manifest = {
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "requested_cli_version": requested_version,
            "node_runtime_source": str(node_runtime_dir),
            "install_strategy": "host-prepared-runtime-injection",
            "opencode_version": version_result.stdout.strip(),
            "npm_install_output_tail": install_result.stdout.strip().splitlines()[-10:],
        }
        manifest_path_build = build_dir / ".skillsbench-runtime.json"
        manifest_path_build.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with tarfile.open(temp_archive_path, "w:gz") as archive:
            for child in sorted(build_dir.iterdir()):
                archive.add(child, arcname=child.name, recursive=True)

        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        build_dir.rename(runtime_dir)
        build_dir = None
        temp_archive_path.replace(archive_path)
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        raise RuntimeError(
            "Failed to prepare host OpenCode runtime. "
            f"Command: {' '.join(exc.cmd)}\n{output.strip()}"
        ) from exc
    finally:
        if build_dir and build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        if temp_archive_path.exists():
            temp_archive_path.unlink(missing_ok=True)

    return runtime_dir, archive_path


def _host_gemini_bridge_script_path() -> Path:
    repo_root = Path(__file__).resolve().parent
    bridge_path = (
        repo_root / "skillsbench_private" / "harbor_ext" / "gemini_openai_bridge.js"
    )
    if not bridge_path.exists():
        raise FileNotFoundError(f"Gemini bridge script not found: {bridge_path}")
    return bridge_path


def _patch_harbor_claude_code() -> None:
    try:
        from harbor.agents import factory as harbor_factory
        from harbor.agents.installed import claude_code as harbor_claude_code
        from harbor.models.agent.name import AgentName
        from skillsbench_private.harbor_ext.claude_terminus_agent import (
            ClaudeCodeTerminusBridge,
        )
    except Exception:
        return

    harbor_claude_code.ClaudeCode = ClaudeCodeTerminusBridge
    harbor_factory.ClaudeCode = ClaudeCodeTerminusBridge

    patched_agents = []
    for agent in harbor_factory.AgentFactory._AGENTS:
        if getattr(agent, "name", lambda: None)() == AgentName.CLAUDE_CODE.value:
            patched_agents.append(ClaudeCodeTerminusBridge)
        else:
            patched_agents.append(agent)
    harbor_factory.AgentFactory._AGENTS = patched_agents
    harbor_factory.AgentFactory._AGENT_MAP[AgentName.CLAUDE_CODE] = ClaudeCodeTerminusBridge


def _patch_harbor_codex() -> None:
    try:
        from harbor.agents.installed import codex as harbor_codex
        from skillsbench_private.docker_proxy import (
            api_proxy_enabled,
            get_api_proxy_env,
            get_docker_proxy_url,
            get_proxy_scope,
        )
        from skillsbench_private.harbor_ext.codex_runtime_injection import (
            CONTAINER_RUNTIME_BIN,
            CONTAINER_RUNTIME_ROOT,
            get_host_codex_runtime_bundle,
        )
    except Exception:
        return

    repo_root = Path(__file__).resolve().parent
    config_path = resolve_codex_config_path(repo_root)
    if config_path is None or not config_path.exists():
        return

    codex_cls = harbor_codex.Codex
    if getattr(codex_cls, "_skillsbench_private_config_patch", False):
        return

    original_create_run_agent_commands = codex_cls.create_run_agent_commands
    config_text = config_path.read_text(encoding="utf-8")
    escaped_config = shlex.quote(config_text)
    shell_bootstrap = f'export PATH="{CONTAINER_RUNTIME_BIN}:$PATH"; '
    snapshot_helper_path = (
        repo_root / "skillsbench_private" / "harbor_ext" / "output_file_snapshot.py"
    )

    def require_prepared_bundle() -> bool:
        return os.environ.get(
            "SKILLSBENCH_CODEX_RUNTIME_REQUIRE_PREPARED", "0"
        ).lower() not in {"", "0", "false", "no"}

    def patched_create_run_agent_commands(self, instruction: str):
        overlay_text, overlay_source = _load_optional_codex_instruction_overlay()
        effective_instruction = _compose_instruction_with_overlay(instruction, overlay_text)

        commands = original_create_run_agent_commands(self, effective_instruction)
        if not commands:
            return commands

        runtime_exports, runtime_env_map = _resolve_container_agent_runtime_exports()
        api_proxy_env = get_api_proxy_env()
        api_proxy_exports = "".join(
            f"export {key}={shlex.quote(value)}; "
            for key, value in sorted(api_proxy_env.items())
        )
        model = self.model_name.split("/")[-1]
        escaped_instruction = shlex.quote(effective_instruction)
        reasoning_effort = getattr(self, "_reasoning_effort", None)
        reasoning_flag = (
            f"-c model_reasoning_effort={reasoning_effort} " if reasoning_effort else ""
        )
        output_path = f"/logs/agent/{self._OUTPUT_FILENAME}"
        debug_manifest = {
            "config_injected": True,
            "instruction_overlay_enabled": bool(overlay_text),
            "instruction_overlay_source": overlay_source,
            "runtime_injection_expected": True,
            "setup_install_strategy": "host-prepared-runtime-injection",
            "container_runtime_root": CONTAINER_RUNTIME_ROOT,
            "container_agent_runtime_root": runtime_env_map.get("CODEX_HOME"),
            "container_agent_runtime_env": runtime_env_map,
            "container_codex_path": f"{CONTAINER_RUNTIME_BIN}/codex",
            "container_bundled_rg": f"{CONTAINER_RUNTIME_BIN}/rg",
            "proxy_scope": get_proxy_scope(),
            "api_proxy_enabled": api_proxy_enabled(),
            "api_proxy_url": get_docker_proxy_url() if api_proxy_enabled() else "",
        }
        debug_manifest_escaped = shlex.quote(
            json.dumps(debug_manifest, indent=2, sort_keys=True)
        )
        prepare_command = "\n".join(
            [
                "set -euo pipefail",
                runtime_exports,
                'mkdir -p /logs/agent "$CODEX_HOME"',
                commands[0].command.rstrip(),
                f"printf '%s\\n' {escaped_config} > \"$CODEX_HOME/config.toml\"",
                (
                    f"printf '%s\\n' {debug_manifest_escaped} "
                    "> /logs/agent/codex_runtime_injection.json"
                ),
                shell_bootstrap.rstrip("; "),
                '(command -v codex || true) > /logs/agent/codex_binary_path.txt',
                '(codex --version || true) > /logs/agent/codex_version.txt',
                '(rg --version || true) > /logs/agent/codex_rg_version.txt',
            ]
        )
        commands[0].command = prepare_command

        if len(commands) > 1:
            if getattr(commands[1], "env", None) is not None:
                commands[1].env.update(api_proxy_env)
            commands[1].command = "\n".join(
                [
                    "set -euo pipefail",
                    runtime_exports,
                    api_proxy_exports,
                    'cleanup() { rm -rf /tmp/codex-secrets "$CODEX_HOME/auth.json"; };',
                    "trap cleanup EXIT TERM INT",
                    shell_bootstrap.rstrip("; "),
                    (
                        "if command -v python3 >/dev/null 2>&1 && "
                        "[ -f /installed-agent/output_file_snapshot.py ]; then "
                        "python3 /installed-agent/output_file_snapshot.py "
                        "pre /logs/agent/pre_exec_fs_snapshot.json "
                        "/root /root/output /root/workspace /workspace || true; "
                        "fi"
                    ),
                    "set +e",
                    (
                        "codex exec "
                        "--dangerously-bypass-approvals-and-sandbox "
                        "--skip-git-repo-check "
                        f"--model {shlex.quote(model)} "
                        "--json "
                        "--enable unified_exec "
                        f"{reasoning_flag}"
                        "-- "
                        f"{escaped_instruction} "
                        f"> {shlex.quote(output_path)} 2>&1 < /dev/null"
                    ),
                    "codex_rc=$?",
                    "set -e",
                    (
                        "if command -v python3 >/dev/null 2>&1 && "
                        "[ -f /installed-agent/output_file_snapshot.py ]; then "
                        "python3 /installed-agent/output_file_snapshot.py "
                        "post /logs/agent/pre_exec_fs_snapshot.json "
                        "/logs/agent/output_files.json "
                        "/root /root/output /root/workspace /workspace || true; "
                        "fi"
                    ),
                    "exit $codex_rc",
                ]
            )
        return commands

    async def patched_setup(self, environment):
        archive_path, manifest_path, runtime_manifest = get_host_codex_runtime_bundle(
            repo_root=repo_root,
            require_prepared=require_prepared_bundle(),
        )
        resolved_archive_path = archive_path.resolve()

        setup_dir = self.logs_dir / "setup"
        setup_dir.mkdir(parents=True, exist_ok=True)
        debug_manifest = {
            "runtime_injection_expected": True,
            "setup_install_strategy": "host-prepared-runtime-injection",
            "archive_path": str(archive_path),
            "resolved_archive_path": str(resolved_archive_path),
            "manifest_path": str(manifest_path),
            "fingerprint": runtime_manifest.get("fingerprint"),
            "codex_version": runtime_manifest.get("codex_version"),
            "package_name": runtime_manifest.get("package_name"),
            "host_os": runtime_manifest.get("host_os"),
            "host_arch": runtime_manifest.get("host_arch"),
            "output_snapshot_helper": str(snapshot_helper_path),
            "container_runtime_root": runtime_manifest.get(
                "container_runtime_root", CONTAINER_RUNTIME_ROOT
            ),
            "container_codex_path": runtime_manifest.get(
                "container_codex_path", f"{CONTAINER_RUNTIME_BIN}/codex"
            ),
            "container_bundled_rg": runtime_manifest.get(
                "container_bundled_rg", f"{CONTAINER_RUNTIME_BIN}/rg"
            ),
        }
        debug_manifest_text = json.dumps(
            debug_manifest, indent=2, sort_keys=True
        )
        escaped_manifest = shlex.quote(debug_manifest_text)

        install_script = (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "rm -rf /opt/codex-runtime\n"
            "mkdir -p /installed-agent /logs/agent /opt/codex-runtime\n"
            f"printf '%s\\n' {escaped_manifest} > /logs/agent/codex_runtime_injection.json\n"
            "tar -xzf /installed-agent/codex-runtime.tar.gz -C /opt/codex-runtime\n"
            f"export PATH=\"{CONTAINER_RUNTIME_BIN}:$PATH\"\n"
            "(command -v codex || true) > /logs/agent/codex_binary_path.txt\n"
            "(codex --version || true) > /logs/agent/codex_version.txt\n"
            "(rg --version || true) > /logs/agent/codex_rg_version.txt\n"
        )

        script_path = self.logs_dir / "install.sh"
        script_path.write_text(install_script, encoding="utf-8")
        (self.logs_dir / "codex_runtime_injection.json").write_text(
            debug_manifest_text + "\n",
            encoding="utf-8",
        )

        await environment.exec(
            command="mkdir -p /installed-agent /logs/agent /opt/codex-runtime"
        )
        await environment.upload_file(
            source_path=script_path,
            target_path="/installed-agent/install.sh",
        )
        await environment.upload_file(
            source_path=resolved_archive_path,
            target_path="/installed-agent/codex-runtime.tar.gz",
        )
        await environment.upload_file(
            source_path=snapshot_helper_path,
            target_path="/installed-agent/output_file_snapshot.py",
        )

        (setup_dir / "command.txt").write_text(
            "bash /installed-agent/install.sh",
            encoding="utf-8",
        )
        (setup_dir / "strategy.txt").write_text(
            "host-prepared-runtime-injection\n",
            encoding="utf-8",
        )

        result = await environment.exec(
            command="bash /installed-agent/install.sh",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        (setup_dir / "return-code.txt").write_text(
            str(result.return_code),
            encoding="utf-8",
        )
        if result.stdout:
            (setup_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        if result.stderr:
            (setup_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.return_code != 0:
            raise RuntimeError(
                f"Codex injected setup failed with exit code {result.return_code}. "
                f"See logs in {setup_dir}"
            )

    codex_cls.create_run_agent_commands = patched_create_run_agent_commands
    codex_cls.setup = patched_setup
    codex_cls._skillsbench_private_config_patch = True


def _patch_harbor_gemini_cli() -> None:
    try:
        from harbor.agents.installed import gemini_cli as harbor_gemini_cli
    except Exception:
        return

    repo_root = Path(__file__).resolve().parent
    gemini_cls = harbor_gemini_cli.GeminiCli
    if getattr(gemini_cls, "_skillsbench_private_nvm_patch", False):
        return

    original_create_run_agent_commands = gemini_cls.create_run_agent_commands
    original_setup = gemini_cls.setup
    snapshot_helper_path = (
        repo_root / "skillsbench_private" / "harbor_ext" / "output_file_snapshot.py"
    )
    shell_bootstrap = (
        'export GEMINI_CLI_HOME="${GEMINI_CLI_HOME:-/opt/skillsbench-gemini-cli}"; '
        'export GEMINI_CLI_TRUST_WORKSPACE="${GEMINI_CLI_TRUST_WORKSPACE:-true}"; '
        'case "${GEMINI_CLI_TRUST_WORKSPACE}" in '
        '1|true|TRUE|True|yes|YES|Yes|on|ON|On) '
        '  GEMINI_CLI_TRUST_WORKSPACE=true ;; '
        'skip|SKIP|skip-trust|SKIP-TRUST) '
        '  GEMINI_CLI_TRUST_WORKSPACE=skip ;; '
        '*) '
        '  : ;; '
        'esac; '
        'if [ "$GEMINI_CLI_TRUST_WORKSPACE" = "skip" ]; then '
        '  GEMINI_CLI_TRUST_WORKSPACE_ARGS=" --skip-trust"; '
        'else '
        '  GEMINI_CLI_TRUST_WORKSPACE_ARGS=""; '
        'fi; '
        'export PATH="$GEMINI_CLI_HOME/bin:$PATH"; '
        'mkdir -p "$HOME/.gemini"; '
        'if ! command -v gemini >/dev/null 2>&1; then '
        'gemini_bin="$(find "$GEMINI_CLI_HOME" -path "*/bin/gemini" 2>/dev/null | sort | tail -n 1 || true)"; '
        'if [ -n "${gemini_bin:-}" ]; then export PATH="$(dirname "$gemini_bin"):$PATH"; fi; '
        "fi; "
    )
    compat_bridge_bootstrap = (
        'if [ -n "${SKILLSBENCH_GEMINI_UPSTREAM_BASE_URL:-}" ]; then '
        'export SKILLSBENCH_GEMINI_BRIDGE_PORT="${SKILLSBENCH_GEMINI_BRIDGE_PORT:-8765}"; '
        'export GOOGLE_GEMINI_BASE_URL="http://127.0.0.1:${SKILLSBENCH_GEMINI_BRIDGE_PORT}"; '
        'export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"; '
        'export no_proxy="127.0.0.1,localhost${no_proxy:+,$no_proxy}"; '
        'bridge_script="$GEMINI_CLI_HOME/bridge/gemini_openai_bridge.js"; '
        'bridge_log="/logs/agent/gemini-bridge.log"; '
        'if [ -f "$bridge_script" ]; then '
        'node "$bridge_script" >"$bridge_log" 2>&1 & '
        'bridge_pid=$!; '
        'printf "%s\\n" "$bridge_pid" > /logs/agent/gemini-bridge.pid; '
        'for _i in $(seq 1 50); do '
        '(echo >/dev/tcp/127.0.0.1/"$SKILLSBENCH_GEMINI_BRIDGE_PORT") >/dev/null 2>&1 && break; '
        'sleep 0.1; '
        'done; '
        "fi; "
        "fi; "
    )
    compat_bridge_teardown = (
        'if [ -f /logs/agent/gemini-bridge.pid ]; then '
        'kill "$(cat /logs/agent/gemini-bridge.pid)" >/dev/null 2>&1 || true; '
        "fi; "
    )

    async def patched_setup(self, environment):
        try:
            runtime_dir, archive_path = _ensure_host_gemini_cli_runtime(self.version())
            resolved_archive_path = archive_path.resolve()
            bridge_script_path = _host_gemini_bridge_script_path()
            injection_error = None
        except Exception as exc:
            runtime_dir = None
            archive_path = None
            resolved_archive_path = None
            bridge_script_path = None
            injection_error = str(exc)

        if archive_path is None:
            await original_setup(self, environment)
            debug_manifest = {
                "runtime_injection_expected": False,
                "setup_install_strategy": "upstream-cold-install-fallback",
                "fallback_reason": injection_error,
            }
            (self.logs_dir / "gemini_cli_injection.json").write_text(
                json.dumps(debug_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return

        setup_dir = self.logs_dir / "setup"
        setup_dir.mkdir(parents=True, exist_ok=True)

        debug_manifest = {
            "runtime_injection_expected": True,
            "setup_install_strategy": "host-prepared-runtime-injection",
            "runtime_dir": str(runtime_dir),
            "archive_path": str(archive_path),
            "resolved_archive_path": str(resolved_archive_path),
            "bridge_script_path": str(bridge_script_path),
            "output_snapshot_helper": str(snapshot_helper_path),
        }
        debug_manifest_text = json.dumps(debug_manifest, indent=2, sort_keys=True)
        escaped_manifest = shlex.quote(debug_manifest_text)

        install_script = (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "rm -rf \"$HOME/.gemini\"\n"
            "mkdir -p /installed-agent /logs/agent /opt/skillsbench-gemini-cli /opt/skillsbench-gemini-cli/bridge \"$HOME/.gemini\"\n"
            f"printf '%s\\n' {escaped_manifest} > /logs/agent/gemini_cli_injection.json\n"
            "tar -xzf /installed-agent/gemini-cli-runtime.tar.gz -C /opt/skillsbench-gemini-cli\n"
            "cp /installed-agent/gemini_openai_bridge.js /opt/skillsbench-gemini-cli/bridge/gemini_openai_bridge.js\n"
            "cat > \"$HOME/.gemini/settings.json\" <<'EOF'\n"
            "{\n"
            '  "experimental": {\n'
            '    "skills": true\n'
            "  }\n"
            "}\n"
            "EOF\n"
            "printf '{}\\n' > \"$HOME/.gemini/projects.json\"\n"
            "export PATH=\"/opt/skillsbench-gemini-cli/bin:$PATH\"\n"
            "(command -v gemini || true) > /logs/agent/gemini_binary_path.txt\n"
            "(gemini --version || true) > /logs/agent/gemini_version.txt\n"
        )

        script_path = self.logs_dir / "install.sh"
        script_path.write_text(install_script, encoding="utf-8")
        (self.logs_dir / "gemini_cli_injection.json").write_text(
            debug_manifest_text + "\n",
            encoding="utf-8",
        )

        await environment.exec(command="mkdir -p /installed-agent /logs/agent /opt/skillsbench-gemini-cli")
        await environment.upload_file(
            source_path=script_path,
            target_path="/installed-agent/install.sh",
        )
        await environment.upload_file(
            source_path=resolved_archive_path,
            target_path="/installed-agent/gemini-cli-runtime.tar.gz",
        )
        await environment.upload_file(
            source_path=bridge_script_path,
            target_path="/installed-agent/gemini_openai_bridge.js",
        )
        await environment.upload_file(
            source_path=snapshot_helper_path,
            target_path="/installed-agent/output_file_snapshot.py",
        )

        (setup_dir / "command.txt").write_text(
            "bash /installed-agent/install.sh",
            encoding="utf-8",
        )
        (setup_dir / "strategy.txt").write_text(
            "host-prepared-runtime-injection\n",
            encoding="utf-8",
        )

        result = await environment.exec(
            command="bash /installed-agent/install.sh",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        (setup_dir / "return-code.txt").write_text(
            str(result.return_code),
            encoding="utf-8",
        )
        if result.stdout:
            (setup_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        if result.stderr:
            (setup_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.return_code != 0:
            raise RuntimeError(
                f"Gemini CLI injected setup failed with exit code {result.return_code}. "
                f"See logs in {setup_dir}"
            )

    def patched_create_run_agent_commands(self, instruction: str):
        commands = original_create_run_agent_commands(self, instruction)
        if commands is None:
            return []
        snapshot_enabled = os.environ.get(
            "SKILLSBENCH_OUTPUT_FILE_SNAPSHOT_ENABLE", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}

        for exec_input in commands:
            env = dict(exec_input.env) if exec_input.env else {}
            passthrough_vars = [
                "GEMINI_API_KEY",
                "GOOGLE_GEMINI_BASE_URL",
                "GEMINI_API_KEY_AUTH_MECHANISM",
                "GEMINI_MODEL",
                "GOOGLE_GENAI_API_VERSION",
                "SKILLSBENCH_GEMINI_UPSTREAM_API_KEY",
                "SKILLSBENCH_GEMINI_UPSTREAM_BASE_URL",
                "SKILLSBENCH_GEMINI_BRIDGE_PORT",
                "SKILLSBENCH_GEMINI_BRIDGE_DEBUG",
                "SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS",
                "SKILLSBENCH_GEMINI_BRIDGE_DEFAULT_MODEL",
                "SKILLSBENCH_GEMINI_BRIDGE_PREVIEW_MODEL",
                "SKILLSBENCH_GEMINI_BRIDGE_FLASH_MODEL",
                "SKILLSBENCH_GEMINI_BRIDGE_WEB_SEARCH_MODEL",
                "SKILLSBENCH_GEMINI_BRIDGE_MODEL_ALIASES",
                "SKILLSBENCH_GEMINI_DIRECT_DISABLE_GOOGLE_API_KEY",
                "GEMINI_CLI_TRUST_WORKSPACE",
                "GEMINI_CLI_SKIP_TRUST",
            ]
            for var in passthrough_vars:
                if var in os.environ and var not in env:
                    env[var] = os.environ[var]
            if "GEMINI_API_KEY" not in env and env.get("GOOGLE_API_KEY"):
                env["GEMINI_API_KEY"] = env["GOOGLE_API_KEY"]
            if (
                env.get("SKILLSBENCH_GEMINI_DIRECT_DISABLE_GOOGLE_API_KEY") == "1"
                and env.get("GEMINI_API_KEY")
            ):
                env.pop("GOOGLE_API_KEY", None)
            exec_input.env = env
            if "gemini" not in exec_input.command:
                continue
            gemini_skip_trust = (
                env.get("GEMINI_CLI_SKIP_TRUST", "").strip().lower() in {"1", "true", "yes", "on"}
            )
            if gemini_skip_trust:
                env["GEMINI_CLI_TRUST_WORKSPACE"] = "skip"
                exec_input.env = env
            command_prefix = "set -uo pipefail; " + shell_bootstrap + compat_bridge_bootstrap
            if snapshot_enabled:
                command_prefix += (
                    "if command -v python3 >/dev/null 2>&1 && "
                    "[ -f /installed-agent/output_file_snapshot.py ]; then "
                    "python3 /installed-agent/output_file_snapshot.py "
                    "pre /logs/agent/pre_exec_fs_snapshot.json "
                    "/root /root/output /root/workspace /workspace || true; "
                    "fi; "
                )
            command_suffix = ""
            if snapshot_enabled:
                command_suffix = (
                    "if command -v python3 >/dev/null 2>&1 && "
                    "[ -f /installed-agent/output_file_snapshot.py ]; then "
                    "python3 /installed-agent/output_file_snapshot.py "
                    "post /logs/agent/pre_exec_fs_snapshot.json "
                    "/logs/agent/output_files.json "
                    "/root /root/output /root/workspace /workspace || true; "
                    "fi; "
                )
            exec_input.command = (
                command_prefix
                + "set +e; "
                + exec_input.command.replace(
                    " gemini --yolo",
                    " gemini${GEMINI_CLI_TRUST_WORKSPACE_ARGS} --yolo",
                )
                + "; rc=$?; set -e; "
                + command_suffix
                + compat_bridge_teardown
                + 'exit "$rc"'
            )

        return commands

    gemini_cls.setup = patched_setup
    gemini_cls.create_run_agent_commands = patched_create_run_agent_commands
    gemini_cls._skillsbench_private_nvm_patch = True


def _patch_harbor_opencode() -> None:
    try:
        from harbor.agents.installed.base import ExecInput
        from harbor.agents.installed import opencode as harbor_opencode
    except Exception:
        return

    opencode_cls = harbor_opencode.OpenCode
    if getattr(opencode_cls, "_skillsbench_private_runtime_patch", False):
        return

    original_setup = opencode_cls.setup
    original_create_run_agent_commands = opencode_cls.create_run_agent_commands
    shell_bootstrap = (
        'export OPENCODE_HOME="${OPENCODE_HOME:-/opt/skillsbench-opencode}"; '
        'export PATH="$OPENCODE_HOME/bin:$PATH"; '
        'mkdir -p "$HOME/.config/opencode" "$HOME/.local/share/opencode"; '
        'if ! command -v opencode >/dev/null 2>&1; then '
        'opencode_bin="$(find "$OPENCODE_HOME" -path "*/bin/opencode" 2>/dev/null | sort | tail -n 1 || true)"; '
        'if [ -n "${opencode_bin:-}" ]; then export PATH="$(dirname "$opencode_bin"):$PATH"; fi; '
        "fi; "
    )

    async def patched_setup(self, environment):
        try:
            runtime_dir, archive_path = _ensure_host_opencode_runtime(self.version())
            resolved_archive_path = archive_path.resolve()
            injection_error = None
        except Exception as exc:
            runtime_dir = None
            archive_path = None
            resolved_archive_path = None
            injection_error = str(exc)

        if archive_path is None:
            await original_setup(self, environment)
            debug_manifest = {
                "runtime_injection_expected": False,
                "setup_install_strategy": "upstream-cold-install-fallback",
                "fallback_reason": injection_error,
            }
            (self.logs_dir / "opencode_runtime_injection.json").write_text(
                json.dumps(debug_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return

        setup_dir = self.logs_dir / "setup"
        setup_dir.mkdir(parents=True, exist_ok=True)

        provider_id = "openai"
        model_id = "gpt-5.2"
        if self.model_name and "/" in self.model_name:
            provider_id, model_id = self.model_name.split("/", 1)

        context_limit = int(
            os.environ.get("SKILLSBENCH_OPENCODE_MODEL_CONTEXT_LIMIT", "131072")
        )
        output_limit = int(
            os.environ.get("SKILLSBENCH_OPENCODE_MODEL_OUTPUT_LIMIT", "16384")
        )
        provider_name = os.environ.get("SKILLSBENCH_OPENCODE_PROVIDER_NAME") or provider_id.upper()
        small_model = (
            os.environ.get("SKILLSBENCH_OPENCODE_SMALL_MODEL")
            or self.model_name
            or f"{provider_id}/{model_id}"
        )
        use_compat_provider = os.environ.get(
            "SKILLSBENCH_OPENCODE_COMPAT_PROVIDER", "0"
        ).lower() not in {"", "0", "false", "no"}

        opencode_config = None
        if use_compat_provider:
            opencode_config = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    provider_id: {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": provider_name,
                        "options": {
                            "baseURL": os.environ.get("OPENAI_BASE_URL", ""),
                            "apiKey": "{env:OPENAI_API_KEY}",
                            "timeout": 600000,
                            "chunkTimeout": 30000,
                        },
                        "models": {
                            model_id: {
                                "name": model_id,
                                "limit": {
                                    "context": context_limit,
                                    "output": output_limit,
                                },
                            }
                        },
                    }
                },
                "small_model": small_model,
            }

        debug_manifest = {
            "runtime_injection_expected": True,
            "setup_install_strategy": "host-prepared-runtime-injection",
            "runtime_dir": str(runtime_dir),
            "archive_path": str(archive_path),
            "resolved_archive_path": str(resolved_archive_path),
            "provider_id": provider_id,
            "model_id": model_id,
            "compat_provider_enabled": use_compat_provider,
            "config_path": "/root/.config/opencode/opencode.json",
        }
        debug_manifest_text = json.dumps(debug_manifest, indent=2, sort_keys=True)
        escaped_manifest = shlex.quote(debug_manifest_text)
        config_text = json.dumps(opencode_config, indent=2, sort_keys=True)
        escaped_config = shlex.quote(config_text)

        install_script = (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "rm -rf \"$HOME/.config/opencode\"\n"
            "mkdir -p /installed-agent /logs/agent /opt/skillsbench-opencode \"$HOME/.config/opencode\" \"$HOME/.local/share/opencode\"\n"
            f"printf '%s\\n' {escaped_manifest} > /logs/agent/opencode_runtime_injection.json\n"
            "tar -xzf /installed-agent/opencode-runtime.tar.gz -C /opt/skillsbench-opencode\n"
            f"printf '%s\\n' {escaped_config} > \"$HOME/.config/opencode/opencode.json\"\n"
            "export PATH=\"/opt/skillsbench-opencode/bin:$PATH\"\n"
            "(command -v opencode || true) > /logs/agent/opencode_binary_path.txt\n"
            "(opencode --version > /logs/agent/opencode_version.txt 2>&1 || true)\n"
        )

        script_path = self.logs_dir / "install.sh"
        script_path.write_text(install_script, encoding="utf-8")
        (self.logs_dir / "opencode_runtime_injection.json").write_text(
            debug_manifest_text + "\n",
            encoding="utf-8",
        )

        await environment.exec(
            command="mkdir -p /installed-agent /logs/agent /opt/skillsbench-opencode"
        )
        await environment.upload_file(
            source_path=script_path,
            target_path="/installed-agent/install.sh",
        )
        await environment.upload_file(
            source_path=resolved_archive_path,
            target_path="/installed-agent/opencode-runtime.tar.gz",
        )

        (setup_dir / "command.txt").write_text(
            "bash /installed-agent/install.sh",
            encoding="utf-8",
        )
        (setup_dir / "strategy.txt").write_text(
            "host-prepared-runtime-injection\n",
            encoding="utf-8",
        )

        result = await environment.exec(
            command="bash /installed-agent/install.sh",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        (setup_dir / "return-code.txt").write_text(
            str(result.return_code),
            encoding="utf-8",
        )
        if result.stdout:
            (setup_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        if result.stderr:
            (setup_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.return_code != 0:
            raise RuntimeError(
                f"OpenCode injected setup failed with exit code {result.return_code}. "
                f"See logs in {setup_dir}"
            )

    def patched_create_run_agent_commands(self, instruction: str):
        compat_provider_enabled = os.environ.get(
            "SKILLSBENCH_OPENCODE_COMPAT_PROVIDER", "0"
        ).lower() not in {"", "0", "false", "no"}
        known_providers = {
            "amazon-bedrock",
            "anthropic",
            "azure",
            "deepseek",
            "github-copilot",
            "google",
            "groq",
            "huggingface",
            "llama",
            "mistral",
            "openai",
            "xai",
        }

        provider = None
        if self.model_name and "/" in self.model_name:
            provider, _ = self.model_name.split("/", 1)

        if compat_provider_enabled and provider and provider not in known_providers:
            escaped_instruction = shlex.quote(instruction)
            escaped_model_name = shlex.quote(self.model_name)
            commands = [
                ExecInput(
                    command=(
                        f"opencode --model {escaped_model_name} run --format=json "
                        f"{escaped_instruction} 2>&1 | tee /logs/agent/opencode.txt"
                    ),
                    env={},
                )
            ]
        else:
            commands = original_create_run_agent_commands(self, instruction)
        auto_approve = os.environ.get("SKILLSBENCH_OPENCODE_AUTO_APPROVE", "1")

        for exec_input in commands:
            env = dict(exec_input.env) if exec_input.env else {}
            passthrough_vars = [
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
                "OPENAI_ORG_ID",
                "OPENAI_ORGANIZATION",
                "SKILLSBENCH_OPENCODE_COMPAT_PROVIDER",
                "SKILLSBENCH_OPENCODE_PROVIDER_NAME",
                "SKILLSBENCH_OPENCODE_SMALL_MODEL",
                "SKILLSBENCH_OPENCODE_MODEL_CONTEXT_LIMIT",
                "SKILLSBENCH_OPENCODE_MODEL_OUTPUT_LIMIT",
            ]
            for var in passthrough_vars:
                if var in os.environ and var not in env:
                    env[var] = os.environ[var]
            env.setdefault("OPENCODE_FAKE_VCS", "git")
            exec_input.env = env

            if "opencode" not in exec_input.command:
                continue

            command_body = exec_input.command
            if auto_approve.lower() not in {"", "0", "false", "no"}:
                command_body = command_body.replace(
                    " run --format=json ",
                    " run --dangerously-skip-permissions --format=json ",
                    1,
                )
            exec_input.command = (
                "set -uo pipefail; "
                + shell_bootstrap
                + "set +e; "
                + command_body
                + '; rc=$?; set -e; exit "$rc"'
            )

        return commands

    opencode_cls.setup = patched_setup
    opencode_cls.create_run_agent_commands = patched_create_run_agent_commands
    opencode_cls._skillsbench_private_runtime_patch = True


def _patch_harbor_qwen_code() -> None:
    try:
        from harbor.agents.installed import qwen_code as harbor_qwen_code
    except Exception:
        return

    qwen_cls = harbor_qwen_code.QwenCode
    if getattr(qwen_cls, "_skillsbench_private_nvm_patch", False):
        return

    original_create_run_agent_commands = qwen_cls.create_run_agent_commands
    shell_bootstrap = (
        'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; '
        'mkdir -p "$HOME/.qwen"; '
        'if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true; fi; '
        'nvm use 22 >/dev/null 2>&1 || nvm use default >/dev/null 2>&1 || true; '
        'if ! command -v qwen >/dev/null 2>&1; then '
        'qwen_bin="$(find "$NVM_DIR/versions/node" -path "*/bin/qwen" 2>/dev/null | sort | tail -n 1 || true)"; '
        'if [ -n "${qwen_bin:-}" ]; then export PATH="$(dirname "$qwen_bin"):$PATH"; fi; '
        "fi; "
    )

    def patched_create_run_agent_commands(self, instruction: str):
        commands = original_create_run_agent_commands(self, instruction)
        for exec_input in commands:
            if "qwen" not in exec_input.command:
                continue
            exec_input.command = (
                "set -euo pipefail; "
                + shell_bootstrap
                + exec_input.command
            )
        return commands

    qwen_cls.create_run_agent_commands = patched_create_run_agent_commands
    qwen_cls._skillsbench_private_nvm_patch = True


def _patch_harbor_cline() -> None:
    try:
        from harbor.agents.installed.base import ExecInput
        from harbor.agents.installed.cline import cline as harbor_cline
    except Exception:
        return

    cline_cls = harbor_cline.ClineCli
    if getattr(cline_cls, "_skillsbench_private_run_patch", False):
        return

    def patched_create_run_agent_commands(self, instruction: str):
        escaped_instruction = shlex.quote(instruction)

        if not self.model_name or ":" not in self.model_name:
            raise ValueError(
                f"model_name must be in format 'provider:model-id', got: '{self.model_name}'"
            )

        provider, model = self.model_name.split(":", 1)
        api_key = (
            os.environ.get("API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not api_key:
            raise ValueError("API_KEY environment variable is required")

        env = {
            "PROVIDER": provider,
            "API_KEY": api_key,
            "MODELID": model,
        }

        base_url = ""
        if provider == "openai":
            base_url = os.environ.get("BASE_URL") or os.environ.get("OPENAI_BASE_URL", "")
            if not base_url:
                raise ValueError(
                    "BASE_URL environment variable is required for openai provider"
                )
            env["BASE_URL"] = base_url

        setup_config_cmd = ExecInput(
            command=(
                "mkdir -p ~/.cline/data && "
                "cat > ~/.cline/data/globalState.json <<EOF\n"
                '{"welcomeViewCompleted": true, "isNewUser": false}\n'
                "EOF"
            ),
            env=env,
        )

        nvm_setup_command = (
            'export NVM_DIR="$HOME/.nvm"; '
            '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
            "nvm use 22 >/dev/null 2>&1 || nvm use default >/dev/null 2>&1 || true"
        )

        if provider == "openai" and base_url:
            auth_command = (
                'cline auth --provider openai --apikey "$API_KEY" --modelid "$MODELID" --baseurl "$BASE_URL" '
                '|| cline auth -p openai -k "$API_KEY" -m "$MODELID" -b "$BASE_URL"'
            )
        else:
            auth_command = (
                f'cline auth --provider {provider} --apikey "$API_KEY" --modelid "$MODELID" '
                f'|| cline auth -p {provider} -k "$API_KEY" -m "$MODELID"'
            )

        run_cline_cmd = ExecInput(
            command=(
                f"{nvm_setup_command}; "
                "set -o pipefail; "
                f"{auth_command} && "
                f"cline -y --verbose -- {escaped_instruction} 2>&1 | tee /logs/agent/cline.txt; "
                "EXIT_CODE=$?; "
                'if command -v timeout >/dev/null 2>&1; then timeout 5s cline instance kill -a >/dev/null 2>&1 || true; fi; '
                "exit $EXIT_CODE"
            ),
            env=env,
        )

        return [setup_config_cmd, run_cline_cmd]

    cline_cls.create_run_agent_commands = patched_create_run_agent_commands
    cline_cls._skillsbench_private_run_patch = True


def _patch_harbor_docker_proxy_overlay() -> None:
    try:
        from harbor.environments.docker import docker as harbor_docker
        from skillsbench_private.docker_proxy import docker_global_proxy_enabled
    except Exception:
        return

    docker_env_cls = harbor_docker.DockerEnvironment
    if getattr(docker_env_cls, "_skillsbench_private_docker_proxy_overlay_patch", False):
        return

    original_paths_property = docker_env_cls._docker_compose_paths
    overlay_path = (
        Path(__file__).resolve().parent
        / "skillsbench_private"
        / "harbor_ext"
        / "docker_proxy_overlay.compose.yaml"
    )

    def patched_docker_compose_paths(self):
        paths = list(original_paths_property.fget(self))
        if docker_global_proxy_enabled() and overlay_path.exists():
            paths.append(overlay_path)
        return paths

    docker_env_cls._docker_compose_paths = property(patched_docker_compose_paths)
    docker_env_cls._skillsbench_private_docker_proxy_overlay_patch = True


def _sanitize_docker_image_name_suffix(value: str) -> str:
    value = value.lower().replace("/", "-").replace(":", "-")
    safe = "".join(
        char if char.isalnum() or char in "._-" else "-"
        for char in value
    ).strip("._-")
    return safe or "trial"


def _patch_harbor_docker_unique_image_names() -> None:
    try:
        from harbor.environments.docker import docker as harbor_docker
    except Exception:
        return

    docker_env_cls = harbor_docker.DockerEnvironment
    if getattr(docker_env_cls, "_skillsbench_private_unique_image_names_patch", False):
        return

    original_init = docker_env_cls.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if os.environ.get("SKILLSBENCH_UNIQUE_DOCKER_IMAGE_NAMES", "1") != "1":
            return

        session_id = getattr(self, "session_id", "") or getattr(
            self, "environment_name", "trial"
        )
        self._env_vars.main_image_name = (
            f"hb__{_sanitize_docker_image_name_suffix(str(session_id))}"
        )

    docker_env_cls.__init__ = patched_init
    docker_env_cls._skillsbench_private_unique_image_names_patch = True


def _patch_harbor_docker_exec() -> None:
    try:
        from harbor.environments.docker import docker as harbor_docker
    except Exception:
        return

    docker_env_cls = harbor_docker.DockerEnvironment
    if getattr(docker_env_cls, "_skillsbench_private_non_tty_exec_patch", False):
        return

    async def patched_exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
    ):
        exec_command = ["exec", "-T"]

        if cwd:
            exec_command.extend(["-w", cwd])

        if env:
            for key, value in env.items():
                exec_command.extend(["-e", f"{key}={shlex.quote(value)}"])

        exec_command.append("main")
        exec_command.extend(["bash", "-lc", command])

        return await self._run_docker_compose_command(
            exec_command, check=False, timeout_sec=timeout_sec
        )

    docker_env_cls.exec = patched_exec
    docker_env_cls._skillsbench_private_non_tty_exec_patch = True


def _patch_harbor_trial_timeouts() -> None:
    try:
        from harbor.trial import trial as harbor_trial
    except Exception:
        return

    trial_cls = harbor_trial.Trial
    if getattr(trial_cls, "_skillsbench_private_env_timeout_patch", False):
        return

    original_init = trial_cls.__init__

    def patched_init(self, config):
        original_init(self, config)
        self._environment_build_timeout_sec = max(
            self._environment_build_timeout_sec,
            1200.0,
        )

    trial_cls.__init__ = patched_init
    trial_cls._skillsbench_private_env_timeout_patch = True


def _patch_harbor_trial_contract_linter_repair() -> None:
    if os.environ.get("SKILLSBENCH_CONTRACT_LINTER_ENABLE", "0") != "1":
        return
    try:
        from harbor.trial import trial as harbor_trial
        from experiments.retrieval_tasks_backend.contract_linter_repair import (
            run_contract_linter_repair,
        )
    except Exception:
        return

    trial_cls = harbor_trial.Trial
    if getattr(trial_cls, "_skillsbench_private_contract_linter_patch", False):
        return

    original_run_verification = trial_cls._run_verification

    async def patched_run_verification(self, *args, **kwargs):
        try:
            await run_contract_linter_repair(self)
        except Exception as exc:
            try:
                manifest_path = self._trial_paths.agent_dir / "contract_linter_manifest.json"
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(
                    json.dumps(
                        {
                            "contract_linter_enabled": True,
                            "contract_linter_checked": False,
                            "contract_linter_passed": None,
                            "contract_linter_findings": [
                                {
                                    "type": "host_hook_exception",
                                    "error": str(exc)[:500],
                                }
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        return await original_run_verification(self, *args, **kwargs)

    trial_cls._run_verification = patched_run_verification
    trial_cls._skillsbench_private_contract_linter_patch = True


_patch_harbor_claude_code()
_patch_harbor_codex()
_patch_harbor_gemini_cli()
_patch_harbor_opencode()
_patch_harbor_qwen_code()
_patch_harbor_cline()
_patch_harbor_docker_proxy_overlay()
_patch_harbor_docker_unique_image_names()
_patch_harbor_docker_exec()
_patch_harbor_trial_timeouts()
_patch_harbor_trial_contract_linter_repair()
