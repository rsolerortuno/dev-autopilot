"""Exceptions and stable failure classifications."""

from enum import StrEnum


class ErrorClass(StrEnum):
    RETRYABLE_QUOTA = "RETRYABLE_QUOTA"
    RETRYABLE_TIMEOUT = "RETRYABLE_TIMEOUT"
    RETRYABLE_AGENT_ERROR = "RETRYABLE_AGENT_ERROR"
    MECHANICAL_OUTPUT_ERROR = "MECHANICAL_OUTPUT_ERROR"
    JOB_CONFIGURATION_ERROR = "JOB_CONFIGURATION_ERROR"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    TEST_FAILURE = "TEST_FAILURE"
    REVIEW_FORMAT_ERROR = "REVIEW_FORMAT_ERROR"
    SCIENTIFIC_DECISION_REQUIRED = "SCIENTIFIC_DECISION_REQUIRED"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    INTERNAL_ORCHESTRATOR_ERROR = "INTERNAL_ORCHESTRATOR_ERROR"


class AutopilotError(RuntimeError):
    """Base runtime error."""


class ConfigurationError(ValueError):
    """A job configuration could not be parsed or validated."""


class PersistenceError(AutopilotError):
    """Persistent state is missing or inconsistent."""


class TransitionError(AutopilotError):
    """A requested workflow transition is illegal."""


class RunLockError(AutopilotError):
    """A run is already owned by another process."""


class AdapterError(AutopilotError):
    """An adapter contract was violated."""
