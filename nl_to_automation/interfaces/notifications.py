"""
Notification handler interface for usage limits and alerts.
"""

from abc import ABC, abstractmethod
from typing import Optional


class NotificationHandler(ABC):
    """
    Abstract interface for sending notifications.

    Used by executor to notify users when:
    - Usage limits are exceeded
    - Automations fail
    - Other important events occur
    """

    @abstractmethod
    async def notify_usage_limit_exceeded(
        self,
        user_id: str,
        automation_id: str,
        automation_name: str
    ) -> None:
        """
        Notify user that usage limits have been exceeded.

        Args:
            user_id: User ID
            automation_id: Automation UUID
            automation_name: Human-readable automation name
        """
        pass

    @abstractmethod
    async def notify_automation_failed(
        self,
        user_id: str,
        automation_id: str,
        automation_name: str,
        error_summary: Optional[str] = None
    ) -> None:
        """
        Notify user that an automation failed.

        Args:
            user_id: User ID
            automation_id: Automation UUID
            automation_name: Human-readable automation name
            error_summary: Optional error description
        """
        pass

    @abstractmethod
    async def notify_custom(
        self,
        user_id: str,
        title: str,
        body: str,
        **kwargs
    ) -> None:
        """
        Send a custom notification.

        Args:
            user_id: User ID
            title: Notification title
            body: Notification body
            **kwargs: Provider-specific parameters (priority, channel, etc.)
        """
        pass
