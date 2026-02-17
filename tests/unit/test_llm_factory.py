"""Tests for the LLM provider factory."""

import os
import pytest
from unittest.mock import patch, Mock


class TestGetLangchainLlm:
    """Tests for get_langchain_llm()."""

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4.1-nano"})
    @patch("langchain_openai.ChatOpenAI")
    def test_returns_openai_by_default(self, mock_chat_openai):
        from agent.llm_factory import get_langchain_llm

        mock_chat_openai.return_value = Mock()
        result = get_langchain_llm()

        mock_chat_openai.assert_called_once()
        call_kwargs = mock_chat_openai.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4.1-nano"

    @patch.dict(
        os.environ,
        {
            "LLM_PROVIDER": "bedrock",
            "BEDROCK_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
        },
    )
    @patch("langchain_aws.ChatBedrockConverse")
    def test_returns_bedrock_when_configured(self, mock_bedrock):
        from agent.llm_factory import get_langchain_llm

        mock_bedrock.return_value = Mock()
        result = get_langchain_llm()

        mock_bedrock.assert_called_once()
        call_kwargs = mock_bedrock.call_args
        assert call_kwargs.kwargs["model"] == "anthropic.claude-3-haiku-20240307-v1:0"


class TestChatCompletion:
    """Tests for chat_completion()."""

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"})
    @patch("openai.OpenAI")
    def test_openai_chat_completion(self, mock_openai_cls):
        from agent.llm_factory import chat_completion

        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "test response"
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
        )

        assert result == "test response"
        mock_client.chat.completions.create.assert_called_once()

    @patch.dict(os.environ, {"LLM_PROVIDER": "bedrock", "AWS_REGION": "eu-west-1"})
    @patch("boto3.client")
    def test_bedrock_chat_completion(self, mock_boto3_client):
        from agent.llm_factory import chat_completion

        mock_client = Mock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "bedrock response"}]}}
        }
        mock_boto3_client.return_value = mock_client

        result = chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
        )

        assert result == "bedrock response"
        mock_client.converse.assert_called_once()

    @patch.dict(os.environ, {"LLM_PROVIDER": "bedrock", "AWS_REGION": "eu-west-1"})
    @patch("boto3.client")
    def test_bedrock_json_mode_appends_instruction(self, mock_boto3_client):
        from agent.llm_factory import chat_completion

        mock_client = Mock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"key": "value"}'}]}}
        }
        mock_boto3_client.return_value = mock_client

        chat_completion(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "give json"},
            ],
            json_response=True,
        )

        call_kwargs = mock_client.converse.call_args.kwargs
        system_text = call_kwargs["system"][0]["text"]
        assert "MUST respond with valid JSON" in system_text
        assert system_text.startswith("You are helpful.")


class TestPingLlm:
    """Tests for ping_llm()."""

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"})
    @patch("openai.OpenAI")
    def test_ping_openai(self, mock_openai_cls):
        from agent.llm_factory import ping_llm

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = Mock()
        mock_openai_cls.return_value = mock_client

        result = ping_llm()
        assert "OpenAI" in result

    @patch.dict(os.environ, {"LLM_PROVIDER": "bedrock", "AWS_REGION": "eu-west-1"})
    @patch("boto3.client")
    def test_ping_bedrock(self, mock_boto3_client):
        from agent.llm_factory import ping_llm

        mock_client = Mock()
        mock_client.converse.return_value = {}
        mock_boto3_client.return_value = mock_client

        result = ping_llm()
        assert "Bedrock" in result


class TestGetCircuitBreakerExceptionClass:
    """Tests for get_circuit_breaker_exception_class()."""

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai"})
    def test_returns_openai_error(self):
        from agent.llm_factory import get_circuit_breaker_exception_class
        from openai import OpenAIError

        result = get_circuit_breaker_exception_class()
        assert result is OpenAIError

    @patch.dict(os.environ, {"LLM_PROVIDER": "bedrock"})
    def test_returns_client_error(self):
        from agent.llm_factory import get_circuit_breaker_exception_class
        from botocore.exceptions import ClientError

        result = get_circuit_breaker_exception_class()
        assert result is ClientError
