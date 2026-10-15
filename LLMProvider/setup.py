from setuptools import setup, find_packages

setup(
    name="llm-provider",
    version="0.1.0",
    description="Unified LLM Provider Interface for multiple AI platforms",
    long_description="""
# LLM Provider

A unified interface for managing multiple LLM providers including OpenRouter, DeepSeek, OpenAI, and more.

## Installation

```bash
pip install -e .
```

## Usage

```python
from llm_provider import ProviderManager

# Get default provider
provider = ProviderManager.get_provider()

# Or specify a provider
provider = ProviderManager.get_provider("openrouter")

# Use the provider
response = provider.chat(
    messages=[{"role": "user", "content": "Hello!"}],
    temperature=0.7,
    max_tokens=100
)
```
""",
    author="David Li",
    author_email="leezhengjiang@126.com",
    packages=find_packages(),
    install_requires=[
        "requests>=2.25.0",
        "python-dotenv>=1.0.0",
        "typing-extensions>=4.0.0; python_version<'3.8'",
    ],
    python_requires=">=3.7",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="llm ai openrouter deepseek openai provider api",
    url="https://github.com/ZJLi2013/niche_ai_tools",
    project_urls={
        "Source": "https://github.com/ZJLi2013/niche_ai_tools/tree/main/agents/modules/LLMProvider",
    },
)
