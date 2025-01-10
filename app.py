from flask import Flask, request, jsonify, Response
from better_profanity import profanity
import requests
import json  # <--- Ensure we import json

app = Flask(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OPEN_LIBRARY_URL = "https://openlibrary.org/search.json"
profanity.load_censor_words()

@app.route("/search-books", methods=["POST"])
def search_books():
    """Main endpoint to search for books."""
    try:
        data = request.get_json()

        if not data or "query" not in data or not isinstance(data["query"], str) or not data["query"].strip():
            return jsonify({"error": "Invalid input. 'query' must be a non-empty string."}), 400

        user_query = data["query"].strip()

        if profanity.contains_profanity(user_query):
            return respond_with_profanity_message()

        # Generate query with Ollama
        query_config = generate_query_with_ollama(user_query)
        if isinstance(query_config, dict) and "error" in query_config:
            return jsonify(query_config), 500

        # Validate required keys in query_config
        if not validate_query_config(query_config):
            query_config = generate_query_with_ollama(user_query)
            if isinstance(query_config, dict) and "error" in query_config:
                logger.error(f"Ollama error on retry: {query_config['error']}")
                return jsonify(query_config), 500

        # Search books with Open Library
        books_data = search_books_with_openlibrary(query_config)
        if isinstance(books_data, dict) and "error" in books_data:
            return jsonify(books_data), 500


        # Refine response with Ollama and stream to client
        refined_response = refine_response_with_ollama(user_query, books_data)
        if isinstance(refined_response, dict) and "error" in refined_response:
            return jsonify(refined_response), 500

        return refined_response  # Streamed response

    except Exception as e:
        return jsonify({"error": "An unexpected error occurred."}), 500


def respond_with_profanity_message():
    def stream_chunks():
        chunk = {"response": "The Book Search service is moderated, and does now allow for profanity"}
        yield json.dumps(chunk) + "\n"

    return Response(stream_chunks(), content_type="application/json")

def generate_query_with_ollama(user_query):
    """Generate a query using Ollama."""
    try:
        payload = {
            "model": "llama3.2",
            "prompt": (
                f"Based on the user's query: '{user_query}', "
                "construct a JSON object to query Open Library. Extract up to 6 essential keywords. "
                "limit by default is 3 unless the user asks for a different amount of results\n"
                "options for query_type are 'q', 'author', 'title'\n"
                "The format should be:\n"
                "{\n"
                "  'query_type': 'q',\n"
                "  'query_value': 'keywords',\n"
                "  'limit': '3',\n"
                "}\n"
            ),
            "format": "json",
            "stream": False
        }
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        data = response.json()
        return eval(data.get("response", {}))  # Convert response string to dict
    except Exception as e:
        return {"error": f"Ollama generation failed: {str(e)}"}


def validate_query_config(query_config):
    """Validate that query_config contains required keys."""
    required_keys = ["query_type", "query_value"]
    return all(key in query_config for key in required_keys)


def search_books_with_openlibrary(query_config):
    """Search Open Library."""
    try:
        query_type = query_config.get("query_type", "q")
        query_value = query_config.get("query_value", "")
        limit = query_config.get("limit", "3")
        response = requests.get(OPEN_LIBRARY_URL, params={query_type: query_value, limit: limit})
        response.raise_for_status()
        books = response.json()
        return books
    except Exception as e:
        return {"error": f"Open Library API failed: {str(e)}"}


def refine_response_with_ollama(user_query, books):
    """Generate intro, outro, and refine response using Ollama."""
    try:
        # Construct the book details as a string
        book_details = "\n".join(
            [
                f"\u2022 '{book.get('title', 'Unknown Title')}' by {', '.join(book.get('author_name', ['Unknown Author']))}"
                for book in books.get("docs", [])[:3]
            ]
        )
        payload = {
            "model": "llama3.2",
            "prompt": (
                f"The user asked: '{user_query}'. Based on the following books:\n{book_details}\n"
                "Write an engaging intro, include the book details as bullets, and a happy outro. Use plain text."
            ),
            "stream": True
        }

        response = requests.post(OLLAMA_URL, json=payload, stream=True)
        response.raise_for_status()

        # Generator to yield chunks
        def stream_chunks():
            for chunk in response.iter_lines():
                if chunk:
                    content = chunk.decode("utf-8")
                    yield content + "\n"  # Add newline to separate chunks in the response

        return Response(stream_chunks(), content_type="text/plain")  # Stream the response

    except Exception as e:
        return jsonify({"error": f"Refinement failed: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)

