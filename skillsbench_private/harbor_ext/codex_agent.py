from __future__ import annotations

import json
import os
import shlex
import tomllib
from pathlib import Path

from harbor.agents.installed.base import ExecInput
from harbor.agents.installed.codex import Codex
from harbor.models.trial.paths import EnvironmentPaths


class PrivateCodexAgent(Codex):
    """Private Harbor bridge for Codex using repo-local Responses config."""

    _DEBUG_FILENAME = "codex_runtime_debug.json"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._repo_root = Path(__file__).resolve().parents[2]
        self._config_path = self._repo_root / ".codex" / "config.toml"

        if not self._config_path.exists():
            raise FileNotFoundError(f"Codex config not found at {self._config_path}")

        self._config_text = self._config_path.read_text(encoding="utf-8")
        self._config_data = tomllib.loads(self._config_text)

    @staticmethod
    def name() -> str:
        return "codex"

    def _debug_payload(self) -> dict[str, object]:
        provider_name = self._config_data.get("model_provider")
        providers = self._config_data.get("model_providers", {})
        provider_cfg = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
        requested_seed = os.environ.get("EXPERIMENT_SEED") or os.environ.get("SEED")

        return {
            "adapter_layer": "skillsbench_private.harbor_ext.codex_agent",
            "model_name": self.model_name,
            "selected_model": self._config_data.get("model"),
            "model_provider": provider_name,
            "provider_name": provider_cfg.get("name"),
            "provider_base_url": provider_cfg.get("base_url"),
            "provider_wire_api": provider_cfg.get("wire_api"),
            "provider_env_key": provider_cfg.get("env_key"),
            "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
            "host_config_path": str(self._config_path),
            "container_config_path": "/root/.codex/config.toml",
            "codex_exec_command": "codex exec",
            "requested_seed": requested_seed,
            "seed_mode": "best_effort" if requested_seed else None,
            "model_seed_supported": False if requested_seed else None,
        }

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        if not self.model_name:
            raise ValueError("Model name is required")

        model = self.model_name.split("/")[-1]
        escaped_instruction = shlex.quote(instruction)
        config_escaped = shlex.quote(self._config_text)
        debug_json = shlex.quote(json.dumps(self._debug_payload(), indent=2, sort_keys=True))
        output_path = shlex.quote(str(EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME))
        debug_path = shlex.quote(str(EnvironmentPaths.agent_dir / self._DEBUG_FILENAME))
        binary_path_file = shlex.quote(str(EnvironmentPaths.agent_dir / "codex_binary_path.txt"))
        version_file = shlex.quote(str(EnvironmentPaths.agent_dir / "codex_version.txt"))

        env = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
            "CODEX_HOME": "/root/.codex",
            "CODEX_MODEL": model,
            "CODEX_RUNTIME_DEBUG_PATH": str(EnvironmentPaths.agent_dir / self._DEBUG_FILENAME),
            "EXPERIMENT_SEED": os.environ.get("EXPERIMENT_SEED", ""),
            "SEED": os.environ.get("SEED", ""),
            "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", ""),
        }

        reasoning_effort = self._reasoning_effort
        reasoning_flag = f"-c model_reasoning_effort={reasoning_effort} " if reasoning_effort else ""
        codex_path_setup = (
            "export NVM_DIR=/root/.nvm; "
            "if [ -s \"$NVM_DIR/nvm.sh\" ]; then . \"$NVM_DIR/nvm.sh\"; fi; "
            "CODEX_BIN=$(find /root/.nvm/versions/node -path '*/bin/codex' | head -n 1); "
            "if [ -z \"$CODEX_BIN\" ]; then echo 'codex binary not found under /root/.nvm' >&2; exit 127; fi; "
            "export PATH=\"$(dirname \"$CODEX_BIN\"):$PATH\"; "
        )

        prepare_command = (
            "set -euo pipefail; "
            "mkdir -p /logs/agent \"$CODEX_HOME\" "
            f"{shlex.quote(str(EnvironmentPaths.agent_dir))}; "
            f"printf '%s\\n' {config_escaped} > \"$CODEX_HOME/config.toml\"; "
            f"printf '%s\\n' {debug_json} > {debug_path}; "
            f"{codex_path_setup}"
            f"command -v codex > {binary_path_file}; "
            f"codex --version > {version_file}"
        )

        run_command = (
            "set -euo pipefail; "
            "cd /root; "
            f"{codex_path_setup}"
            "codex exec "
            "--dangerously-bypass-approvals-and-sandbox "
            "--skip-git-repo-check "
            f"--model \"$CODEX_MODEL\" "
            "--json "
            "--enable unified_exec "
            f"{reasoning_flag}"
            "-- "
            f"{escaped_instruction} "
            f"2>&1 </dev/null | tee {output_path}; "
            "rc=${PIPESTATUS[0]}; "
            "python3 - <<'PY'\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "path = Path(os.environ['CODEX_RUNTIME_DEBUG_PATH'])\n"
            "data = json.loads(path.read_text(encoding='utf-8'))\n"
            "data['codex_exec_invoked'] = True\n"
            "data['codex_model_runtime'] = os.environ.get('CODEX_MODEL')\n"
            "data['codex_home'] = os.environ.get('CODEX_HOME')\n"
            "data['requested_seed_runtime'] = os.environ.get('EXPERIMENT_SEED') or os.environ.get('SEED')\n"
            "data['pythonhashseed_runtime'] = os.environ.get('PYTHONHASHSEED')\n"
            "path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding='utf-8')\n"
            "PY\n"
            "exit \"$rc\""
        )

        return [
            ExecInput(command=prepare_command, env=env),
            ExecInput(command=run_command, env=env, cwd="/root", timeout_sec=3600),
        ]
