"""
Tools: fungsi 'aksi' yang mengambil data spesifik (bukan retrieval semantik).
Saat ini satu tool: cek status tiket dari data mock (data/mock_tickets.csv).
"""

import re
from functools import lru_cache

import pandas as pd

from app import config

_TICKET_RE = re.compile(r"^TICKET-(\d{1,6})$")


@lru_cache(maxsize=1)
def _load_tickets() -> pd.DataFrame:
    return pd.read_csv(config.MOCK_TICKETS_PATH, dtype=str).fillna("")


def normalize_ticket_id(ticket_id: str) -> str:
    """'ticket-42' / 'TICKET-0042' -> 'TICKET-0042'. Input tidak valid dikembalikan apa adanya (uppercase)."""
    tid = (ticket_id or "").strip().upper()
    m = _TICKET_RE.match(tid)
    return f"TICKET-{int(m.group(1)):04d}" if m else tid


def find_ticket(ticket_id: str) -> dict | None:
    """Kembalikan baris tiket sebagai dict, atau None bila tidak ditemukan."""
    df = _load_tickets()
    row = df[df["ticket_id"] == normalize_ticket_id(ticket_id)]
    return None if row.empty else row.iloc[0].to_dict()


def get_ticket_status(ticket_id: str) -> str:
    """Susun jawaban status tiket. Tiket tidak ditemukan -> pesan informatif (tidak melempar error)."""
    tid = normalize_ticket_id(ticket_id)
    t = find_ticket(tid)
    if t is None:
        return f"Tiket {tid} tidak ditemukan. Mohon periksa kembali nomor tiketnya (format: TICKET-XXXX)."
    return (
        f"Tiket {t['ticket_id']} ({t['category']}): \"{t['subject']}\" "
        f"berstatus {t['status']}, update terakhir {t['last_update']}."
    )
