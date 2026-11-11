"""
Modul RAG: knowledge base -> embedding -> index FAISS -> semantic search -> jawaban LLM.

Index dibangun sekali (saat startup, atau lazy pada request pertama), bukan di setiap request.
Vektor dinormalisasi L2 lalu disimpan di IndexFlatIP, sehingga skor = cosine similarity.
"""

import asyncio
import re
import threading

import faiss
import pandas as pd

from app import config
from app.llm import embed_texts, generate_text

_index = None
_df = None
_lock = threading.Lock()


def build_index():
    """Baca knowledge base, buat embedding (1 unit = 1 FAQ: 'Q: ... A: ...'), simpan ke index FAISS."""
    global _index, _df
    with _lock:
        df = pd.read_csv(config.KNOWLEDGE_BASE_PATH)
        missing = {"faq_id", "category", "question", "answer"} - set(df.columns)
        if missing:
            raise ValueError(f"kolom knowledge base kurang: {missing}")
        docs = ("Q: " + df["question"] + " A: " + df["answer"]).tolist()
        emb = embed_texts(docs, "RETRIEVAL_DOCUMENT")
        faiss.normalize_L2(emb)
        index = faiss.IndexFlatIP(emb.shape[1])
        index.add(emb)
        _df, _index = df, index
        return index


def is_ready() -> bool:
    return _index is not None


def semantic_search(query: str, top_k: int | None = None) -> list[dict]:
    """Cari top-k FAQ paling mirip (cosine similarity). Mengembalikan list dict berurutan dari terbaik."""
    if _index is None:
        build_index()
    k = min(top_k or config.TOP_K, _index.ntotal)
    q = embed_texts([query], "RETRIEVAL_QUERY")
    faiss.normalize_L2(q)
    scores, idxs = _index.search(q, k)
    return [
        {
            "rank": r + 1,
            "faq_id": int(_df.iloc[i]["faq_id"]),
            "category": _df.iloc[i]["category"],
            "question": _df.iloc[i]["question"],
            "answer": _df.iloc[i]["answer"],
            "score": float(scores[0][r]),
        }
        for r, i in enumerate(idxs[0])
    ]


# [FIX ISSUE-01] Query multi-topik ("cuti, reset password, perangkat, dan onboarding") dibagi per topik, karena satu embedding
# untuk banyak topik mengambang di antara topik-topik itu dan top-3 bisa tidak memuat satu pun FAQ yang tepat (lihat EVAL-11).
_FILLER_RE = re.compile(
    r"\b(tolong|mohon|jelaskan|sedetail-detailnya|selengkapnya|seluruh|semua|sekaligus|dalam satu jawaban|secara lengkap)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r",|;|\bdan\b|\bserta\b", re.IGNORECASE)
MIN_WORDS_FOR_SPLIT = 10
MAX_SUBQUERIES = 5
PER_SUBQUERY_K = 2
MIN_SUBQUERY_SCORE = 0.68  # dikalibrasi dari 4 query uji (lihat README); heuristik, bukan nilai universal


def split_query(query: str) -> list[str]:
    """Pecah query panjang menjadi sub-query per topik. Query pendek/satu topik dikembalikan apa adanya."""
    if len(query.split()) < MIN_WORDS_FOR_SPLIT:
        return [query]
    parts = [" ".join(_FILLER_RE.sub(" ", p).split()).strip(" .!?") for p in _SPLIT_RE.split(query)]
    parts = [p for p in parts if len(p.split()) >= 2]
    return parts[:MAX_SUBQUERIES] if len(parts) >= 2 else [query]


def _to_results(pairs: list[tuple[int, float]]) -> list[dict]:
    return [
        {"rank": r + 1, "faq_id": int(_df.iloc[i]["faq_id"]), "category": _df.iloc[i]["category"],
         "question": _df.iloc[i]["question"], "answer": _df.iloc[i]["answer"], "score": float(sc)}
        for r, (i, sc) in enumerate(pairs)
    ]


def retrieve(query: str) -> tuple[list[dict], int]:
    """Retrieval adaptif. Return (hasil, jumlah sub-query yang dipakai). 1 = perilaku lama (top-k satu query).

    Query dipecah hanya bila tiap sub-topik menemukan FAQ yang cukup relevan (skor top-1 >= MIN_SUBQUERY_SCORE).
    Tanpa gerbang ini kalimat biasa yang kebetulan memuat koma/'dan' ikut dipecah dan membuat context membengkak.
    """
    subs = split_query(query)
    if len(subs) == 1:
        return semantic_search(query), 1
    if _index is None:
        build_index()
    emb = embed_texts([query] + subs, "RETRIEVAL_QUERY")  # query utuh + semua sub-query dalam SATU request embedding
    faiss.normalize_L2(emb)
    k = min(max(config.TOP_K, PER_SUBQUERY_K), _index.ntotal)
    scores, idxs = _index.search(emb, k)
    sub_scores, sub_idxs = scores[1:, :PER_SUBQUERY_K], idxs[1:, :PER_SUBQUERY_K]
    if sub_scores[:, 0].min() < MIN_SUBQUERY_SCORE:
        return _to_results(list(zip(idxs[0, : config.TOP_K].tolist(), scores[0, : config.TOP_K].tolist()))), 1
    best: dict[int, float] = {}
    for row_s, row_i in zip(sub_scores, sub_idxs):
        for sc, i in zip(row_s, row_i):
            best[int(i)] = max(best.get(int(i), 0.0), float(sc))
    return _to_results(sorted(best.items(), key=lambda kv: -kv[1])), len(subs)


def build_prompt(query: str, results: list[dict], multi_topic: bool = False) -> str:
    """Prompt terstruktur Persona / Task / Context / Format. Jawaban dibatasi pada context."""
    max_sentences = 8 if multi_topic else 4
    context = "\n\n".join(
        f"[FAQ {r['faq_id']} | {r['category']}]\nPertanyaan: {r['question']}\nJawaban: {r['answer']}" for r in results
    )
    return f"""# Persona
Kamu adalah asisten IT Helpdesk dan HR internal perusahaan yang ramah, sopan, dan ringkas.

# Task
Jawab pertanyaan karyawan HANYA berdasarkan FAQ pada bagian Context. Jangan memakai pengetahuan di luar Context dan jangan mengarang angka, kebijakan, atau prosedur.
Jika Context tidak memuat informasi yang menjawab pertanyaan, katakan terus terang bahwa kamu belum memiliki informasi tersebut dan sarankan karyawan menghubungi tim IT/HR. Jika hanya sebagian yang terjawab, jawab bagian yang ada dan sebutkan bagian yang belum tersedia.

# Context
{context}

# Format
- Bahasa Indonesia, sapaan sopan, maksimal {max_sentences} kalimat.
- Sebutkan nomor FAQ yang benar-benar kamu pakai untuk menjawab di akhir, misal: (Sumber: FAQ 1). Jika kamu menolak atau belum memiliki informasi, JANGAN mencantumkan sumber.

# Pertanyaan karyawan
{query}

# Jawaban"""


async def answer_from_knowledge_base(query: str) -> str:
    """Dipanggil router saat intent = 'faq': semantic search -> prompt -> LLM."""
    results, n_sub = await asyncio.to_thread(retrieve, query)
    prompt = build_prompt(query, results, multi_topic=n_sub > 1)
    return await asyncio.to_thread(generate_text, prompt)
