import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
from harbor.models.agent.context import AgentContext


class MinimalToolAgent(BaseInstalledAgent):
    @classmethod
    def name(cls) -> str:
        return "minimal-tool-agent"

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).with_name("install-minimal-tool-agent.sh.j2")

    def populate_context_post_run(self, context: AgentContext) -> None:
        log_path = self.logs_dir / "command-0" / "stdout.txt"
        metadata: dict[str, object] = {"agent": "minimal-tool-agent"}

        if log_path.exists():
            output = log_path.read_text(encoding="utf-8", errors="replace")
            metadata["trajectory_log_path"] = "/logs/agent/minimal_tool_agent.txt"
            metadata["stdout_log_path"] = str(log_path)
            metadata["instruction_written"] = "/root/instruction.md" in output
            metadata["reasoning_started"] = "Thought:" in output or "Action:" in output
            metadata["tool_actions"] = output.count("Action:")
            metadata["final_answer_done"] = "Final Answer: DONE" in output
            metadata["model_calls"] = output.count("MODEL CALL")
            metadata["output_tail"] = output[-4000:]

        if context.metadata:
            metadata = {**context.metadata, **metadata}
        context.metadata = metadata

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        instruction_escaped = shlex.quote(instruction)
        model_name = shlex.quote(self.model_name)
        max_steps = shlex.quote(os.environ.get("MINIMAL_TOOL_AGENT_MAX_STEPS", "30"))
        bash_timeout = shlex.quote(os.environ.get("MINIMAL_TOOL_AGENT_BASH_TIMEOUT_SEC", "90"))
        max_output_chars = shlex.quote(os.environ.get("MINIMAL_TOOL_AGENT_MAX_OUTPUT_CHARS", "8000"))
        llm_timeout = shlex.quote(os.environ.get("MINIMAL_TOOL_AGENT_LLM_TIMEOUT_SEC", "180"))
        env = {
            "OPENAI_API_BASE": os.environ.get("OPENAI_API_BASE", "http://172.17.0.1:9100/v1"),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "temp-key"),
            "MINIMAL_TOOL_AGENT_MODEL": self.model_name,
        }

        command = (
            "set -euo pipefail; "
            "mkdir -p /logs/agent && "
            "printf '%s' "
            f"{instruction_escaped} > /root/instruction.md && "
            "python3 /installed-agent/minimal_tool_agent_runner.py "
            f"--instruction-file /root/instruction.md "
            f"--model {model_name} "
            f"--max-steps {max_steps} "
            f"--bash-timeout {bash_timeout} "
            f"--max-output-chars {max_output_chars} "
            f"--llm-timeout {llm_timeout} "
            "2>&1 | tee /logs/agent/minimal_tool_agent.txt"
        )

        return [ExecInput(command=command, cwd="/root", env=env, timeout_sec=3600)]
