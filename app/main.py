"""A small streaming chat client for a local Ollama server."""

import json
import sys

import requests

from chunker import chunk_document_pages
from document_loader import load_document, load_document_pages
from document_importer import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DocumentImportError,
    import_document_chunks,
)
from embeddings import (
    EMBEDDING_MODEL,
    EmbeddingError,
    generate_embeddings,
    generate_query_embedding,
)
from knowledge_library import delete_document, list_documents, reindex_document
from language_preferences import (
    build_language_instruction,
    get_language_preference,
)
from personas import (
    build_persona_instruction,
    get_active_persona,
    list_personas,
    switch_persona,
)
from rag import answer_question, format_source_label
from semantic_search import semantic_search
from vector_store import VectorStore, VectorStoreError


OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_HEALTH_URL = f"{OLLAMA_BASE_URL}/api/version"
MODEL_NAME = "qwen3.5:4b"


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def stream_response(user_message, persona=None, language=None):
    """Send one message to local Ollama and print its streamed response."""
    selected_persona = persona or get_active_persona()
    selected_language = language or get_language_preference()
    request_data = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": build_persona_instruction(selected_persona),
            },
            {
                "role": "system",
                "content": build_language_instruction(
                    selected_language,
                    user_message,
                ),
            },
            {"role": "user", "content": user_message},
        ],
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
                        print(
                            f"{selected_persona['display_name']}: ",
                            end="",
                            flush=True,
                        )
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


def display_knowledge_library(documents):
    """Print stored documents and their local metadata."""
    print("\nKnowledge Library:")

    if not documents:
        print("The local knowledge base is empty.")
        return

    for position, document in enumerate(documents, start=1):
        file_type = document["file_type"].lstrip(".").upper() or "UNKNOWN"
        print(f"\n[{position}] {document['filename']}")
        print(f"Type: {file_type}")
        print(f"Chunks: {document['chunk_count']}")

        if document["page_count"] is not None:
            print(f"Pages with text: {document['page_count']}")

        if document["source_path"]:
            print(f"Source: {document['source_path']}")


def choose_library_document(documents):
    """Ask the user to select one displayed knowledge document."""
    selection = input("Choose a document number: ").strip()

    try:
        selection_index = int(selection) - 1
    except ValueError:
        print("Please enter a valid document number.")
        return None

    if selection_index < 0 or selection_index >= len(documents):
        print("Please choose a number shown in the knowledge library.")
        return None

    return documents[selection_index]


def display_persona_information(persona):
    """Show the active persona's user-facing configuration."""
    print(f"\nCurrent persona: {persona['display_name']}")
    print(f"Style: {persona['style_description']}")
    print(f"Preferred response length: {persona['preferred_response_length']}")
    print(f"Greeting: {persona['greeting']}")


def choose_persona(personas):
    """Ask the user to select one available persona."""
    selection = input("Choose a persona number: ").strip()

    try:
        selection_index = int(selection) - 1
    except ValueError:
        print("Please enter a valid persona number.")
        return None

    if selection_index < 0 or selection_index >= len(personas):
        print("Please choose a number shown in the persona list.")
        return None

    return personas[selection_index]


