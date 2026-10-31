"""Version 1's single reviewer: a strict Adobe Stock inspector."""

from __future__ import annotations

from adobe_reviewer.agents.base import BaseAgent
from adobe_reviewer.agents.registry import register_agent
from adobe_reviewer.config.models import AgentSettings
from adobe_reviewer.models.schemas import ReviewContext


@register_agent("adobe_reviewer")
class AdobeReviewerAgent(BaseAgent):
    """Reviews an image against the full Adobe Stock checklist in one pass.

    Uses the prompt set named by the agent's ``prompt`` setting
    (``prompts/<prompt>.system.md`` and ``prompts/<prompt>.user.md``).
    """

    def build_prompts(self, context: ReviewContext, settings: AgentSettings) -> tuple[str, str]:
        """System prompt as written; user prompt filled with the file name and measurements."""
        prompt_set = self._prompts.get(settings.prompt)
        user_text = self._prompts.render_user(
            settings.prompt,
            image_name=context.image_path.name,
            metrics_block=self.metrics_text(context),
        )
        return prompt_set.system, user_text
