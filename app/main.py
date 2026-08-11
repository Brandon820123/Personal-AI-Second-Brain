"""A small streaming chat client for a local Ollama server."""

import json

import requests


OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_HEALTH_URL = f"{OLLAMA_BASE_URL}/api/version"
MODEL_NAME = "qwen3.5:4b"


def main():
    """Check Ollama, ask for one message, and stream the reply."""
    try:
        health_response = requests.get(OLLAMA_HEALTH_URL, timeout=3)
        health_response.raise_for_status()
    except requests.exceptions.RequestException:
        print("Ollama is unavailable. Please start Ollama and try again.")
        return

    user_message = input("You: ").strip()

    if not user_message:
        print("Please enter a message.")
        return

    request_data = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": user_message}],
        "stream": True,
        "think": False,
    }

    try:
        with requests.post(
            OLLAMA_CHAT_URL,
            json=request_data,
            stream=True,
            timeout=(5, 300),
        ) as response:
            response.raise_for_status()

            answer_started = False

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue

                try:
                    response_chunk = json.loads(line)
                except json.JSONDecodeError:
                    print("\nOllama returned an unexpected response.")
                    return

                if "error" in response_chunk:
                    print(f"\nOllama error: {response_chunk['error']}")
                    return

                content = response_chunk.get("message", {}).get("content", "")

                if content:
                    if not answer_started:
                        print("Ollama: ", end="", flush=True)
                        answer_started = True

                    print(content, end="", flush=True)

                if response_chunk.get("done"):
                    break

            if answer_started:
                print()
            else:
                print("Ollama returned an empty response.")
    except requests.exceptions.ConnectionError:
        print("Lost connection to Ollama. Please try again.")
        return
    except requests.exceptions.Timeout:
        print("Ollama took too long to respond. Please try again.")
        return
    except requests.exceptions.HTTPError as error:
        print(f"Ollama returned an HTTP error: {error}")
        return
    except requests.exceptions.RequestException as error:
        print(f"Could not send the request to Ollama: {error}")
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nChat cancelled.")
