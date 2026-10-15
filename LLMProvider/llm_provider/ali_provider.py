# llm/ali_provider.py
import requests
import json
from typing import List, Dict, Any, Optional, Union, Iterator
from .base_provider import BaseLLMProvider


class AliProvider(BaseLLMProvider):
    """Ali (DashScope) LLM provider implementation with embedding support"""

    def __init__(
        self,
        api_key: str,
        default_model: str = "qwen-plus",
        timeout: int = 60,
        max_retries: int = 3,
    ):
        super().__init__(api_key, default_model, timeout)
        self.chat_endpoint = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
        self.embedding_endpoint = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
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
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        """
        Send chat completion request to Ali DashScope

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Model identifier (uses default if None)
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            **kwargs: Additional Ali-specific parameters

        Returns:
            Response text or stream iterator

        Raises:
            Exception: If request fails after retries
        """
        model = model or self.default_model

        # Convert messages to Ali format
        ali_messages = []
        for msg in messages:
            ali_messages.append({"role": msg["role"], "content": msg["content"]})

        payload = {
            "model": model,
            "input": {"messages": ali_messages},
            "parameters": {"temperature": temperature, **kwargs},
        }

        if max_tokens is not None:
            payload["parameters"]["max_tokens"] = max_tokens

        if stream:
            payload["parameters"]["stream"] = True
            return self._chat_stream(payload)
        else:
            return self._chat_completion(payload)

    def _chat_completion(self, payload: Dict[str, Any]) -> str:
        """Handle non-streaming chat completion"""

        def request_func():
            return requests.post(
                self.chat_endpoint,
                json=payload,
                headers=self.default_headers,
                timeout=self.timeout,
            )

        response = self._make_request_with_retry(
            request_func, max_retries=self.max_retries
        )

        response_data = response.json()

        # Extract content from Ali response format
        try:
            content = response_data["output"]["choices"][0]["message"]["content"]
            if not content or len(content.strip()) < 1:
                raise ValueError("Empty response content")
            return content
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(f"Failed to extract content from response: {str(e)}")

    def _chat_stream(self, payload: Dict[str, Any]) -> Iterator[str]:
        """Handle streaming chat completion"""

        def request_func():
            return requests.post(
                self.chat_endpoint,
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
                        if "output" in chunk_data and chunk_data["output"]["choices"]:
                            delta = chunk_data["output"]["choices"][0].get(
                                "message", {}
                            )
                            if "content" in delta:
                                yield delta["content"]
                    except json.JSONDecodeError:
                        continue

    def embed(
        self,
        text: Union[str, List[str]],
        model: Optional[str] = "text-embedding-v4",
        **kwargs,
    ) -> List[List[float]]:
        """
        Generate embeddings using Ali DashScope with batch support.

        Args:
            text: Input text or list of texts
            model: Embedding model identifier (default: text-embedding-v4)
            **kwargs: Additional parameters; supports `max_batch_size` for batching

        Returns:
            List of embedding vectors (order matches input)
        """
        # Normalize input to list while preserving type behavior
        if isinstance(text, str):
            texts: List[str] = [text]
        else:
            texts = list(text)

        # Batch size can be overridden by caller; default to 32
        max_batch_size = int(kwargs.pop("max_batch_size", 32)) if texts else 32

        embeddings: List[List[float]] = []

        # Process in batches to reduce HTTP requests and latency
        for i in range(0, len(texts), max_batch_size):
            batch = texts[i : i + max_batch_size]
            payload = {"model": model, "input": {"texts": batch}}

            def request_func():
                return requests.post(
                    self.embedding_endpoint,
                    json=payload,
                    headers=self.default_headers,
                    timeout=self.timeout,
                )

            response = self._make_request_with_retry(
                request_func, max_retries=self.max_retries
            )
            response_data = response.json()

            # Extract embeddings; API returns one embedding per input text in order
            try:
                emb_list = response_data["output"]["embeddings"]
                for item in emb_list:
                    embeddings.append(item["embedding"])
            except (KeyError, IndexError, TypeError) as e:
                raise ValueError(
                    f"Failed to extract embeddings from response: {str(e)} | data={response_data}"
                )

        return embeddings

    def get_models(self) -> List[Dict[str, Any]]:
        """Get list of available models from Ali DashScope"""
        # Ali doesn't have a public models endpoint, return default list
        return [
            {
                "id": "qwen-plus",
                "name": "Qwen Plus",
                "description": "Ali's general-purpose chat model",
            },
            {
                "id": "qwen-max",
                "name": "Qwen Max",
                "description": "Ali's most powerful chat model",
            },
            {
                "id": "text-embedding-v4",
                "name": "Text Embedding V2",
                "description": "Ali's text embedding model",
            },
        ]

    def check_connection(self) -> bool:
        """Check if Ali DashScope connection is working"""
        try:
            # Test with a simple embedding request
            embeddings = self.embed(text="Test connection", model="text-embedding-v4")
            return len(embeddings) > 0 and len(embeddings[0]) > 0
        except Exception:
            return False
