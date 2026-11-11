"""Pembungkus tipis Gemini API: client lazy, retry, cache embedding ke disk."""

import hashlib
import json
import logging
import os
import time

import numpy as np
from google import genai
from google.genai import types

from app import config

log = logging.getLogger("helpdesk.llm")


class LLMError(RuntimeError):
    """Gemini API tidak bisa dipakai (API key hilang, kuota habis, jaringan, dsb)."""


_client = None


def get_client():
    """Client dibuat saat pertama kali dipakai, supaya aplikasi tetap bisa start tanpa API key."""
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY belum diset (lihat .env.example)")
        _client = genai.Client(api_key=key)
    return _client


def _retry(fn, attempts: int = 3):
    for i in range(attempts):
        try:
            return fn()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - semua kegagalan API dibungkus jadi LLMError
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if i == attempts - 1:
                raise LLMError(f"Gemini API gagal ({type(e).__name__} {code or ''})".strip()) from e
            log.warning("Gemini API error (%s %s), retry %d", type(e).__name__, code, i + 1)
            time.sleep(2 * 2**i)


def generate_text(prompt: str, model: str | None = None, max_output_tokens: int = 2048) -> str:
    """Panggil LLM. Thinking token ikut menghitung max_output_tokens, jadi batasnya sengaja longgar."""
    resp = _retry(
        lambda: get_client().models.generate_content(
            model=model or config.LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=max_output_tokens),
        )
    )
    text = (resp.text or "").strip()
    if not text:
        raise LLMError("LLM mengembalikan respons kosong")
    return text


def embed_texts(texts: list[str], task_type: str) -> np.ndarray:
    """Embed beberapa teks dalam satu request; hasil di-cache ke disk per (task_type, teks)."""
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    path = os.path.join(config.CACHE_DIR, f"emb_{config.EMBED_MODEL}.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    key = lambda t: hashlib.md5(f"{task_type}|{t}".encode()).hexdigest()  # noqa: E731
    todo = [t for t in texts if key(t) not in cache]
    if todo:
        res = _retry(
            lambda: get_client().models.embed_content(
                model=config.EMBED_MODEL,
                contents=todo,
                config=types.EmbedContentConfig(task_type=task_type),
            )
        )
        if len(res.embeddings) != len(todo):
            raise LLMError("jumlah embedding tidak sama dengan jumlah teks")
        for t, e in zip(todo, res.embeddings):
            cache[key(t)] = e.values
        json.dump(cache, open(path, "w"))
    return np.array([cache[key(t)] for t in texts], dtype="float32")
