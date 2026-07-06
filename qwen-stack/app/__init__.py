"""Qwen Stack gateway package.

A minimal FastAPI gateway that fronts an Ollama OpenAI-compatible upstream,
adding API-key auth, per-key rate limiting, usage logging, and an Anthropic
Messages compatibility shim.
"""

__version__ = "0.1.0"
