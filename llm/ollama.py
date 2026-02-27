import json
import logging
import urllib.request
import urllib.error

from config import config
from storage.models import ErrorAnalysis, ErrorRecord
from llm.base import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert backend engineer performing root cause analysis on production errors \
from a FastAPI application. Analyse the error and respond with ONLY a JSON object \
(no markdown, no preamble) with these exact keys:
- short_description: one sentence describing the error
- root_cause: clear explanation of why it is happening
- suggested_fix: concrete actionable fix
- confidence: one of "high", "medium", or "low"
"""


def _build_user_message(error: ErrorRecord, code_context: str | None) -> str:
    parts = [
        f"Logger: {error.logger_name}",
        f"Occurrences: {error.occurrence_count}",
        f"Message:\n{error.message_template}",
    ]
    if error.sample_traceback:
        parts.append(f"Traceback:\n{error.sample_traceback}")
    if code_context:
        parts.append(f"Source code:\n{code_context}")
    return "\n\n".join(parts)


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        self._base_url = config.ollama_base_url.rstrip("/")
        self._model = config.ollama_model

    def analyze_error(
        self,
        error: ErrorRecord,
        code_context: str | None,
    ) -> ErrorAnalysis:
        prompt = _build_user_message(error, code_context)

        payload = json.dumps({
            "model": self._model,
            "system": _SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read())
                raw = body.get("response", "")
        except urllib.error.URLError as e:
            logger.error("Ollama request failed: %s", e)
            return self._fallback()

        try:
            # Strip accidental markdown fences if the model adds them
            clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(clean)
            return ErrorAnalysis(
                short_description=data["short_description"],
                root_cause=data["root_cause"],
                suggested_fix=data["suggested_fix"],
                confidence=data.get("confidence", "low"),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse Ollama response: %s\nRaw: %s", e, raw[:200])
            return self._fallback()

    def _fallback(self) -> ErrorAnalysis:
        return ErrorAnalysis(
            short_description="Analysis unavailable",
            root_cause="Ollama did not return a parseable response.",
            suggested_fix="Review the error manually or switch to Claude provider.",
            confidence="low",
        )