def main():
    """Let the user use local chat, documents, and the knowledge base."""
    active_persona = get_active_persona(reload=True)
    active_language = get_language_preference(reload=True)
    print(f"Active persona: {active_persona['display_name']}")
    print(f"Response language: {active_language['display_name']}")
    print(active_persona["greeting"])
    print()

    try:
        health_response = requests.get(OLLAMA_HEALTH_URL, timeout=3)
        health_response.raise_for_status()
    except requests.exceptions.RequestException:
        print("Ollama is unavailable. Please start Ollama and try again.")
        return

    print("Choose a mode:")
    print("1. Normal chat")
    print("2. Load and summarize a local document")
    print("3. Semantic search inside a local document")
    print("4. Import document into knowledge base")
    print("5. Search knowledge base")
    print("6. Ask knowledge base (RAG)")
    print("7. List knowledge documents")
    print("8. Delete document from knowledge base")
    print("9. Re-index document")
    print("10. Switch persona / show persona information")
    choice = input("Enter a mode from 1 to 10: ").strip()

    if choice == "1":
        user_message = input("You: ").strip()

        if not user_message:
            print("Please enter a message.")
            return
    elif choice == "2":
        print(
            "Local processing: your document stays on this computer and is sent "
            "only to your local Ollama server."
        )
        file_path = input("Document path (.txt, .md, or .pdf): ").strip().strip('"')

        if not file_path:
            print("Please enter a document path.")
            return

        try:
            document_text = load_document(file_path)
        except (FileNotFoundError, ValueError, OSError) as error:
            print(f"Could not load document: {error}")
            return

        if not document_text.strip():
            print("Could not summarize the document because it is empty.")
            return

        print(f"Document loaded. Summarizing locally with {MODEL_NAME}...")
        user_message = (
            "Summarize the following document clearly and concisely. "
            "Include its main ideas and important details.\n\n"
            f"Document:\n{document_text}"
        )
    elif choice == "3":
        print(
            "Local processing: document text and embeddings stay on this "
            "computer and are sent only to your local Ollama server."
        )
        file_path = input("Document path (.txt, .md, or .pdf): ").strip().strip('"')

        if not file_path:
            print("Please enter a document path.")
            return

        try:
            document_pages = load_document_pages(file_path)
        except (FileNotFoundError, ValueError, OSError) as error:
            print(f"Could not load document: {error}")
            return

        chunks, _ = chunk_document_pages(document_pages)

        if not chunks:
            print("Could not search the document because it is empty.")
            return

        print(
            f"Created {len(chunks)} local chunk(s). Generating embeddings "
            f"locally with {EMBEDDING_MODEL}..."
        )

        try:
            chunk_embeddings = generate_embeddings(chunks)
        except (EmbeddingError, ValueError) as error:
            print(f"Could not generate embeddings: {error}")
            return

        query = input("Search query: ").strip()

        if not query:
            print("Please enter a search query.")
            return

        try:
            results = semantic_search(query, chunks, chunk_embeddings)
        except (EmbeddingError, ValueError) as error:
            print(f"Could not search the document: {error}")
            return

        print("\nTop relevant chunks:")

        for position, (chunk, score) in enumerate(results, start=1):
            print(f"\n[{position}] Similarity: {score:.4f}")
            print(f'"{chunk}"')

        return
    elif choice == "4":
        print(
            "Local processing: the document, embeddings, and knowledge base "
            "stay on this computer."
        )
        file_path = input("Document path (.txt, .md, or .pdf): ").strip().strip('"')

        if not file_path:
            print("Please enter a document path.")
            return

        try:
            document_pages = load_document_pages(file_path)
        except (FileNotFoundError, ValueError, OSError) as error:
            print(f"Could not load document: {error}")
            return

        chunks, chunk_metadata = chunk_document_pages(document_pages)

        if not chunks:
            print("Could not import the document because it is empty.")
            return

        print(f"Importing {file_path}...")
        print(f"Created {len(chunks)} chunks.")
        print(
            f"Generating embeddings locally with {EMBEDDING_MODEL} "
            f"in batches of {DEFAULT_EMBEDDING_BATCH_SIZE}..."
        )

        try:
            stored_count = import_document_chunks(
                file_path,
                chunks,
                chunk_metadata,
            )
        except (
            DocumentImportError,
            EmbeddingError,
            VectorStoreError,
            ValueError,
        ) as error:
            print(f"Could not import document: {error}")
            print("Successfully stored batches are kept. Run Mode 4 again to resume.")
            return

        print(f"Stored {stored_count} chunks in local knowledge base.")
        print("Import complete.")
        return
    elif choice == "5":
        print(
            "Local processing: the query embedding and knowledge search stay "
            "on this computer."
        )
        query = input("Search query: ").strip()

        if not query:
            print("Please enter a search query.")
            return

        try:
            vector_store = VectorStore()

            if vector_store.count() == 0:
                print("The local knowledge base is empty. Import a document first.")
                return

            query_embedding = generate_query_embedding(query)
            results = vector_store.search(query_embedding, top_k=3)
        except (EmbeddingError, VectorStoreError, ValueError) as error:
            print(f"Could not search knowledge base: {error}")
            return

        print("\nTop knowledge base results:")

        for position, result in enumerate(results, start=1):
            metadata = result["metadata"]
            distance = result["distance"]
            similarity = 1.0 - distance
            print(
                f"\n[{position}] Similarity: {similarity:.4f} "
                f"(distance: {distance:.4f})"
            )
            print(f"Source: {format_source_label(metadata)}")
            print(f'"{result["text"]}"')

        return
    elif choice == "6":
        print(
            "Local processing: retrieval, context, and AI generation stay on "
            "this computer."
        )
        question = input("Ask your knowledge base: ").strip()

        if not question:
            print("Please enter a question.")
            return

        print("Searching local knowledge...")

        try:
            answer_question(question)
        except (EmbeddingError, VectorStoreError, ValueError) as error:
            print(f"Could not answer from the knowledge base: {error}")

        return
    elif choice == "7":
        print("Local processing: reading the private Chroma knowledge library.")

        try:
            documents = list_documents()
        except VectorStoreError as error:
            print(f"Could not list knowledge documents: {error}")
            return

        display_knowledge_library(documents)
        return
    elif choice == "8":
        print("Local processing: changes apply only to the local knowledge base.")

        try:
            documents = list_documents()
        except VectorStoreError as error:
            print(f"Could not list knowledge documents: {error}")
            return

        display_knowledge_library(documents)

        if not documents:
            return

        document = choose_library_document(documents)

        if document is None:
            return

        print(
            f"\nDelete `{document['filename']}` and all of its stored knowledge?"
        )
        confirmation = input("Type YES to confirm: ").strip()

        if confirmation != "YES":
            print("Deletion cancelled. No knowledge was removed.")
            return

        try:
            deleted_count = delete_document(document["source_id"])
        except (VectorStoreError, ValueError) as error:
            print(f"Could not delete document: {error}")
            return

        print(
            f"Deleted {document['filename']} and {deleted_count} stored chunk(s)."
        )
        return
    elif choice == "9":
        print(
            "Local processing: the source is reloaded, embedded, and replaced "
            "only in the local knowledge base."
        )

        try:
            documents = list_documents()
        except VectorStoreError as error:
            print(f"Could not list knowledge documents: {error}")
            return

        display_knowledge_library(documents)

        if not documents:
            return

        document = choose_library_document(documents)

        if document is None:
            return

        print(f"\nRe-indexing {document['filename']}...")

        try:
            result = reindex_document(document)
        except (
            DocumentImportError,
            EmbeddingError,
            FileNotFoundError,
            OSError,
            VectorStoreError,
            ValueError,
        ) as error:
            print(f"Could not re-index document: {error}")
            return

        print(
            f"Re-index complete. Replaced {result['previous_chunk_count']} old "
            f"chunk(s) with {result['stored_count']} current chunk(s)."
        )
        return
    elif choice == "10":
        display_persona_information(active_persona)
        personas = list_personas()
        print("\nChoose persona:")

        for position, persona in enumerate(personas, start=1):
            active_marker = " (active)" if persona["id"] == active_persona["id"] else ""
            print(f"{position}. {persona['display_name']}{active_marker}")

        selected_persona = choose_persona(personas)

        if selected_persona is None:
            return

        try:
            active_persona = switch_persona(selected_persona["id"])
        except (OSError, ValueError) as error:
            print(f"Could not switch persona: {error}")
            return

        print(f"Active persona changed to {active_persona['display_name']}.")
        print(active_persona["greeting"])
        return
    else:
        print("Invalid choice. Please run the program again and enter 1 to 10.")
        return

    stream_response(
        user_message,
        persona=active_persona,
        language=active_language,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nChat cancelled.")
