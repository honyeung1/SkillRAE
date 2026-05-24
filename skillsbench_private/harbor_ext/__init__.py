"""Harbor extension hooks used by SkillsBench."""

from .aider_agent import AiderAgent
from .minimal_tool_agent import MinimalToolAgent

__all__ = ["AiderAgent", "MinimalToolAgent"]
