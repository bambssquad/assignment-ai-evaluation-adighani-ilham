"""
Modul RAG: knowledge base -> embedding -> index FAISS -> semantic search -> jawaban LLM.

Index dibangun sekali (saat startup, atau lazy pada request pertama), bukan di setiap request.
Vektor dinormalisasi L2 lalu disimpan di IndexFlatIP, sehingga skor = cosine similarity.
"""

import asyncio
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


def build_prompt(query: str, results: list[dict]) -> str:
    """Prompt terstruktur Persona / Task / Context / Format. Jawaban dibatasi pada context."""
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
- Bahasa Indonesia, sapaan sopan, maksimal 4 kalimat.
- Sebutkan nomor FAQ yang dipakai di akhir, misal: (Sumber: FAQ 1).

# Pertanyaan karyawan
{query}

# Jawaban"""


async def answer_from_knowledge_base(query: str) -> str:
    """Dipanggil router saat intent = 'faq': semantic search -> prompt -> LLM."""
    results = await asyncio.to_thread(semantic_search, query)
    prompt = build_prompt(query, results)
    return await asyncio.to_thread(generate_text, prompt)
