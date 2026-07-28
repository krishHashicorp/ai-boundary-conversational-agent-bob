"""
tests/test_watsonx_llm.py
Unit tests for the WatsonX.ai LLM wrapper.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# Set required env vars before importing the module
os.environ.setdefault("WATSONX_API_KEY", "test-key")
os.environ.setdefault("WATSONX_PROJECT_ID", "test-project")
os.environ.setdefault("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
os.environ.setdefault("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct")

from src.agent.watsonx_llm import chat, SYSTEM_PROMPT  # noqa: E402


class TestChat:
    def _make_mock_model(self, response_text: str) -> MagicMock:
        mock_instance = MagicMock()
        mock_instance.chat.return_value = {
            "choices": [{"message": {"content": f"  {response_text}  "}}]
        }
        return mock_instance

    @patch("src.agent.watsonx_llm.ModelInference")
    def test_returns_model_response(self, mock_model_class):
        mock_instance = self._make_mock_model("Hello, I am your assistant.")
        mock_model_class.return_value = mock_instance

        import src.agent.watsonx_llm as llm_module
        llm_module._model = None

        result = chat([{"role": "user", "content": "Hello"}])

        assert result == "Hello, I am your assistant."
        mock_instance.chat.assert_called_once()

    @patch("src.agent.watsonx_llm.ModelInference")
    def test_strips_whitespace_from_response(self, mock_model_class):
        mock_instance = self._make_mock_model("\n\n  trimmed  \n")
        mock_model_class.return_value = mock_instance

        import src.agent.watsonx_llm as llm_module
        llm_module._model = None

        result = chat([{"role": "user", "content": "test"}])
        assert result == "trimmed"

    @patch("src.agent.watsonx_llm.ModelInference")
    def test_prepends_system_message_when_absent(self, mock_model_class):
        mock_instance = self._make_mock_model("ok")
        mock_model_class.return_value = mock_instance

        import src.agent.watsonx_llm as llm_module
        llm_module._model = None

        messages = [{"role": "user", "content": "Hello"}]
        chat(messages)

        call_kwargs = mock_instance.chat.call_args
        sent_messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        assert sent_messages[0]["role"] == "system"
        assert SYSTEM_PROMPT in sent_messages[0]["content"]

    @patch("src.agent.watsonx_llm.ModelInference")
    def test_does_not_double_add_system_message_when_present(self, mock_model_class):
        mock_instance = self._make_mock_model("ok")
        mock_model_class.return_value = mock_instance

        import src.agent.watsonx_llm as llm_module
        llm_module._model = None

        messages = [
            {"role": "system", "content": "Custom system prompt"},
            {"role": "user", "content": "Hello"},
        ]
        chat(messages)

        call_kwargs = mock_instance.chat.call_args
        sent_messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        system_msgs = [m for m in sent_messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "Custom system prompt"
