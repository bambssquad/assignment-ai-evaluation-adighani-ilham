"""Konfigurasi aplikasi. Semua nilai sensitif dibaca dari environment variable (.env), tidak ada hardcode."""

import os

from dotenv import load_dotenv

load_dotenv()  # membaca .env bila ada (lokal); di Docker diisi lewat --env-file / -e

EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.8-flash")
INTENT_MODE = os.getenv("INTENT_MODE", "keyword").lower()  # "keyword" (baseline) atau "llm"
INTENT_LLM_MODEL = os.getenv("INTENT_LLM_MODEL", LLM_MODEL)

KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "data/it_helpdesk_knowledge_base.csv")
MOCK_TICKETS_PATH = os.getenv("MOCK_TICKETS_PATH", "data/mock_tickets.csv")
CACHE_DIR = os.getenv("CACHE_DIR", ".cache")
TOP_K = int(os.getenv("TOP_K", "3"))
