import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
from harbor.models.agent.context import AgentContext


class AiderAgent(BaseInstalledAgent):
    @classmethod
    def name(cls) -> str:
        return "aider"

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).with_name("install-aider.sh.j2")

    def populate_context_post_run(self, context: AgentContext) -> None:
        aider_log_path = self.logs_dir / "command-0" / "stdout.txt"
        metadata: dict[str, object] = {"agent": "aider"}

        if aider_log_path.exists():
            aider_output = aider_log_path.read_text(encoding="utf-8", errors="replace")
            metadata["aider_log_path"] = str(aider_log_path)
            metadata["aider_output_tail"] = aider_output[-4000:]
            metadata["instruction_written"] = "/root/instruction.md" in aider_output or "instruction.md" in aider_output
            metadata["llm_call_attempts"] = aider_output.count("litellm.InternalServerError") + aider_output.count(
                "Tokens:"
            )
            metadata["reasoning_started"] = metadata["llm_call_attempts"] > 0
            metadata["git_missing"] = "ERROR: git is required" in aider_output
            metadata["git_repo_initialized"] = "Initialized empty Git repository" in aider_output

        if context.metadata:
            metadata = {**context.metadata, **metadata}
        context.metadata = metadata

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        instruction_escaped = shlex.quote(instruction)
        model_name = shlex.quote(self.model_name)
        env = {
            "OPENAI_API_BASE": os.environ.get("OPENAI_API_BASE", "http://172.17.0.1:9100/v1"),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "temp-key"),
            "AIDER_ANALYTICS_DISABLE": "1",
        }

        command = (
            "set -euo pipefail; "
            "python3 -c \"import urllib.request; print(urllib.request.urlopen('http://172.17.0.1:9100/v1/models', timeout=5).read().decode())\" && "
            "printf '%s' "
            f"{instruction_escaped} > /root/instruction.md && "
            "if ! command -v git >/dev/null 2>&1; then "
            "echo 'ERROR: git is required for AiderAgent but is not installed in the task container.' >&2; "
            "exit 127; "
            "fi && "
            "git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init /root && "
            "git -C /root config user.email 'agent@local' && "
            "git -C /root config user.name 'agent' && "
            "git -C /root add -A && "
            "(git -C /root diff --cached --quiet || git -C /root commit -m 'initial state' >/dev/null 2>&1) && "
            "aider --yes-always "
            f"--model {model_name} "
            "--message-file /root/instruction.md "
            "2>&1 | tee /logs/agent/aider.txt"
        )

        return [ExecInput(command=command, cwd="/root", env=env)]
