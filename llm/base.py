from abc import ABC, abstractmethod

from storage.models import ErrorAnalysis, ErrorRecord


class LLMProvider(ABC):
    """
    Abstract interface for LLM-backed error analysis.

    Each implementation receives a fully populated ErrorRecord plus any
    relevant code context and must return a structured ErrorAnalysis.
    Keeping this interface narrow means any component that needs LLM
    analysis can accept an LLMProvider and remain backend-agnostic.
    """

    @abstractmethod
    def analyze_error(
        self,
        error: ErrorRecord,
        code_context: str | None,
    ) -> ErrorAnalysis:
        """
        Analyse a single error and return structured findings.

        Args:
            error:        The ErrorRecord with message, traceback, logger, etc.
            code_context: Source code window around the error line, or None
                          if the file could not be resolved.

        Returns:
            ErrorAnalysis with short_description, root_cause, suggested_fix,
            and a confidence level ("high" | "medium" | "low").
        """
        ...
