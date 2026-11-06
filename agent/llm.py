"""
llm.py — the one place we talk to the Claude API.

Two tricks keep the AI's output predictable:

1. FORCED TOOL CALL (structured output)
   Instead of asking Claude to "reply in JSON" and hoping, we describe a "tool"
   with a JSON schema and set tool_choice to force Claude to call it. The API
   hands us back a Python dict that already matches the schema — no messy
   parsing of free text.

2. TEMPERATURE 0
   Makes answers as consistent as possible run to run.

Neither trick stops hallucination on its own. That's grounding.py's job: every
value we get back is checked against the real page before we use it.
"""

from __future__ import annotations

import os


class LLMError(RuntimeError):
    pass


class Claude:
    def __init__(self, model: str = "claude-haiku-5-5", api_key: str | None = None):
        try:
            import anthropic  # imported here so the rest of the app works without it
        except ImportError as e:
            raise LLMError("The 'anthropic' package isn't installed. Run: pip install -r requirements.txt") from e

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError(
                "No API key found. Get one at console.anthropic.com, then set the "
                "ANTHROPIC_API_KEY environment variable (see README)."
            )
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model

    def call_tool(self, *, system: str, prompt: str, tool_name: str,
                  tool_description: str, schema: dict, max_tokens: int = 4096) -> dict:
        """Ask Claude something and get back a dict shaped like `schema`."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            tools=[{"name": tool_name, "description": tool_description, "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},   # <- forces structured output
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input   # already a Python dict
        raise LLMError("Claude didn't return the expected structured output.")
