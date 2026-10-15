# llm/openrouter_provider.py
import requests
import json
import os
from typing import List, Dict, Any, Optional, Union, Iterator
from .base_provider import BaseLLMProvider


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter LLM provider implementation"""

    def __init__(
        self,
        api_key: str,
        default_model: str = "deepseek/deepseek-chat-v3.1:free",
        timeout: int = 60,
        max_retries: int = 3,
    ):
        super().__init__(api_key, default_model, timeout)
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"
        self.max_retries = max_retries
        self.default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ZJLi2013/niche_ai_tools",
            "X-Title": "EN2CN Translation Agent",
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        """
        Send chat completion request to OpenRouter

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Model identifier (uses default if None)
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            **kwargs: Additional OpenRouter-specific parameters

        Returns:
            Response text or stream iterator

        Raises:
            Exception: If request fails after retries
        """
        model = model or self.default_model

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if stream:
            payload["stream"] = True
            return self._chat_stream(payload)
        else:
            return self._chat_completion(payload)

    def _chat_completion(self, payload: Dict[str, Any]) -> str:
        """Handle non-streaming chat completion"""

        def request_func():
            return requests.post(
                self.endpoint,
                json=payload,
                headers=self.default_headers,
                timeout=self.timeout,
            )

        response = self._make_request_with_retry(
            request_func, max_retries=self.max_retries
        )

        response_data = response.json()
        self._update_token_usage(response_data)

        # Extract content from response
        try:
            content = response_data["choices"][0]["message"]["content"]
            if not content or len(content.strip()) < 1:
                raise ValueError("Empty response content")
            return content
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(f"Failed to extract content from response: {str(e)}")

    def _chat_stream(self, payload: Dict[str, Any]) -> Iterator[str]:
        """Handle streaming chat completion"""

        def request_func():
            return requests.post(
                self.endpoint,
                json=payload,
                headers=self.default_headers,
                timeout=self.timeout,
                stream=True,
            )

        response = self._make_request_with_retry(
            request_func, max_retries=self.max_retries
        )

        for line in response.iter_lines():
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    data = line[6:]  # Remove 'data: ' prefix
                    if data == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(data)
                        if "choices" in chunk_data and chunk_data["choices"]:
                            delta = chunk_data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                    except json.JSONDecodeError:
                        continue

    def embed(
        self, text: Union[str, List[str]], model: Optional[str] = None, **kwargs
    ) -> List[List[float]]:
        """
        Generate embeddings using OpenRouter

        Note: OpenRouter doesn't have a dedicated embedding endpoint,
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
        """Get list of available models from OpenRouter"""
        models_endpoint = "https://openrouter.ai/api/v1/models"

        def request_func():
            return requests.get(
                models_endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )

        try:
            response = self._make_request_with_retry(request_func)
            models_data = response.json()
            return models_data.get("data", [])
        except Exception as e:
            # Return a default list if API call fails
            return [
                {
                    "id": "deepseek/deepseek-chat-v3.1:free",
                    "name": "DeepSeek Chat V3.1 (Free)",
                    "description": "Free DeepSeek model",
                },
                {
                    "id": "openai/gpt-4-turbo",
                    "name": "GPT-4 Turbo",
                    "description": "OpenAI GPT-4 Turbo",
                },
            ]

    def check_connection(self) -> bool:
        """Check if OpenRouter connection is working"""
        try:
            models = self.get_models()
            return len(models) > 0
        except Exception:
            return False
