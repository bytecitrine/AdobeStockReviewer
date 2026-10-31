"""Runs the configured reviewer agent(s) and produces one final verdict.

Version 1 implements the ``single`` mode: one agent, its verdict is the result.
Version 2 adds ``sequential`` / ``parallel_judge`` by writing one runner method each
and registering it in ``_runners``; the rest of the application does not change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from adobe_reviewer.agents import create_agent
from adobe_reviewer.agents.base import BaseAgent
from adobe_reviewer.clients.openrouter import OpenRouterClient
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import ConfigError
from adobe_reviewer.models.schemas import AgentResult, ReviewContext, ReviewVerdict
from adobe_reviewer.prompts.manager import PromptManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    """The pipeline's final verdict plus every agent's individual result."""

    verdict: ReviewVerdict
    agent_results: tuple[AgentResult, ...]

    @property
    def reviewed_by(self) -> list[str]:
        """Names of the agents that ran, in order."""
        return [result.agent_name for result in self.agent_results]

    @property
    def models_used(self) -> list[str]:
        """Distinct models used, in order of first use."""
        return list(dict.fromkeys(result.model for result in self.agent_results))

    @property
    def total_cost_usd(self) -> float | None:
        """Total cost if the API reported any, else ``None``."""
        costs = [r.cost_usd for r in self.agent_results if r.cost_usd is not None]
        return sum(costs) if costs else None


class AgentPipeline:
    """Builds the agents from the live configuration and runs them in the configured mode.

    Agents are rebuilt for every run, so edits to ``settings.json`` (model, prompt,
    even which agents run) apply to the next image without a restart.

    Args:
        config: The application's :class:`ConfigManager`.
        client: The :class:`OpenRouterClient` shared by all agents.
        prompts: The :class:`PromptManager` shared by all agents.
    """

    def __init__(self, config: ConfigManager, client: OpenRouterClient, prompts: PromptManager) -> None:
        self._config = config
        self._client = client
        self._prompts = prompts
        self._runners: dict[str, Callable[[list[BaseAgent], ReviewContext], PipelineResult]] = {
            "single": self._run_single,
        }

    def run(self, context: ReviewContext) -> PipelineResult:
        """Review one image with the configured agent(s).

        Raises:
            ConfigError: If the configured mode or an agent type is not available.
            OpenRouterError / ReviewParseError / PromptError: Propagated from the agents.
        """
        pipeline_settings = self._config.settings.pipeline
        runner = self._runners.get(pipeline_settings.mode)
        if runner is None:
            raise ConfigError(
                f"pipeline.mode '{pipeline_settings.mode}' is not available in this version. "
                f"Available modes: {', '.join(sorted(self._runners))}"
            )
        agents = [
            create_agent(agent_settings, config=self._config, client=self._client, prompts=self._prompts)
            for agent_settings in pipeline_settings.agents
        ]
        return runner(agents, context)

    @staticmethod
    def _run_single(agents: list[BaseAgent], context: ReviewContext) -> PipelineResult:
        """Version 1: run the one configured agent; its verdict is final."""
        result = agents[0].run(context)
        return PipelineResult(verdict=result.verdict, agent_results=(result,))
