from __future__ import annotations

import json
import os
from pathlib import Path

from libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills import (
    HarborTerminus2WithSkills,
)


class ClaudeCodeTerminusBridge(HarborTerminus2WithSkills):
    """Expose the SkillsBench Terminus loop under Harbor's `claude-code` name.

    This is a private adapter layer. It preserves the upstream SkillsBench
    prompt/runner/skill protocol, but it is allowed to choose a different model
    transport so the private benchmark route matches the locally verified Claude
    Messages endpoint.
    """

    @staticmethod
    def name() -> str:
        return "claude-code"

    def version(self) -> str | None:
        return "skillsbench-terminus-bridge"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        parser_name: str = "json",
        skill_format: str = "json",
        prompt_template: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ):
        model_name, api_base, api_key = self._normalize_runtime(
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
        )
        self._adapter_runtime = {
            "adapter_layer": "skillsbench_private.harbor_ext.claude_terminus_agent",
            "note": "Private adapter runtime selection. Not an upstream SkillsBench baseline file.",
            "model_name": model_name,
            "api_base": api_base,
            "api_key_present": bool(api_key),
        }
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            parser_name=parser_name,
            skill_format=skill_format,
            prompt_template=prompt_template,
            api_base=api_base,
            api_key=api_key,
            **kwargs,
        )
        self._write_adapter_runtime_debug()

    def _write_adapter_runtime_debug(self) -> None:
        debug_path = self.logs_dir / "private_adapter_runtime.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(
            json.dumps(self._adapter_runtime, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _normalize_runtime(
        model_name: str | None,
        api_base: str | None,
        api_key: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        if model_name and "/" in model_name:
            return model_name, api_base, api_key

        openai_api_base = (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or os.environ.get("OPENAI_URL_BASE")
        )
        openai_api_key = os.environ.get("OPENAI_API_KEY")

        anthropic_api_base = os.environ.get("ANTHROPIC_BASE_URL")
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

        # Private adapter rule:
        # Prefer the Anthropic-compatible Messages endpoint when it is available,
        # because that is the path we have manually validated against LiteLLM.
        if anthropic_api_base or anthropic_api_key:
            normalized_model = f"anthropic/{model_name}" if model_name else model_name
            normalized_api_base = api_base or anthropic_api_base
            normalized_api_key = api_key or anthropic_api_key or "temp-key"
            return normalized_model, normalized_api_base, normalized_api_key

        # Fallback to the previous OpenAI-compatible path if no Anthropic-style
        # endpoint is configured in the runtime environment.
        if openai_api_base:
            normalized_model = f"openai/{model_name}" if model_name else model_name
            return normalized_model, api_base or openai_api_base, api_key or openai_api_key

        return model_name, api_base, api_key
