# Personal-AI-Second-Brain
# 🧠 Personal AI Second Brain

A local-first personal AI knowledge assistant built with Large Language Models (LLMs), Retrieval-Augmented Generation (RAG), and vector databases.

## 📌 Overview

Personal AI Second Brain is a project that aims to build a private AI-powered knowledge management system.

Instead of manually searching through documents, this system allows users to upload personal files, retrieve information through semantic search, and interact with their own knowledge base.

The goal is to create a personal AI assistant that can understand and organize:

- 📚 Learning materials
- 📝 Notes
- 💻 Programming documents
- 🚀 Project files
- 📄 Research papers

---

# 🎯 Goals

The project focuses on building a practical personal knowledge system with:

- Local AI inference
- Private knowledge storage
- Semantic document retrieval
- RAG-based question answering
- Personal knowledge organization

The final goal is to create an AI assistant that can help users:

- Find information from their own files
- Summarize knowledge
- Connect related concepts
- Support learning and projects

---

# 🏗️ Architecture

The system follows a Retrieval-Augmented Generation (RAG) architecture.
Documents
|
v
Document Parser
|
v
Text Chunking
|
v
Embedding Model
|
v
Vector Database
|
v
Semantic Retrieval
|
v
Large Language Model
|
v
Answer + Sources

## Phase 6B: Real-time Local Voice

The PySide6 desktop interface keeps text generation independent from speech:

```text
Ollama token stream
    -> natural sentence segmenter
    -> local Piper synthesis FIFO
    -> local WAV playback FIFO
```

- Chinese `。！？；` and English `. ! ?` boundaries release useful sentences.
- Piper voice models are warmed and cached only while VOICE is enabled.
- Synthesis and playback use separate workers, so the next sentence can be
  prepared while the current sentence is playing.
- Stop Speaking and explicit microphone activation immediately stop playback and
  invalidate pending speech without cancelling the streamed text answer.
- Voice input, STT, TTS, temporary audio, and playback remain on-device. There is
  no wake word, background listening, voice cloning, or cloud speech service.

## Persona Avatar Assets

The desktop UI uses processed, square Persona artwork from `assets/avatars/`:

- `delamain.png` is presented in a mildly rounded cyan system frame.
- `fairy.png` is presented with a circular violet mask and halo.
- Neutral retains the minimal programmatic placeholder.

One reusable Qt avatar widget loads and caches source and high-DPI prepared
pixmaps for dialogue panels, Persona selection cards, and the current-Persona
header. Lightweight QPainter overlays provide Persona-specific motion without
transforming the full source portraits. Fairy keeps its base image and size stable
while a clipped inner ring layer, including its circular accent, rotates at a
constant speed; it does not breathe, pulse, flash, scale, or emit listening waves.
Only the newest active dialogue panel may run continuous effects; completed,
hidden, header, selection, and SPEAKING avatars remain static. Missing image assets
log a development warning and fall back to the programmatic renderer.

---

# 🛠️ Technology Stack

## AI

- Ollama
- Qwen Series Models

## Backend

- Python
- FastAPI

## Knowledge Retrieval

- Embedding Models
- ChromaDB

## Frontend

- HTML
- CSS
- JavaScript

## Development

- GitHub
- VS Code

---

# 💻 Hardware Environment

Current development environment:

| Component | Specification |
|-|-|
| CPU | Intel Core Ultra 7 155H |
| RAM | 32GB |
| OS | Windows |

The project is designed to run efficiently on consumer hardware.

---

# 🚀 Development Roadmap

## Phase 0: Environment Setup

- [x] Create GitHub repository
- [x] Install Ollama
- [ ] Test local LLM inference
- [ ] Select suitable local model

---

## Phase 1: Local AI Engine

- [ ] Connect Python with Ollama API
- [ ] Build basic AI interaction interface

---

## Phase 2: Document Processing

- [ ] Support PDF/TXT/Markdown files
- [ ] Extract document contents
- [ ] Implement text chunking

---

## Phase 3: Knowledge Retrieval

- [ ] Generate embeddings
- [ ] Store vectors
- [ ] Implement semantic search

---

## Phase 4: RAG System

- [ ] Retrieve relevant knowledge
- [ ] Generate answers based on documents
- [ ] Provide source references

---

## Phase 5: Web Application

- [ ] Build user interface
- [ ] Upload documents
- [ ] Chat with knowledge base

---

## Phase 6: Personal Memory System

Future features:

- Knowledge organization
- Learning history
- Project memory
- Knowledge graph
- AI-assisted planning

---

# 📂 Project Structure
AI-Second-Brain/

├── app/
│
├── data/
│
├── tests/
│
├── docs/
│
├── README.md
│
└── requirements.txt

---

# 🌱 Vision

The project aims to explore how local AI models can become a personal knowledge companion.

By combining:

- Local Large Language Models
- Retrieval-Augmented Generation
- Personal Data Management

this project hopes to create a long-term AI system that grows together with its user.

---

# 📜 License

MIT License
