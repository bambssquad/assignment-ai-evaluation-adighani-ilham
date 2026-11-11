"""
Entry point FastAPI: IT Helpdesk Assistant.

  POST /ask  {"query": "..."}  ->  {"status": ..., "intent": ..., "data": ...}
  GET  /health

Alur: /ask -> router.handle_query -> RAG (intent faq) atau tool call (intent ticket_status).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import rag
from app.router import handle_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("helpdesk.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Bangun index FAISS sekali saat startup. Bila gagal (mis. API key belum diset),
    # aplikasi tetap hidup: tool call tetap jalan dan index dibangun ulang pada request FAQ pertama.
    try:
        rag.build_index()
        log.info("index FAISS siap")
    except Exception as e:  # noqa: BLE001
        log.warning("index belum bisa dibangun saat startup (%s); akan dicoba lagi saat request FAQ", e)
    yield


app = FastAPI(title="IT Helpdesk Assistant API", lifespan=lifespan)


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Pertanyaan atau permintaan karyawan")


class AskResponse(BaseModel):
    status: str  # success | not_found | needs_input | error
    intent: str  # faq | ticket_status | unknown
    data: str


@app.get("/health")
async def health():
    """Memastikan service berjalan; juga melaporkan apakah index RAG sudah siap."""
    return {"status": "ok", "rag_index_ready": rag.is_ready()}


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    """Terima query bebas (informasi/kebijakan ATAU cek status tiket) dan kembalikan response terstruktur."""
    result = await handle_query(request.query)
    if result["status"] == "error":
        # intent tidak dikenali -> 400 (kesalahan input); LLM gagal -> 503 (layanan sementara tidak tersedia)
        code = 400 if result["intent"] == "unknown" else 503
        return JSONResponse(status_code=code, content=result)
    return AskResponse(**result)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    log.exception("error tak terduga: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "intent": "unknown", "data": "Terjadi kesalahan internal pada server."},
    )
