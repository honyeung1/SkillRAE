import os
import shlex
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from harbor.agents.installed.base import ExecInput
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.trial.paths import EnvironmentPaths

from skillsbench_private.docker_proxy import get_api_proxy_env


class LocalBinaryClaudeCode(ClaudeCode):
    """Use a pre-fetched Claude Code binary instead of the online installer."""

    _LOCAL_BINARY_PATH = Path(
        os.environ.get(
            "SKILLSBENCH_CLAUDE_LOCAL_BINARY",
            Path.home() / ".local" / "share" / "claude" / "versions" / "2.1.78",
        )
    )
    _CONTAINER_UID = int(os.environ.get("CLAUDE_CODE_CONTAINER_UID", "1007"))
    _CONTAINER_GID = int(os.environ.get("CLAUDE_CODE_CONTAINER_GID", "1007"))
    _PROXY_MODEL = os.environ.get(
        "CLAUDE_CODE_PROXY_MODEL", "claude-3-5-sonnet-20241022"
    )

    @classmethod
    def _api_mode_enabled(cls) -> bool:
        return bool(
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or os.environ.get("OPENAI_API_KEY")
            or (
                os.environ.get("ANTHROPIC_BASE_URL")
                and os.environ.get("ANTHROPIC_API_KEY")
            )
            or (
                os.environ.get("ANTHROPIC_BASE_URL")
                and os.environ.get("ANTHROPIC_AUTH_TOKEN")
            )
        )

    @classmethod
    def _host_api_base_url(cls) -> str:
        return "http://localhost:4000"

    @classmethod
    def _rootless_host_ip(cls) -> str:
        override = os.environ.get("CLAUDE_CODE_HOST_IP")
        if override:
            return override
        for candidate in os.popen("hostname -I").read().split():
            if candidate.startswith("127."):
                continue
            if candidate.startswith("172.17."):
                continue
            return candidate
        return "127.0.0.1"

    @classmethod
    def _host_api_key(cls) -> str:
        return "anything"

    @classmethod
    def _container_base_url(cls) -> str:
        return f"http://{cls._rootless_host_ip()}:4000"

    @classmethod
    def _binary_file_policy(cls) -> str:
        return """
Universal binary-file policy:
- Before reading any file, classify it by extension and metadata.
- For binary or semi-binary files, DO NOT use the Read tool directly.
- Treat at least these as binary/semi-binary by default: .stl, .png, .jpg, .jpeg, .zip, .npy, .npz, .pt, .pth, .pdf, .docx, .xlsx, .parquet, .sqlite.
- For any file whose MIME is not text/*, or whose bytes are mostly non-printable, do not read raw contents into context.
- First inspect with small metadata-only commands such as:
  - stat for size and timestamps
  - file --mime-type when available
  - python3 snippets that emit JSON metadata only
- If common inspection tools are unavailable, fall back to Python stdlib probes instead of stopping.
- If you need binary content analysis, use Bash or Python to parse the file and return only structured summaries, counts, headers, dimensions, record counts, or extracted text/metadata.
- Never dump raw binary payloads, large hexdumps, or entire opaque documents into context.
- For opaque binary formats, write and run a small parser script, then inspect its structured output.
- For record-oriented binary formats, validate the layout before full parsing:
  - determine total file size
  - identify any fixed header size
  - read any declared record count from the header when present
  - compute the implied bytes-per-record from file_size minus header_size
  - verify that header_size + record_count * record_size matches the file size before iterating
- If the implied record size does not match your assumption, stop and correct the parser instead of forcing the parse.
- Prefer small sanity-check probes that print only header fields, sizes, counts, and derived record sizes before the full parser.
"""

    def _augment_instruction(self, instruction: str) -> str:
        return (
            f"{self._binary_file_policy().strip()}\n\n"
            "Execution constraints:\n"
            "- You are running inside a containerized task workspace.\n"
            "- Use relative or absolute paths that already exist in the workspace.\n"
            "- Prefer Bash/Python metadata extraction for binary files.\n"
            "- Write intermediate scripts and scratch outputs to /tmp/skillsbench-work.\n"
            "- Treat /root/mass_report.json as the final verifier-facing output path.\n"
            "- Do not create new intermediate scripts directly under /root when /tmp/skillsbench-work is available.\n"
            "- Only use Read on plain-text files that are safe to read directly.\n"
            "- For structured binary formats, do a layout sanity check before the full parse and correct the parser if sizes do not line up.\n"
            "- If a binary parser fails, inspect file size, header fields, and implied record size, then retry with a corrected parser.\n"
            "- When you have the final result, write valid JSON directly to /root/mass_report.json.\n\n"
            f"Task instruction:\n{instruction}"
        )

    def _get_session_dir(self) -> Path | None:
        """Choose the project session directory that actually contains the run JSONL."""
        sessions_root = self.logs_dir / "sessions" / "projects"
        if not sessions_root.is_dir():
            return None

        candidate_dirs = []
        for child in sessions_root.iterdir():
            if not child.is_dir():
                continue
            if any(child.glob("*.jsonl")):
                candidate_dirs.append(child)

        if len(candidate_dirs) == 1:
            return candidate_dirs[0]

        preferred = sessions_root / "-root"
        if preferred.is_dir() and any(preferred.glob("*.jsonl")):
            return preferred

        if len(candidate_dirs) > 1:
            print(
                "Multiple Claude Code session directories found; "
                "could not identify the correct one"
            )
        return None

    def _build_runtime_env(self) -> dict[str, str]:
        api_mode = self._api_mode_enabled()
        rootless_host_ip = self._rootless_host_ip()
        env = {
            "ANTHROPIC_API_KEY": self._host_api_key() if api_mode else "local",
            "ANTHROPIC_BASE_URL": self._container_base_url()
            if api_mode
            else "http://127.0.0.1:3999",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "FORCE_AUTO_BACKGROUND_TASKS": "1",
            "ENABLE_BACKGROUND_TASKS": "1",
            "IS_SANDBOX": "1",
        }
        env.update(
            get_api_proxy_env(
                extra_no_proxy=("host.docker.internal", rootless_host_ip)
            )
        )

        if self.model_name:
            if api_mode:
                env["ANTHROPIC_MODEL"] = self.model_name.split("/")[-1]
            elif self.model_name.startswith("openai///"):
                env["ANTHROPIC_MODEL"] = self._PROXY_MODEL
            else:
                env["ANTHROPIC_MODEL"] = self.model_name.split("/")[-1]
        elif "ANTHROPIC_MODEL" in os.environ:
            env["ANTHROPIC_MODEL"] = os.environ["ANTHROPIC_MODEL"]
        else:
            env["ANTHROPIC_MODEL"] = self._PROXY_MODEL

        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = env["ANTHROPIC_MODEL"]
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = env["ANTHROPIC_MODEL"]
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = env["ANTHROPIC_MODEL"]
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = env["ANTHROPIC_MODEL"]

        max_thinking_tokens = self._max_thinking_tokens
        if max_thinking_tokens is not None:
            env["MAX_THINKING_TOKENS"] = str(max_thinking_tokens)
        elif "MAX_THINKING_TOKENS" in os.environ:
            env["MAX_THINKING_TOKENS"] = os.environ["MAX_THINKING_TOKENS"]

        env["CLAUDE_CONFIG_DIR"] = (EnvironmentPaths.agent_dir / "sessions").as_posix()
        return env

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        instruction = self._augment_instruction(instruction)
        escaped_instruction = shlex.quote(instruction)
        env = self._build_runtime_env()
        effective_model = env["ANTHROPIC_MODEL"]

        setup_command = (
            'mkdir -p "$CLAUDE_CONFIG_DIR"/debug "$CLAUDE_CONFIG_DIR"/projects/-app '
            '"$CLAUDE_CONFIG_DIR"/shell-snapshots "$CLAUDE_CONFIG_DIR"/statsig '
            '"$CLAUDE_CONFIG_DIR"/todos /tmp/claude-home /tmp/skillsbench-work /logs/agent && '
            "chmod o+rwx /root && "
            f"chown -R {self._CONTAINER_UID}:{self._CONTAINER_GID} "
            '/tmp/claude-home /tmp/skillsbench-work 2>/dev/null || true && '
            'chmod -R 777 "$CLAUDE_CONFIG_DIR" /logs/agent 2>/dev/null || true && '
            f"touch /root/mass_report.json && chown {self._CONTAINER_UID}:{self._CONTAINER_GID} "
            "/root/mass_report.json && chmod 666 /root/mass_report.json && "
            'if [ -d /root/.claude/skills ]; then '
            'cp -r /root/.claude/skills "$CLAUDE_CONFIG_DIR"/skills 2>/dev/null || true; '
            "fi"
        )

        max_turns_flag = ""
        max_turns = self._max_turns
        if max_turns is None and "CLAUDE_CODE_MAX_TURNS" in os.environ:
            max_turns = int(os.environ["CLAUDE_CODE_MAX_TURNS"])
        if max_turns is not None:
            max_turns_flag = f"--max-turns {max_turns} "

        env_exports = "".join(
            f"export {key}={shlex.quote(value)}; " for key, value in sorted(env.items())
        )

        inner_command = (
            'export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"; '
            "export HOME=/tmp/claude-home; "
            "mkdir -p \"$HOME\"; "
            f"{env_exports}"
            f"claude --dangerously-skip-permissions --setting-sources local "
            f"--model {shlex.quote(effective_model)} "
            f"--verbose --output-format stream-json {max_turns_flag}"
            f"-p {escaped_instruction} 2>&1 </dev/null"
        )

        run_command = (
            "chmod o+rwx /root && "
            "mkdir -p /tmp/claude-home /tmp/skillsbench-work \"$CLAUDE_CONFIG_DIR\" /logs/agent && "
            f"chown -R {self._CONTAINER_UID}:{self._CONTAINER_GID} "
            "/tmp/claude-home /tmp/skillsbench-work 2>/dev/null || true && "
            "chmod -R 777 \"$CLAUDE_CONFIG_DIR\" /logs/agent 2>/dev/null || true && "
            f"touch /root/mass_report.json && chown {self._CONTAINER_UID}:{self._CONTAINER_GID} "
            "/root/mass_report.json && chmod 666 /root/mass_report.json && "
            f"setpriv --reuid={self._CONTAINER_UID} --regid={self._CONTAINER_GID} "
            f"--clear-groups sh -lc {shlex.quote(inner_command)}"
        )

        return [
            ExecInput(command=setup_command, env=env),
            ExecInput(command=run_command, env=env),
        ]

    async def setup(self, environment: BaseEnvironment) -> None:
        await environment.exec(
            command="echo 'PS1=1 . ~/.bashrc 2>/dev/null; unset PS1' >> ~/.bash_profile"
        )
        await environment.exec(command="mkdir -p /installed-agent /usr/local/bin")

        if not self._LOCAL_BINARY_PATH.exists():
            raise FileNotFoundError(
                f"Claude binary not found at {self._LOCAL_BINARY_PATH}"
            )

        await environment.upload_file(
            source_path=self._LOCAL_BINARY_PATH,
            target_path="/usr/local/bin/claude",
        )

        result = await environment.exec(
            command="chmod +x /usr/local/bin/claude && /usr/local/bin/claude --version",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        setup_dir = self.logs_dir / "setup"
        setup_dir.mkdir(parents=True, exist_ok=True)
        (setup_dir / "return-code.txt").write_text(str(result.return_code))

        if result.stdout:
            (setup_dir / "stdout.txt").write_text(result.stdout)

        if result.stderr:
            (setup_dir / "stderr.txt").write_text(result.stderr)

        if result.return_code != 0:
            raise RuntimeError(
                f"Agent setup failed with exit code {result.return_code}. "
                f"See logs in {setup_dir}"
            )
