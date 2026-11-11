"""Tes offline (tanpa API key / tanpa LLM) untuk perbaikan di system/. Jalankan: python evaluation/test_fixes.py"""
import os
import sys
from pathlib import Path

SYSTEM = Path(__file__).resolve().parent.parent / "system"
os.chdir(SYSTEM)
sys.path.insert(0, str(SYSTEM))
os.environ.pop("GEMINI_API_KEY", None)
os.environ["CACHE_DIR"] = "/tmp/ia5_test_cache"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.rag import split_query  # noqa: E402
from app.router import _SMALLTALK_RE, detect_intent_keyword  # noqa: E402

fails = []


def check(name, cond):
    print(("OK  " if cond else "XX  ") + name)
    if not cond:
        fails.append(name)


# ISSUE-05: router
check("EVAL-08 'tiket saya gimana ya?' -> ticket_status", detect_intent_keyword("tiket saya gimana ya?") == "ticket_status")
check("'kenapa tiket saya belum dibalas 3 hari?' -> ticket_status", detect_intent_keyword("kenapa tiket saya belum dibalas 3 hari?") == "ticket_status")
for q in ["bagaimana cara membuat tiket baru ke tim IT?", "cara buat tiket untuk laptop rusak", "berapa jatah cuti tahunan saya?"]:
    check(f"tidak salah rute ke ticket_status: {q!r}", detect_intent_keyword(q) == "faq")

# ISSUE-03: smalltalk hanya untuk sapaan murni
for q in ["halo", "Halo kak!", "selamat pagi", "terima kasih", "makasih ya"]:
    check(f"smalltalk: {q!r}", bool(_SMALLTALK_RE.match(q)))
for q in ["halo, berapa jatah cuti?", "halo status TICKET-0002", "terima kasih, tapi password VPN gimana?"]:
    check(f"BUKAN smalltalk: {q!r}", not _SMALLTALK_RE.match(q))

# ISSUE-01: split_query
check("EVAL-11 -> 4 sub-query", split_query("Tolong jelaskan sedetail-detailnya seluruh kebijakan cuti, reset password, permintaan perangkat, dan proses onboarding sekaligus dalam satu jawaban.")
      == ["kebijakan cuti", "reset password", "permintaan perangkat", "proses onboarding"])
check("query pendek tidak dipecah", split_query("berapa jatah cuti tahunan saya?") == ["berapa jatah cuti tahunan saya?"])

# ISSUE-05/03: validasi input terstruktur (tanpa memanggil LLM)
with TestClient(app, raise_server_exceptions=False) as c:
    for q, code, frag in [("", 422, "kosong"), ("     ", 422, "kosong"), ("a" * 501, 422, "panjang"), ("cuti " * 190, 422, "panjang")]:
        r = c.post("/ask", json={"query": q})
        b = r.json()
        check(f"validasi {q[:8]!r}...({len(q)} char) -> {code} terstruktur", r.status_code == code and b.get("status") == "error" and frag in b.get("data", ""))
    r = c.post("/ask", json={"query": "halo"})
    check("smalltalk tanpa API key tetap 200", r.status_code == 200 and r.json()["intent"] == "smalltalk")
    r = c.post("/ask", json={"query": "tiket saya gimana ya?"})
    check("EVAL-08 tanpa API key -> needs_input", r.status_code == 200 and r.json()["status"] == "needs_input")
    r = c.post("/ask", json={"wrong": 1})
    check("field salah -> 422 terstruktur", r.status_code == 422 and r.json().get("status") == "error")

print("\nGAGAL:" if fails else "\nSemua tes lulus", fails or "")
sys.exit(1 if fails else 0)
