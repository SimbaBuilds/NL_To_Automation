"""
Tool registry interface for automation execution.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Dict, List, Optional


@dataclass
class Tool:
    """
    A tool that can be executed in an automation.
    """
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON schema for parameters
    returns: str  # Description of return value
    handler: Callable[..., Awaitable[Any]]  # Async function that executes the tool
    service: Optional[str] = None  # Service this tool belongs to (e.g., "Oura", "Gmail")
    metadata: Optional[Dict[str, Any]] = None  # Additional tool metadata


class ToolRegistry(ABC):
    """
    Abstract interface for tool discovery and execution.

    Implementations should provide:
    - Tool lookup by name
    - Tool listing (optionally filtered by service)
    - Tool execution with parameter validation
    """

    @abstractmethod
    async def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """
        Get a tool by its name.

        Args:
            name: Tool name (e.g., "oura_get_daily_sleep")

        Returns:
            Tool instance if found, None otherwise
        """
        pass

    @abstractmethod
    async def list_tools(self, service: Optional[str] = None) -> List[Tool]:
        """
        List all available tools, optionally filtered by service.

        Args:
            service: Optional service name to filter by (e.g., "Oura")

        Returns:
            List of Tool instances
        """
        pass

    @abstractmethod
    async def execute_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_id: str,
        **kwargs
    ) -> Any:
        """
        Execute a tool with the given parameters.

        Args:
            tool_name: Name of the tool to execute
            parameters: Parameters to pass to the tool
            user_id: User ID for authentication/context
            **kwargs: Additional context (request_id, timeout, etc.)

        Returns:
            Tool execution result

        Raises:
            Exception: If tool not found or execution fails
        """
        pass
