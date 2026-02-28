"""
Database interface for automation storage and execution logging.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AutomationDatabase(ABC):
    """
    Abstract interface for automation database operations.

    Implementations should handle:
    - Automation CRUD operations
    - Execution logging
    - Service capabilities lookup
    """

    @abstractmethod
    async def get_automation(self, automation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get an automation by ID.

        Args:
            automation_id: Automation UUID
            user_id: User ID for ownership verification

        Returns:
            Automation dict if found and owned by user, None otherwise
        """
        pass

    @abstractmethod
    async def create_automation(
        self,
        user_id: str,
        automation: Dict[str, Any]
    ) -> str:
        """
        Create a new automation.

        Args:
            user_id: User ID who owns the automation
            automation: Automation definition dict

        Returns:
            Created automation ID
        """
        pass

    @abstractmethod
    async def update_automation(
        self,
        automation_id: str,
        user_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update an existing automation.

        Args:
            automation_id: Automation UUID
            user_id: User ID for ownership verification
            updates: Fields to update

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_automation(self, automation_id: str, user_id: str) -> bool:
        """
        Delete an automation.

        Args:
            automation_id: Automation UUID
            user_id: User ID for ownership verification

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def list_automations(
        self,
        user_id: str,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List automations for a user.

        Args:
            user_id: User ID
            status: Optional status filter ("active", "paused", etc.)

        Returns:
            List of automation dicts
        """
        pass

    @abstractmethod
    async def log_execution(
        self,
        automation_id: str,
        user_id: str,
        log_entry: Dict[str, Any]
    ) -> str:
        """
        Log an automation execution.

        Args:
            automation_id: Automation UUID
            user_id: User ID
            log_entry: Execution log data (status, duration, results, etc.)

        Returns:
            Log entry ID
        """
        pass

    @abstractmethod
    async def get_service_capabilities(self, service_name: str) -> Optional[Dict[str, Any]]:
        """
        Get capabilities for a service (webhook support, polling intervals, etc.).

        Args:
            service_name: Service name (e.g., "Oura", "Gmail")

        Returns:
            Service capabilities dict if found, None otherwise
        """
        pass
