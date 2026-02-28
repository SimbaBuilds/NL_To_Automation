"""
User information provider interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class UserInfo:
    """User information for automation context."""
    id: str
    email: str
    timezone: str = "UTC"
    phone: Optional[str] = None
    name: Optional[str] = None


class UserProvider(ABC):
    """
    Abstract interface for retrieving user information.

    Used to populate {{user.*}} template variables and provide
    user context to tool executions.
    """

    @abstractmethod
    async def get_user_info(self, user_id: str) -> Optional[UserInfo]:
        """
        Get user information by ID.

        Args:
            user_id: User ID

        Returns:
            UserInfo if found, None otherwise
        """
        pass
