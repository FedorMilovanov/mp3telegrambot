import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import json

from services.eng_subtitles import _translate_chunk_with_retry

@pytest.mark.asyncio
async def test_translate_chunk_with_retry_json_parsing(monkeypatch):
    """Test that the json parsing correctly strips markdown wrappers from Gemini."""
    
    mock_response = MagicMock()
    mock_response.text = "```json\n{\n  \"1\": \"Привет мир\",\n  \"2\": \"Как дела\"\n}\n```"
    
    mock_models = AsyncMock()
    mock_models.generate_content.return_value = mock_response
    
    mock_aio = MagicMock()
    mock_aio.models = mock_models
    
    mock_client = MagicMock()
    mock_client.aio = mock_aio
    
    # Mock GEMINI_CLIENTS in the module
    monkeypatch.setattr("services.eng_subtitles.GEMINI_CLIENTS", [mock_client])
    
    chunk = [(1, "Hello world"), (2, "How are you")]
    data = await _translate_chunk_with_retry(chunk)
    
    assert data is not None
    assert data["1"] == "Привет мир"
    assert data["2"] == "Как дела"
    mock_models.generate_content.assert_called_once()

@pytest.mark.asyncio
async def test_translate_chunk_with_retry_plain_json(monkeypatch):
    """Test standard JSON without markdown wrapper."""
    mock_response = MagicMock()
    mock_response.text = '{"1": "Привет"}'
    
    mock_models = AsyncMock()
    mock_models.generate_content.return_value = mock_response
    
    mock_client = MagicMock()
    mock_client.aio = MagicMock(models=mock_models)
    
    monkeypatch.setattr("services.eng_subtitles.GEMINI_CLIENTS", [mock_client])
    
    chunk = [(1, "Hello")]
    data = await _translate_chunk_with_retry(chunk)
    
    assert data == {"1": "Привет"}
