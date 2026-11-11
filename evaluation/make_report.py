"""Menggabungkan hasil run (evaluation/runs/*.csv) + observasi manual -> evaluation_results.csv, ringkasan angka.
Semua angka berasal dari run_eval.py (diukur), observasi ditulis manual setelah membaca response."""
import csv
import json
import statistics as st
from pathlib import Path

EV = Path(__file__).resolve().parent
OBS = {
    "EVAL-01": ("Sesuai", "Akurat: 12 hari, min. masa kerja 3 bulan, sumber FAQ 1 (top-1, skor 0.799). Tidak ada hallucination."),
    "EVAL-02": ("Sesuai", "FAQ 8 jadi top-1 (skor 0.807) walau frasa informal; jawaban sesuai isi FAQ."),
    "EVAL-03": ("Sesuai", "Sinonim 'kata sandi' -> FAQ 12 (laptop) top-1 (skor 0.800); jawaban sesuai FAQ."),
    "EVAL-04": ("Sesuai", "Mengakui belum punya informasi parkir, tidak mengarang. Retrieval tetap mengambil 3 FAQ tak relevan (skor 0.672) tetapi LLM mengabaikannya."),
    "EVAL-05": ("Sesuai", "Tidak menyebut nama CEO; mengakui keterbatasan informasi. Tidak ada hallucination."),
    "EVAL-06": ("Sesuai", "Data cocok dengan mock_tickets.csv (TICKET-0005, Diproses, 2026-08-27). 0 token LLM, <10 ms."),
    "EVAL-07": ("Sesuai", "Status not_found + pesan informatif, HTTP 200, tidak crash."),
    "EVAL-08": ("TIDAK SESUAI", "Router memilih intent faq, bukan ticket_status. Jawaban 'belum memiliki informasi mengenai status tiket Anda' tidak meminta nomor tiket; memakai 1 embedding call + 1 LLM call (371 token) untuk jawaban tak membantu."),
    "EVAL-09": ("Catatan kualitatif", "Router hanya memilih satu intent (faq). Jawaban benar menyatakan KB tidak memuat 'laptop lambat' maupun 'cara cek tiket', tetapi bagian aksi (cek tiket) tidak diarahkan ke tool / tidak meminta nomor tiket."),
    "EVAL-10": ("Sesuai", "Konsisten dengan EVAL-01: 12 hari, min. 3 bulan, FAQ 1 top-1 pada kedua kalimat."),
    "EVAL-11": ("TIDAK SESUAI", "False 'tidak ada informasi': menyatakan belum punya info kebijakan cuti & permintaan perangkat padahal KB memilikinya (FAQ 1/2/14). Top-3 retrieval (FAQ 12, 31, 8) tidak memuat satu pun FAQ cuti/perangkat. Tidak ada angka karangan, tetapi jawaban tidak lengkap."),
    "EVAL-12": ("Boros", "Sapaan 'halo' memicu pipeline penuh: 1 embedding call + 1 LLM call, 334 prompt token - nyaris sama dengan EVAL-11 (346). Biaya token ditentukan template+context tetap, bukan panjang query."),
}


def load(name):
    return {r["scenario_id"]: r for r in csv.DictReader(open(EV / "runs" / name, encoding="utf-8"))}


def fnum(r, k):
    return float(r[k])


before, after = load("scenarios_before.csv"), load("scenarios_after.csv")
sb, sa = load("safety_before.csv"), load("safety_after.csv")

cols = ["scenario_id", "category", "query", "intent", "status", "http_status", "response", "response_time_s",
        "retrieval_s", "llm_s", "llm_calls", "prompt_tokens", "output_tokens", "total_tokens", "retrieved_faq_score",
        "penilaian", "observasi"]
with open(EV / "evaluation_results.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for sid, r in before.items():
        pen, obs = OBS[sid]
        w.writerow(dict(scenario_id=sid, category=r["category"], query=r["query"], intent=r["intent"], status=r["status"],
                        http_status=r["http_status"], response=r["response"], response_time_s=r["latency_s"],
                        retrieval_s=r["retrieval_s"], llm_s=r["llm_s"], llm_calls=r["llm_calls"],
                        prompt_tokens=r["prompt_tokens"], output_tokens=r["output_tokens"], total_tokens=r["total_tokens"],
                        retrieved_faq_score=r["retrieved"], penilaian=pen, observasi=obs))

rag_b = [r for r in before.values() if int(r["llm_calls"]) > 0]
rag_a = [r for r in after.values() if int(r["llm_calls"]) > 0]
summary = {
    "before": {
        "rag_n": len(rag_b),
        "rag_latency_mean": round(st.mean(fnum(r, "latency_s") for r in rag_b), 3),
        "rag_retrieval_mean": round(st.mean(fnum(r, "retrieval_s") for r in rag_b), 3),
        "rag_llm_mean": round(st.mean(fnum(r, "llm_s") for r in rag_b), 3),
        "rag_prompt_tokens_mean": round(st.mean(fnum(r, "prompt_tokens") for r in rag_b), 1),
        "rag_total_tokens_mean": round(st.mean(fnum(r, "total_tokens") for r in rag_b), 1),
        "tool_latency_ms": [round(fnum(before[k], "latency_s") * 1000, 1) for k in ("EVAL-06", "EVAL-07")],
        "all12_total_tokens": sum(int(r["total_tokens"]) for r in before.values()),
    },
    "after": {
        "rag_n": len(rag_a),
        "rag_latency_mean": round(st.mean(fnum(r, "latency_s") for r in rag_a), 3),
        "rag_retrieval_mean": round(st.mean(fnum(r, "retrieval_s") for r in rag_a), 3),
        "rag_llm_mean": round(st.mean(fnum(r, "llm_s") for r in rag_a), 3),
        "rag_prompt_tokens_mean": round(st.mean(fnum(r, "prompt_tokens") for r in rag_a), 1),
        "rag_total_tokens_mean": round(st.mean(fnum(r, "total_tokens") for r in rag_a), 1),
        "all12_total_tokens": sum(int(r["total_tokens"]) for r in after.values()),
    },
    "pairs": {sid: {"before": {k: before[sid][k] for k in ("latency_s", "retrieval_s", "llm_s", "prompt_tokens", "output_tokens", "total_tokens", "intent", "status")},
                    "after": {k: after[sid][k] for k in ("latency_s", "retrieval_s", "llm_s", "prompt_tokens", "output_tokens", "total_tokens", "intent", "status")}}
              for sid in before},
    "safety_pairs": {sid: {"before": {k: sb[sid][k] for k in ("http_status", "intent", "status", "total_tokens", "latency_s")},
                           "after": {k: sa[sid][k] for k in ("http_status", "intent", "status", "total_tokens", "latency_s")}}
                     for sid in sb},
}
json.dump(summary, open(EV / "analysis_summary.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps({k: summary[k] for k in ("before", "after")}, indent=1))
for sid in ("EVAL-11", "EVAL-12", "EVAL-08"):
    print(sid, summary["pairs"][sid])
print("SAF-10", summary["safety_pairs"]["SAF-10"])
