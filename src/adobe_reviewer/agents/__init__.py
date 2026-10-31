"""Reviewer agents.

Importing this package registers every built-in agent type, so
:func:`adobe_reviewer.agents.registry.create_agent` can find them by name.
"""

from adobe_reviewer.agents.base import BaseAgent
from adobe_reviewer.agents.registry import available_agent_types, create_agent, register_agent

# Imported for its side effect: the @register_agent decorator runs on import.
from adobe_reviewer.agents.adobe_reviewer import AdobeReviewerAgent  # isort: skip

__all__ = ["AdobeReviewerAgent", "BaseAgent", "available_agent_types", "create_agent", "register_agent"]
