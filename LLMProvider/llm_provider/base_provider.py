# llm/base_provider.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, Iterator
import time
import requests
import json


class BaseLLMProvider(ABC):
    """Base class for all LLM providers with common functionality"""

    def __init__(self, api_key: str, default_model: str = None, timeout: int = 60):
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.usage_stats = {
            "total_requests": 0,
            "total_tokens": 0,
            "failed_requests": 0,
        }

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        **kwargs
    ) -> Union[str, Iterator[str]]:
        """
        Send chat completion request

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Model identifier (uses default if None)
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            **kwargs: Additional provider-specific parameters

        Returns:
            Response text or stream iterator
        """
        pass

    @abstractmethod
    def embed(
        self, text: Union[str, List[str]], model: Optional[str] = None, **kwargs
    ) -> List[List[float]]:
        """
        Generate embeddings for text

        Args:
            text: Input text or list of texts
            model: Embedding model identifier
            **kwargs: Additional parameters

        Returns:
            List of embedding vectors
        """
        pass

    @abstractmethod
    def get_models(self) -> List[Dict[str, Any]]:
        """
        Get list of available models

        Returns:
            List of model information dictionaries
        """
        pass

    def check_connection(self) -> bool:
        """
        Check if provider connection is working

        Returns:
            True if connection is successful
        """
        try:
            models = self.get_models()
            return len(models) > 0
        except Exception:
            return False

    def get_usage(self) -> Dict[str, Any]:
        """
        Get usage statistics

        Returns:
            Dictionary with usage statistics
        """
        return self.usage_stats.copy()

    def _make_request_with_retry(
        self, request_func, max_retries: int = 3, initial_delay: float = 1.0, **kwargs
    ) -> requests.Response:
        """
        Make HTTP request with exponential backoff retry

        Args:
            request_func: Function that makes the request
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay between retries in seconds
            **kwargs: Arguments for request_func

        Returns:
            Response object

        Raises:
            Exception: If all retries fail
        """
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                response = request_func(**kwargs)
                response.raise_for_status()
                self.usage_stats["total_requests"] += 1
                return response

            except requests.exceptions.RequestException as e:
                last_exception = e
                self.usage_stats["failed_requests"] += 1

                if attempt < max_retries:
                    delay = initial_delay * (2**attempt)  # Exponential backoff
                    time.sleep(delay)
                    continue
                else:
                    raise last_exception

    def _update_token_usage(self, response_data: Dict[str, Any]):
        """Update token usage statistics from response"""
        if "usage" in response_data:
            usage = response_data["usage"]
            if "total_tokens" in usage:
                self.usage_stats["total_tokens"] += usage["total_tokens"]
