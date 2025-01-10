import json
import pytest
from unittest.mock import patch, MagicMock

from app import (
    app,
    generate_query_with_ollama,
    validate_query_config,
    search_books_with_openlibrary,
    refine_response_with_ollama
)


@pytest.fixture
def client():
    """
    Pytest fixture: provides a Flask test client within an app context.
    """
    app.testing = True
    with app.test_client() as client:
        with app.app_context():
            yield client


# ------------------------------------------------------------------------------
# 1. Test profanity handling
# ------------------------------------------------------------------------------
def test_profanity_message(client):
    """
    If the query contains profanity, we expect a single moderation chunk (status 200).
    """
    response = client.post(
        "/search-books",
        data=json.dumps({"query": "This is a shitty query"}),
        content_type="application/json",
    )

    # Get all data as bytes and decode once
    response_data = response.get_data(as_text=True).strip()

    assert response.status_code == 200
    assert "The Book Search service is moderated" in response_data


# ------------------------------------------------------------------------------
# 2. Test invalid input handling
# ------------------------------------------------------------------------------
def test_invalid_input_empty_string(client):
    """
    Passing an empty string for 'query' should return 400.
    """
    response = client.post(
        "/search-books",
        data=json.dumps({"query": ""}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert b"Invalid input. 'query' must be a non-empty string." in response.data


def test_invalid_input_no_query_key(client):
    """
    Missing 'query' key should return 400.
    """
    response = client.post(
        "/search-books",
        data=json.dumps({"wrong_key": "hello"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert b"Invalid input. 'query' must be a non-empty string." in response.data


# ------------------------------------------------------------------------------
# 3. Test generate_query_with_ollama (mocked)
# ------------------------------------------------------------------------------
@patch("requests.post")
def test_generate_query_with_ollama_success(mock_post):
    """
    When requests.post succeeds, generate_query_with_ollama should return the parsed dict.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "response": "{'query_type': 'q', 'query_value': 'test', 'limit': '3'}"
    }
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = generate_query_with_ollama("some query")
    assert result == {"query_type": "q", "query_value": "test", "limit": "3"}
    mock_post.assert_called_once()


@patch("requests.post")
def test_generate_query_with_ollama_error(mock_post):
    """
    If requests.post fails, ensure we get back a dict with an 'error' key.
    """
    mock_post.side_effect = Exception("Some network error")

    result = generate_query_with_ollama("some query")
    assert "error" in result
    assert "Some network error" in result["error"]


# ------------------------------------------------------------------------------
# 4. Test validate_query_config
# ------------------------------------------------------------------------------
def test_validate_query_config_success():
    """
    If required keys are present, validate_query_config is True.
    """
    query_config = {
        "query_type": "q",
        "query_value": "python",
        "limit": "3",
    }
    assert validate_query_config(query_config)


def test_validate_query_config_failure():
    """
    Missing a required key => False.
    """
    query_config = {
        "limit": "3"  # missing 'query_type' and 'query_value'
    }
    assert not validate_query_config(query_config)


# ------------------------------------------------------------------------------
# 5. Test search_books_with_openlibrary (mocked)
# ------------------------------------------------------------------------------
@patch("requests.get")
def test_search_books_with_openlibrary_success(mock_get):
    """
    When requests.get succeeds, search_books_with_openlibrary returns JSON dict.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {"docs": [{"title": "Book A"}, {"title": "Book B"}]}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    query_config = {"query_type": "q", "query_value": "python", "limit": "3"}
    result = search_books_with_openlibrary(query_config)
    assert "docs" in result
    assert len(result["docs"]) == 2
    mock_get.assert_called_once()


@patch("requests.get")
def test_search_books_with_openlibrary_error(mock_get):
    """
    If requests.get raises an exception, ensure the function returns {'error': ...}.
    """
    mock_get.side_effect = Exception("Open Library is down")

    query_config = {"query_type": "q", "query_value": "python", "limit": "3"}
    result = search_books_with_openlibrary(query_config)
    assert "error" in result
    assert "Open Library is down" in result["error"]


# ------------------------------------------------------------------------------
# 6. Test refine_response_with_ollama (mocked streaming)
# ------------------------------------------------------------------------------
@patch("requests.post")
def test_refine_response_with_ollama_success(mock_post, client):
    """
    If refine_response_with_ollama returns a streamed response, we verify it yields chunks.
    We mock requests.post to provide chunked bytes.
    """
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = [b"Chunk1", b"Chunk2"]
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    books = {"docs": [{"title": "Book A", "author_name": ["Author A"]}]}

    # Must run inside an app context to allow flask.Response usage
    with client.application.app_context():
        response = refine_response_with_ollama("User query", books)

    # Collect all streamed data as bytes in one go
    data = response.get_data()
    # data might be b"Chunk1\nChunk2\n"
    chunks = data.decode("utf-8").split("\n")
    assert "Chunk1" in chunks
    assert "Chunk2" in chunks


@patch("requests.post")
def test_refine_response_with_ollama_error(mock_post, client):
    """
    If requests.post fails, refine_response_with_ollama should return
    jsonify(...), 500. We wrap it in an app context for jsonify() usage.
    """
    mock_post.side_effect = Exception("Refinement error")
    books = {"docs": [{"title": "Book A", "author_name": ["Author A"]}]}

    with client.application.app_context():
        response = refine_response_with_ollama("User query", books)

    # The function returns (jsonify({error: ...}), 500) on error
    assert response[1] == 500
    assert "error" in response[0].json
    assert "Refinement error" in response[0].json["error"]

