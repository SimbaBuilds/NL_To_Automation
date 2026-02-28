"""
Data types for automation execution.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class ExecutionStatus(str, Enum):
    """Status of automation execution."""
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    USAGE_LIMIT_EXCEEDED = "usage_limit_exceeded"


# Error identifier returned by service tools when usage limit is hit
USAGE_LIMIT_ERROR = "USAGE_LIMIT_EXCEEDED"


@dataclass
class ActionResult:
    """Result of a single action execution."""
    action_id: str
    tool: str
    success: bool
    duration_ms: int
    output: Optional[Any] = None
    error: Optional[str] = None
    skipped: bool = False  # True if condition was false
    condition_result: Optional[bool] = None


@dataclass
class ExecutionResult:
    """Result of full automation execution."""
    success: bool
    status: ExecutionStatus
    actions_executed: int
    actions_failed: int
    action_results: List[ActionResult]
    duration_ms: int
    error_summary: Optional[str] = None
