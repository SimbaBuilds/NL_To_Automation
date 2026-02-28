"""
LLM and search provider interfaces for opt-in intelligence.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LLMProvider(ABC):
    """
    Abstract interface for LLM inference.

    Used by llm_tools.py for:
    - llm_classify: Lightweight classification (YES/NO, categories)
    - llm_transform: Text transformation and restructuring
    - call_agent: Full agent reasoning
    """

    @abstractmethod
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Optional model override (e.g., "claude-3-haiku-20240307")
            temperature: Sampling temperature (0.0 = deterministic)
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific parameters

        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    async def track_usage(
        self,
        user_id: str,
        input_tokens: int,
        output_tokens: int,
        cost: float
    ) -> None:
        """
        Track LLM usage and cost (optional).

        Args:
            user_id: User ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cost: Cost in USD
        """
        pass


class WebSearchProvider(ABC):
    """
    Abstract interface for web search.

    Used by llm_tools.py for search_web tool.
    """

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Search the web and return results.

        Args:
            query: Search query
            max_results: Maximum number of results to return
            **kwargs: Provider-specific parameters

        Returns:
            List of result dicts with keys:
            - title: str
            - url: str
            - content: str (snippet or full text)
        """
        pass
