"""
Router: menentukan apakah query butuh RAG ('faq') atau tool call ('ticket_status').

  query -> detect_intent -> faq           -> RAG (rag.py)
                         -> ticket_status -> extract_ticket_id -> tool (tools.py)
                         -> lainnya       -> 'unknown' (error terstruktur)

Dua mode intent detection (env INTENT_MODE):
  keyword (default) : aturan regex + kata kunci, tanpa LLM, deterministik, tanpa biaya kuota.
  llm               : klasifikasi oleh Gemini; bila LLM gagal otomatis jatuh ke keyword.
"""

import asyncio
import logging
import re

from app import config
from app.llm import LLMError, generate_text
from app.rag import answer_from_knowledge_base
from app.tools import find_ticket, get_ticket_status, normalize_ticket_id

log = logging.getLogger("helpdesk.router")

# 'TICKET-0042', 'ticket_42', 'tiket #7' (pemisah wajib) atau 'ticket 0042' (spasi, min. 3 digit)
_TICKET_ID_RE = re.compile(r"\b(?:ticket|tiket)(?:\s*[-_#]\s*(\d{1,6})|\s+#?(\d{3,6}))\b", re.IGNORECASE)
_TICKET_WORDS = ("tiket", "ticket")
_STATUS_WORDS = (
    "status", "cek", "periksa", "lacak", "pantau", "track", "progres", "progress",
    "perkembangan", "sampai mana", "update", "sudah selesai", "udah selesai", "diproses",
)
# Pertanyaan 'cara ...' tanpa nomor tiket = pertanyaan prosedur (FAQ), bukan cek tiket
_HOWTO_PREFIX = re.compile(r"^\s*(bagaimana|gimana|cara|apa(kah)?|kenapa|mengapa|berapa)\b", re.IGNORECASE)

# [FIX ISSUE-05] 'tiket saya gimana ya?': menyebut tiket milik sendiri tanpa nomor -> cek status (minta nomor tiket),
# kecuali kalimatnya tentang MEMBUAT/prosedur tiket (cara, buat, ajukan, ...)
_OWN_TICKET_RE = re.compile(r"\b(?:tiket|ticket)\w*\s+(?:yang\s+)?(?:saya|aku|ku|gue|gw|kami)\b", re.IGNORECASE)
_CREATE_WORDS = ("cara", "prosedur", "membuat", "buat ", "bikin", "ajukan", "mengajukan", "submit", "kirim", "sla")

# [FIX ISSUE-03] sapaan/terima kasih murni tidak perlu retrieval + LLM (hemat embedding call dan ~330 token per pesan)
_SMALLTALK_RE = re.compile(
    r"^\s*(?:halo+|hai+|hi+|hello|hey|p|permisi|selamat\s+(?:pagi|siang|sore|malam)|assalamu'?alaikum|terima\s*kasih|makasih|thanks?|thx|ok(?:e|ay)?)"
    r"(?:\s+(?:kak|pak|bu|mas|mba|min|admin|bot|helpdesk|semua|tim|ya+|dong|deh|banyak))*[\s!.,?]*$",
    re.IGNORECASE,
)
SMALLTALK_REPLY = (
    "Halo! Saya asisten IT Helpdesk & HR internal. Saya bisa membantu soal cuti, password, perangkat, "
    "dan cek status tiket (contoh: 'status TICKET-0002'). Ada yang bisa dibantu?"
)

VALID_INTENTS = {"faq", "ticket_status"}

INTENT_PROMPT = """Klasifikasikan pesan karyawan berikut ke dalam tepat satu kategori:
- ticket_status : meminta pengecekan status/progres sebuah tiket tertentu (biasanya menyebut nomor seperti TICKET-0042).
- faq : pertanyaan informasi/kebijakan/prosedur umum (cuti, password, perangkat, HR, kebijakan kerja, dsb), termasuk pertanyaan tentang CARA membuat/memakai tiket, dan pertanyaan di luar topik.
Jawab hanya dengan satu kata: faq atau ticket_status.

Pesan: {query}
Kategori:"""


def detect_intent_keyword(query: str) -> str:
    """Baseline berbasis aturan."""
    q = query.lower()
    if _TICKET_ID_RE.search(query):
        return "ticket_status"
    has_ticket_word = any(w in q for w in _TICKET_WORDS)
    has_status_word = any(w in q for w in _STATUS_WORDS)
    if has_ticket_word and has_status_word and not _HOWTO_PREFIX.match(query):
        return "ticket_status"
    if _OWN_TICKET_RE.search(query) and not any(w in q for w in _CREATE_WORDS):
        return "ticket_status"
    return "faq"


def _detect_intent_llm(query: str) -> str:
    raw = generate_text(INTENT_PROMPT.format(query=query), model=config.INTENT_LLM_MODEL, max_output_tokens=1024)
    label = re.sub(r"[^a-z_]", "", raw.strip().lower().split()[0]) if raw.strip() else ""
    return label if label in VALID_INTENTS else "unknown"


async def detect_intent(query: str) -> str:
    """Kembalikan 'faq', 'ticket_status', atau 'unknown'."""
    if not query or not query.strip():
        return "unknown"
    if config.INTENT_MODE == "llm":
        try:
            return await asyncio.to_thread(_detect_intent_llm, query)
        except LLMError as e:
            log.warning("intent LLM gagal (%s), fallback ke keyword", e)
    return detect_intent_keyword(query)


def extract_ticket_id(query: str) -> str | None:
    """Ambil ticket_id dari teks dan normalisasi ke 'TICKET-XXXX'. None bila tidak ada nomor tiket."""
    m = _TICKET_ID_RE.search(query)
    if not m:
        return None
    return normalize_ticket_id(f"TICKET-{m.group(1) or m.group(2)}")


async def handle_query(query: str) -> dict:
    """Dipanggil endpoint /ask. Mengembalikan dict {status, intent, data}."""
    if _SMALLTALK_RE.match(query or ""):
        return {"status": "success", "intent": "smalltalk", "data": SMALLTALK_REPLY}
    intent = await detect_intent(query)

    if intent == "ticket_status":
        ticket_id = extract_ticket_id(query)
        if ticket_id is None:
            return {
                "status": "needs_input",
                "intent": "ticket_status",
                "data": "Mohon sertakan nomor tiketnya (format: TICKET-XXXX), misalnya: 'status TICKET-0002'.",
            }
        data = get_ticket_status(ticket_id)
        status = "success" if find_ticket(ticket_id) else "not_found"
        return {"status": status, "intent": "ticket_status", "data": data}

    if intent == "faq":
        try:
            data = await answer_from_knowledge_base(query)
        except LLMError as e:
            log.error("RAG gagal: %s", e)
            return {
                "status": "error",
                "intent": "faq",
                "data": "Maaf, layanan AI sedang tidak dapat menjawab. Silakan coba lagi nanti atau hubungi tim IT/HR.",
            }
        return {"status": "success", "intent": "faq", "data": data}

    return {"status": "error", "intent": "unknown", "data": "Maaf, saya tidak memahami permintaan Anda."}
