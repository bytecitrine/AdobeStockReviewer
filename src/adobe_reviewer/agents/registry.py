"""Maps the ``type`` written in ``settings.json`` to an agent class.

Adding a new agent type (for example Version 2's ``technical_inspector``) means:
write the class, decorate it with ``@register_agent("technical_inspector")``, and
reference that type in ``settings.json``. Nothing else changes.
"""

from __future__ import annotations

from collections.abc import Callable

from adobe_reviewer.agents.base import BaseAgent
from adobe_reviewer.clients.openrouter import OpenRouterClient
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.config.models import AgentSettings
from adobe_reviewer.exceptions import ConfigError
from adobe_reviewer.prompts.manager import PromptManager

_AGENT_TYPES: dict[str, type[BaseAgent]] = {}


def register_agent(type_name: str) -> Callable[[type[BaseAgent]], type[BaseAgent]]:
    """Class decorator that makes an agent available under ``type_name``."""

    def decorator(cls: type[BaseAgent]) -> type[BaseAgent]:
        _AGENT_TYPES[type_name] = cls
        return cls

    return decorator


def available_agent_types() -> list[str]:
    """Names of all registered agent types."""
    return sorted(_AGENT_TYPES)


def create_agent(
    settings: AgentSettings,
    *,
    config: ConfigManager,
    client: OpenRouterClient,
    prompts: PromptManager,
) -> BaseAgent:
    """Build the agent described by ``settings``.

    Raises:
        ConfigError: If ``settings.type`` is not a registered agent type.
    """
    try:
        agent_class = _AGENT_TYPES[settings.type]
    except KeyError:
        raise ConfigError(
            f"Unknown agent type '{settings.type}' for agent '{settings.name}'. "
            f"Available types: {', '.join(available_agent_types()) or 'none'}"
        ) from None
    return agent_class(settings.name, config=config, client=client, prompts=prompts)
