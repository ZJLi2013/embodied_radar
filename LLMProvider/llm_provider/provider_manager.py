# llm/provider_manager.py
from typing import Dict, Any, Optional, List
import os
from dotenv import load_dotenv
from .base_provider import BaseLLMProvider
from .openrouter_provider import OpenRouterProvider
from .deepseek_provider import DeepSeekProvider
from .ali_provider import AliProvider

# Load environment variables from .env file
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)


class ProviderManager:
    """Manager for LLM providers with configuration and factory methods"""

    _providers: Dict[str, BaseLLMProvider] = {}
    _config: Dict[str, Any] = {}

    @classmethod
    def initialize(cls, config: Dict[str, Any] = None):
        """Initialize provider manager with configuration"""
        if config is None:
            config = cls._load_default_config()
        cls._config = config
        cls._providers = {}

    @classmethod
    def _load_default_config(cls) -> Dict[str, Any]:
        """Load default configuration from environment variables"""
        return {
            "providers": {
                "openrouter": {
                    "api_key": os.getenv("OPENROUTER_API_KEY"),
                    "default_model": os.getenv(
                        "OPENROUTER_MODEL", "deepseek/deepseek-chat-v3.1:free"
                    ),
                    "timeout": int(os.getenv("OPENROUTER_TIMEOUT", "60")),
                    "max_retries": int(os.getenv("OPENROUTER_MAX_RETRIES", "3")),
                    "enabled": bool(os.getenv("OPENROUTER_API_KEY")),
                },
                "deepseek": {
                    "api_key": os.getenv("DEEPSEEK_API_KEY"),
                    "default_model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                    "timeout": int(os.getenv("DEEPSEEK_TIMEOUT", "60")),
                    "max_retries": int(os.getenv("DEEPSEEK_MAX_RETRIES", "3")),
                    "enabled": bool(os.getenv("DEEPSEEK_API_KEY")),
                },
                "ali": {
                    "api_key": os.getenv("DASHSCOPE_API_KEY"),
                    "default_model": os.getenv("ALI_MODEL", "qwen-plus"),
                    "timeout": int(os.getenv("ALI_TIMEOUT", "60")),
                    "max_retries": int(os.getenv("ALI_MAX_RETRIES", "3")),
                    "enabled": bool(os.getenv("DASHSCOPE_API_KEY")),
                },
                "openai": {
                    "api_key": os.getenv("OPENAI_API_KEY"),
                    "default_model": os.getenv("OPENAI_MODEL", "gpt-4"),
                    "timeout": int(os.getenv("OPENAI_TIMEOUT", "60")),
                    "max_retries": int(os.getenv("OPENAI_MAX_RETRIES", "3")),
                    "enabled": bool(os.getenv("OPENAI_API_KEY")),
                },
            },
            "default_provider": "openrouter",
        }

    @classmethod
    def get_provider(cls, provider_name: str = None) -> BaseLLMProvider:
        """
        Get LLM provider instance

        Args:
            provider_name: Name of provider (openrouter, openai, etc.)

        Returns:
            LLM provider instance

        Raises:
            ValueError: If provider is not configured or not found
        """
        if not cls._config:
            cls.initialize()

        provider_name = provider_name or cls._config.get(
            "default_provider", "openrouter"
        )

        if provider_name not in cls._providers:
            cls._create_provider(provider_name)

        return cls._providers[provider_name]

    @classmethod
    def _create_provider(cls, provider_name: str):
        """Create provider instance based on configuration"""
        provider_config = cls._config["providers"].get(provider_name)

        if not provider_config:
            raise ValueError(f"Provider '{provider_name}' not found in configuration")

        if not provider_config.get("enabled", False):
            raise ValueError(f"Provider '{provider_name}' is not enabled")

        api_key = provider_config.get("api_key")
        if not api_key:
            raise ValueError(f"API key not configured for provider '{provider_name}'")

        if provider_name == "openrouter":
            cls._providers[provider_name] = OpenRouterProvider(
                api_key=api_key,
                default_model=provider_config.get("default_model"),
                timeout=provider_config.get("timeout", 60),
                max_retries=provider_config.get("max_retries", 3),
            )
        elif provider_name == "deepseek":
            cls._providers[provider_name] = DeepSeekProvider(
                api_key=api_key,
                default_model=provider_config.get("default_model"),
                timeout=provider_config.get("timeout", 60),
                max_retries=provider_config.get("max_retries", 3),
            )
        elif provider_name == "ali":
            cls._providers[provider_name] = AliProvider(
                api_key=api_key,
                default_model=provider_config.get("default_model"),
                timeout=provider_config.get("timeout", 60),
                max_retries=provider_config.get("max_retries", 3),
            )
        elif provider_name == "openai":
            # TODO: Implement OpenAI provider
            raise NotImplementedError(f"Provider '{provider_name}' not yet implemented")
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    @classmethod
    def list_providers(cls) -> List[str]:
        """Get list of available provider names"""
        if not cls._config:
            cls.initialize()
        return list(cls._config["providers"].keys())

    @classmethod
    def get_available_providers(cls) -> List[str]:
        """Get list of enabled and configured providers"""
        if not cls._config:
            cls.initialize()

        available = []
        for name, config in cls._config["providers"].items():
            if config.get("enabled", False) and config.get("api_key"):
                available.append(name)
        return available

    @classmethod
    def get_provider_info(cls, provider_name: str) -> Dict[str, Any]:
        """Get configuration information for a provider"""
        if not cls._config:
            cls.initialize()

        provider_config = cls._config["providers"].get(provider_name, {})
        return {
            "name": provider_name,
            "enabled": provider_config.get("enabled", False),
            "configured": bool(provider_config.get("api_key")),
            "default_model": provider_config.get("default_model"),
            "timeout": provider_config.get("timeout", 60),
            "max_retries": provider_config.get("max_retries", 3),
        }

    @classmethod
    def chat(
        cls, messages: List[Dict[str, str]], provider: str = None, **kwargs
    ) -> str:
        """
        Convenience method for chat completion using default or specified provider

        Args:
            messages: List of message dictionaries
            provider: Provider name (uses default if None)
            **kwargs: Additional parameters for chat method

        Returns:
            Response text
        """
        provider_instance = cls.get_provider(provider)
        return provider_instance.chat(messages, **kwargs)

    @classmethod
    def check_connections(cls) -> Dict[str, bool]:
        """Check connection status for all available providers"""
        if not cls._config:
            cls.initialize()

        status = {}
        for provider_name in cls.get_available_providers():
            try:
                provider = cls.get_provider(provider_name)
                status[provider_name] = provider.check_connection()
            except Exception:
                status[provider_name] = False
        return status


# Initialize on module import
ProviderManager.initialize()
