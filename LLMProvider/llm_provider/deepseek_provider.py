# llm/deepseek_provider.py
import requests
import json
from typing import List, Dict, Any, Optional, Union, Iterator
from .base_provider import BaseLLMProvider


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek LLM provider implementation"""

    def __init__(
        self,
        api_key: str,
        default_model: str = "deepseek-v4-flash",
        timeout: int = 60,
        max_retries: int = 3,
    ):
        super().__init__(api_key, default_model, timeout)
        self.endpoint = "https://api.deepseek.com/chat/completions"
        self.max_retries = max_retries
        self.default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """
        Send chat completion request to DeepSeek (non-streaming).

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Model identifier (uses default if None)
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional DeepSeek-specific parameters

        Returns:
            Response text

        Raises:
            Exception: If request fails after retries
        """
        model = model or self.default_model
        timeout = kwargs.pop("timeout", self.timeout)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            # V4 enables thinking by default, and reasoning tokens are drawn from
            # the same max_tokens budget as the answer, so a small budget yields
            # HTTP 200 with empty content. Callers that want chain-of-thought must
            # opt in explicitly.
            "thinking": {"type": "disabled"},
            **kwargs,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return self._chat_completion(payload, timeout)

    def _chat_completion(self, payload: Dict[str, Any], timeout: int) -> str:
        """Handle non-streaming chat completion"""

        def request_func():
            return requests.post(
                self.endpoint,
                json=payload,
                headers=self.default_headers,
                timeout=timeout,
            )

        response = self._make_request_with_retry(
            request_func, max_retries=self.max_retries
        )

        response_data = response.json()
        self.last_response = response_data
        self._update_token_usage(response_data)

        try:
            choice = response_data["choices"][0]
            content = choice["message"].get("content")
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError) as e:
            raise ValueError(f"Failed to extract content from response: {str(e)}")

        # A truncated answer must not be returned as if it were complete: callers
        # cannot tell a half-written document from a whole one.
        if finish_reason == "length":
            usage = response_data.get("usage", {})
            reasoning_tokens = usage.get("completion_tokens_details", {}).get(
                "reasoning_tokens", 0
            )
            raise ValueError(
                f"Response truncated by max_tokens "
                f"(completion_tokens={usage.get('completion_tokens')}, "
                f"reasoning_tokens={reasoning_tokens}, "
                f"content_chars={len(content or '')}): raise max_tokens"
            )

        if not content or len(content.strip()) < 1:
            raise ValueError(
                f"Empty response content (finish_reason={finish_reason})"
            )

        return content

    def embed(
        self,
        text: Union[str, List[str]],
        model: Optional[str] = None,
        **kwargs,
    ) -> List[List[float]]:
        """
        Generate embeddings using DeepSeek

        Note: DeepSeek doesn't have a dedicated embedding endpoint,
        so this uses chat completion as a workaround.
        """
        if isinstance(text, str):
            text = [text]

        embeddings = []
        for t in text:
            # Use chat completion to generate embeddings-like output
            messages = [
                {
                    "role": "system",
                    "content": "You are an embedding generator. Output a numerical representation of the text.",
                },
                {
                    "role": "user",
                    "content": f"Generate a numerical representation for: {t}",
                },
            ]

            embedding_text = self.chat(
                messages=messages,
                model=model or self.default_model,
                temperature=0.1,
                max_tokens=100,
            )

            # Convert text to numerical representation (simplified)
            embedding = [float(ord(c)) / 1000.0 for c in embedding_text[:512]]
            embeddings.append(embedding)

        return embeddings

    def get_models(self) -> List[Dict[str, Any]]:
        """Get list of available models from DeepSeek"""
        # DeepSeek doesn't have a public models endpoint, return default list
        return [
            {
                "id": "deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "description": "DeepSeek's fast and cost-effective model (284B/13B active)",
            },
            {
                "id": "deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "description": "DeepSeek's flagship model (1.6T/49B active)",
            },
        ]

    def check_connection(self) -> bool:
        """Check if DeepSeek connection is working"""
        try:
            # Test with a simple request
            messages = [{"role": "user", "content": "Hello"}]
            response = self.chat(
                messages=messages,
                max_tokens=10,
                temperature=0.1,
            )

            if response and isinstance(response, str) and response.strip():
                print("✅ DeepSeek API connection is valid.")
                print(f"✅ Model '{self.default_model}' is available.")
                return True
            else:
                print("❌ Error: DeepSeek API returned an empty or invalid response.")
                return False

        except Exception as e:
            print("❌ Error: DeepSeek API connection failed.")
            print(f"Error details: {e}")
            return False
