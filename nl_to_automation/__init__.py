"""
nl_to_automation - Natural language to deterministic automation

A declarative automation engine that executes workflows without
LLM inference at runtime.

Powers Juniper (juniper.app).
"""

__version__ = "0.1.0"

# Core types
from .types import (
    ExecutionStatus,
    ExecutionResult,
    ActionResult,
    USAGE_LIMIT_ERROR,
)

# Template and condition utilities
from .templates import (
    get_nested_value,
    resolve_template,
    resolve_parameters,
)

from .conditions import (
    compare_values,
    evaluate_clause,
    evaluate_condition,
)

# Interfaces for extension
from .interfaces import (
    Tool,
    ToolRegistry,
    AutomationDatabase,
    UserInfo,
    UserProvider,
    LLMProvider,
    WebSearchProvider,
    NotificationHandler,
)

# Executor
from .executor import execute_automation, normalize_for_context, extract_json_from_string

# Note: validation and llm_tools modules still require refactoring
# to remove Juniper-specific dependencies. See REFACTORING_STATUS.md.

__all__ = [
    "__version__",
    # Types
    "ExecutionStatus",
    "ExecutionResult",
    "ActionResult",
    "USAGE_LIMIT_ERROR",
    # Templates
    "get_nested_value",
    "resolve_template",
    "resolve_parameters",
    # Conditions
    "compare_values",
    "evaluate_clause",
    "evaluate_condition",
    # Executor
    "execute_automation",
    "normalize_for_context",
    "extract_json_from_string",
    # Interfaces
    "Tool",
    "ToolRegistry",
    "AutomationDatabase",
    "UserInfo",
    "UserProvider",
    "LLMProvider",
    "WebSearchProvider",
    "NotificationHandler",
]
