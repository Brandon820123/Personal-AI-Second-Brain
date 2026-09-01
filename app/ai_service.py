"""Reusable local AI operations for desktop and future interfaces."""

import json

import requests

try:
    from .chunker import chunk_document_pages
    from .document_importer import import_document_chunks
    from .document_loader import load_document_pages
    from .embeddings import generate_query_embedding
    from .knowledge_library import reindex_document
    from .language_preferences import (
        build_language_instruction,
        get_language_preference,
    )
    from .personas import build_persona_instruction, get_active_persona
    from .rag import (
        DEFAULT_TOP_K,
        MIN_RELEVANCE_SCORE,
        build_context,
        build_rag_system_messages,
        format_sources,
        select_relevant_results,
    )
    from .vector_store import VectorStore
except ImportError:
    from chunker import chunk_document_pages
    from document_importer import import_document_chunks
    from document_loader import load_document_pages
    from embeddings import generate_query_embedding
    from knowledge_library import reindex_document
    from language_preferences import (
        build_language_instruction,
        get_language_preference,
    )
    from personas import build_persona_instruction, get_active_persona
    from rag import (
        DEFAULT_TOP_K,
        MIN_RELEVANCE_SCORE,
        build_context,
        build_rag_system_messages,
        format_sources,
        select_relevant_results,
    )
    from vector_store import VectorStore


OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_HEALTH_URL = f"{OLLAMA_BASE_URL}/api/version"
CHAT_MODEL = "qwen3.5:4b"


class AIServiceError(RuntimeError):
    """Report a concise local-service error suitable for a user interface."""


def check_ollama():
    """Confirm that the local Ollama server is reachable."""
    try:
        response = requests.get(OLLAMA_HEALTH_URL, timeout=3)
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        raise AIServiceError("无法连接本地 Ollama，请启动 Ollama 后重试。") from error

    return True


def _stream_ollama(messages, on_token):
    """Stream one local Ollama answer through a callback."""
    request_data = {
        "model": CHAT_MODEL,
        "messages": messages,
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
            received_text = False

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue

                try:
                    response_chunk = json.loads(line)
                except json.JSONDecodeError as error:
                    raise AIServiceError("Ollama 返回了无法解析的响应。") from error

                if response_chunk.get("error"):
                    raise AIServiceError(
                        f"Ollama 模型错误：{response_chunk['error']}"
                    )

                content = response_chunk.get("message", {}).get("content", "")

                if content:
                    on_token(content)
                    received_text = True

                if response_chunk.get("done"):
                    break

            if not received_text:
                raise AIServiceError("Ollama 未返回任何文本。")
    except requests.exceptions.ConnectionError as error:
        raise AIServiceError("与本地 Ollama 的连接已断开。") from error
    except requests.exceptions.Timeout as error:
        raise AIServiceError("本地模型响应超时，请重试。") from error
    except requests.exceptions.HTTPError as error:
        try:
            message = response.json().get("error", str(error))
        except (ValueError, UnboundLocalError):
            message = str(error)

        if "not found" in message.casefold():
            raise AIServiceError(
                f"本地模型 {CHAT_MODEL} 不可用，请先通过 Ollama 安装。"
            ) from error

        raise AIServiceError(f"Ollama 请求失败：{message}") from error
    except requests.exceptions.RequestException as error:
        raise AIServiceError(f"无法请求本地 Ollama：{error}") from error


def stream_normal_chat(
    message,
    on_token,
    persona=None,
    language=None,
    on_state=lambda state: None,
):
    """Stream a persona-styled normal-chat response locally."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("消息不能为空。")

    selected_persona = persona or get_active_persona()
    selected_language = language or get_language_preference()
    messages = [
        {
            "role": "system",
            "content": build_persona_instruction(selected_persona),
        },
        {
            "role": "system",
            "content": build_language_instruction(selected_language, message),
        },
        {"role": "user", "content": message.strip()},
    ]
    on_state("thinking")
    _stream_ollama(messages, on_token)


def stream_knowledge_chat(
    question,
    on_token,
    persona=None,
    language=None,
    top_k=DEFAULT_TOP_K,
    minimum_score=MIN_RELEVANCE_SCORE,
    on_state=lambda state: None,
):
    """Retrieve local context, stream a grounded answer, and return sources."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题不能为空。")

    on_state("searching")
    store = VectorStore()

    try:
        if store.count() == 0:
            raise AIServiceError("本地知识库为空，请先导入文档。")

        query_embedding = generate_query_embedding(question)
        results = store.search(query_embedding, top_k=top_k)
        relevant_results = select_relevant_results(
            results,
            minimum_score=minimum_score,
        )

        if not relevant_results:
            raise AIServiceError(
                "本地知识库中没有足够相关的信息，无法可靠回答该问题。"
            )

        context = build_context(relevant_results)
        user_message = (
            f"Retrieved context:\n\n{context}\n\n"
            f"Question: {question}\n\n"
            "Give a concise, grounded answer."
        )
        messages = build_rag_system_messages(persona, language, question)
        messages.append({"role": "user", "content": user_message})
        on_state("thinking")
        _stream_ollama(messages, on_token)
        return format_sources(relevant_results)
    finally:
        store.client.close()


def import_document(file_path, on_progress=lambda message: None):
    """Load, chunk, embed, and persist one supported local document."""
    document_pages = load_document_pages(file_path)
    chunks, chunk_metadata = chunk_document_pages(document_pages)

    if not chunks:
        raise AIServiceError("文档中没有可导入的文本。")

    on_progress(f"已创建 {len(chunks)} 个 Chunk，正在生成本地 Embedding…")
    return import_document_chunks(
        file_path,
        chunks,
        chunk_metadata,
        progress_function=on_progress,
    )


def reindex_library_document(document, on_progress=lambda message: None):
    """Re-index one stored local document with progress callbacks."""
    return reindex_document(document, progress_function=on_progress)
