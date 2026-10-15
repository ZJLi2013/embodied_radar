import unittest
import os
from dotenv import load_dotenv
from llm_provider.deepseek_provider import DeepSeekProvider


load_dotenv("./llm_provider/.env")


class TestDeepSeekProvider(unittest.TestCase):
    def setUp(self):
        """Set up the test case."""
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            self.skipTest("DEEPSEEK_API_KEY is not set in the environment variables.")

        self.provider = DeepSeekProvider(api_key=self.api_key)

    def test_chat_completion(self):
        """Test the chat completion functionality."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, what is the capital of France?"},
        ]

        try:
            response = self.provider.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=50,
            )
            self.assertIsInstance(response, str)
            self.assertIn("Paris", response)
            print(f"DeepSeek Chat Response: {response}")

        except Exception as e:
            self.fail(f"Chat completion failed with an exception: {e}")

    def test_check_connection(self):
        """Test the connection check."""
        is_connected = self.provider.check_connection()
        self.assertTrue(is_connected)


if __name__ == "__main__":
    unittest.main()
