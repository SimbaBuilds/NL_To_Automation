"""
Abstract interfaces for extending nl_to_automation.
"""

from .tool_registry import Tool, ToolRegistry
from .database import AutomationDatabase
from .user_provider import UserInfo, UserProvider
from .llm_provider import LLMProvider, WebSearchProvider
from .notifications import NotificationHandler

__all__ = [
    'Tool',
    'ToolRegistry',
    'AutomationDatabase',
    'UserInfo',
    'UserProvider',
    'LLMProvider',
    'WebSearchProvider',
    'NotificationHandler',
]
