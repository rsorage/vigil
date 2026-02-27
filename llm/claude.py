import json
import logging

import anthropic

from config import config
from storage.models import ErrorAnalysis, ErrorRecord
from llm.base import LLMProvider

logger = logging.getLogger(__name__)

# The analysis tool schema — Claude is asked to call this with its findings,
# giving us reliably structured output without fragile JSON parsing.
_ANALYSIS_TOOL = {
    "name": "report_error_analysis",
    "description": "Report the structured analysis of a backend error.",
    "input_schema": {
        "type": "object",
        "properties": {
            "short_description": {
                "type": "string",
                "description": "One sentence describing what the error is.",
            },
            "root_cause": {
                "type": "string",
                "description": (
                    "A clear explanation of why this error is happening, "
                    "referencing specific code locations where relevant."
                ),
            },
            "suggested_fix": {
                "type": "string",
                "description": (
                    "Concrete, actionable fix suggestion. "
                    "Include code snippets if helpful."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": (
                    "Confidence in the analysis: high if root cause is clear "
                    "from the code, medium if inferred, low if speculative."
                ),
            },
        },
        "required": ["short_description", "root_cause", "suggested_fix", "confidence"],
    },
}

_SYSTEM_PROMPT = """\
You are an expert backend engineer performing root cause analysis on production errors \
from a FastAPI application. You will be given an error record containing the logger name, \
error message, stack trace, and optionally a window of source code around the error site.

Your job is to analyse the error and call the report_error_analysis tool with your findings. \
Be precise and technical. Reference specific file paths, line numbers, and variable names \
where relevant. If you can see the source code, use it — don't speculate about things \
that are visible in the code.
"""


def _build_user_message(error: ErrorRecord, code_context: str | None) -> str:
    parts = [
        f"## Error record",
        f"**Logger:** `{error.logger_name}`",
        f"**Occurrences:** {error.occurrence_count}",
        f"**First seen:** {error.first_seen}",
        f"**Last seen:** {error.last_seen}",
        f"",
        f"**Message:**",
        f"```",
        error.message_template,
        f"```",
    ]

    if error.sample_traceback:
        parts += [
            f"",
            f"**Traceback:**",
            f"```python",
            error.sample_traceback,
            f"```",
        ]

    if code_context:
        parts += [
            f"",
            f"**Source code context:**",
            f"```python",
            code_context,
            f"```",
        ]
    else:
        parts.append(
            "\n*No source code context available — analyse based on the message and traceback.*"
        )

    return "\n".join(parts)


class ClaudeProvider(LLMProvider):
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._model = config.anthropic_model

    def analyze_error(
        self,
        error: ErrorRecord,
        code_context: str | None,
    ) -> ErrorAnalysis:
        user_message = _build_user_message(error, code_context)

        logger.debug("Sending error %s to Claude (%s)", error.fingerprint, self._model)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=[_ANALYSIS_TOOL],
            tool_choice={"type": "any"},  # force tool use — no free-form text
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract the tool_use block
        tool_block = next(
            (block for block in response.content if block.type == "tool_use"),
            None,
        )

        if tool_block is None:
            logger.warning(
                "Claude did not call the analysis tool for error %s — falling back",
                error.fingerprint,
            )
            return ErrorAnalysis(
                short_description="Analysis unavailable",
                root_cause="The LLM did not return a structured analysis.",
                suggested_fix="Review the error manually.",
                confidence="low",
            )

        data = tool_block.input
        logger.info(
            "Analysis complete for %s (confidence: %s)",
            error.fingerprint,
            data.get("confidence"),
        )

        return ErrorAnalysis(
            short_description=data["short_description"],
            root_cause=data["root_cause"],
            suggested_fix=data["suggested_fix"],
            confidence=data["confidence"],
        )
