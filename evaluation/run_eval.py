"""
Runner evaluasi: menjalankan skenario terhadap system/ (in-process lewat FastAPI TestClient) dan
mengukur latency per tahap + token usage dari usage_metadata Gemini. Semua angka diukur, bukan diestimasi.

Contoh:
  python evaluation/run_eval.py --suite scenarios --tag before
  python evaluation/run_eval.py --suite safety --tag before
  python evaluation/run_eval.py --suite scenarios --tag after --only EVAL-04,EVAL-12

Env: GEMINI_API_KEY (dari system/.env atau --env-file), LLM_MODEL (default dipaksa lewat --model).
"""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = ROOT / "system"
EVAL = ROOT / "evaluation"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["scenarios", "safety"], required=True)
    ap.add_argument("--tag", required=True, help="label run, mis. before / after")
    ap.add_argument("--only", default="", help="daftar id dipisah koma")
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--env-file", default=str(SYSTEM / ".env"))
    ap.add_argument("--pause", type=float, default=1.0, help="jeda antar request (detik)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    os.environ["LLM_MODEL"] = args.model
    # cache embedding baru per run: embedding query selalu lewat API, jadi latency retrieval terukur adil
    cache = EVAL / ".tmp" / f"cache_{args.tag}_{args.suite}"
    cache.mkdir(parents=True, exist_ok=True)
    for f in cache.glob("*"):
        f.unlink()
    os.environ["CACHE_DIR"] = str(cache)
    os.chdir(SYSTEM)
    sys.path.insert(0, str(SYSTEM))

    import app.llm as llm
    import app.rag as rag

    rec = {}

    def reset():
        rec.clear()
        rec.update(embed_s=0.0, embed_calls=0, llm_s=0.0, llm_calls=[], retries=0, retrieved=[], prompt_chars=0, errors=[])

    reset()

    class Models:
        def __init__(self, real):
            self.real = real

        def generate_content(self, **kw):
            t = time.perf_counter()
            try:
                r = self.real.generate_content(**kw)
            except Exception as e:  # noqa: BLE001
                rec["llm_s"] += time.perf_counter() - t
                rec["errors"].append(f"generate:{type(e).__name__}:{getattr(e, 'code', '')}")
                raise
            dt = time.perf_counter() - t
            u = r.usage_metadata
            rec["llm_s"] += dt
            rec["prompt_chars"] = len(kw.get("contents") or "")
            rec["llm_calls"].append(dict(
                model=kw.get("model"), sec=round(dt, 3),
                prompt=u.prompt_token_count or 0, output=u.candidates_token_count or 0,
                thoughts=u.thoughts_token_count or 0, total=u.total_token_count or 0))
            return r

        def embed_content(self, **kw):
            t = time.perf_counter()
            try:
                return self.real.embed_content(**kw)
            except Exception as e:  # noqa: BLE001
                rec["errors"].append(f"embed:{type(e).__name__}:{getattr(e, 'code', '')}")
                raise
            finally:
                rec["embed_s"] += time.perf_counter() - t
                rec["embed_calls"] += 1

    class Client:
        def __init__(self, real):
            self.models = Models(real.models)

    real_get = llm.get_client
    holder = {}

    def get_client():
        if "c" not in holder:
            holder["c"] = Client(real_get())
        return holder["c"]

    llm.get_client = get_client

    real_sleep = time.sleep

    def counting_sleep(s):
        rec["retries"] += 1
        real_sleep(s)

    llm.time.sleep = counting_sleep

    real_search = rag.semantic_search

    def search(query, top_k=None):
        res = real_search(query, top_k)
        rec["retrieved"] = [(r["faq_id"], round(r["score"], 3)) for r in res]
        return res

    rag.semantic_search = search

    if hasattr(rag, "retrieve"):  # versi sistem setelah perbaikan: retrieval adaptif (multi-query)
        real_retrieve = rag.retrieve

        def retrieve(query):
            res, n_sub = real_retrieve(query)
            rec["retrieved"] = [(r["faq_id"], round(r["score"], 3)) for r in res]
            rec["n_sub"] = n_sub
            return res, n_sub

        rag.retrieve = retrieve

    from fastapi.testclient import TestClient
    from app.main import app

    if args.suite == "scenarios":
        cases = [dict(id=r["scenario_id"], category=r["category"], query=r["query"])
                 for r in csv.DictReader(open(EVAL / "evaluation_test_scenarios.csv", encoding="utf-8"))]
    else:
        sys.path.insert(0, str(EVAL))
        from safety_cases import SAFETY_CASES
        cases = SAFETY_CASES
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        cases = [c for c in cases if c["id"] in only]

    rows = []
    t_build = time.perf_counter()
    with TestClient(app, raise_server_exceptions=False) as client:
        build_s = time.perf_counter() - t_build
        for c in cases:
            reset()
            t = time.perf_counter()
            r = client.post("/ask", json={"query": c["query"]})
            lat = time.perf_counter() - t
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = {"raw": r.text[:300]}
            calls = rec["llm_calls"]
            row = dict(
                scenario_id=c["id"], category=c["category"],
                query=c["query"][:200].encode("unicode_escape").decode("ascii") if not c["query"].isprintable() else c["query"][:200],
                query_len=len(c["query"]),
                http_status=r.status_code, status=body.get("status", ""), intent=body.get("intent", ""),
                response=(body.get("data") or json.dumps(body, ensure_ascii=False))[:1500].replace("\n", " "),
                latency_s=round(lat, 3), retrieval_s=round(rec["embed_s"], 3), llm_s=round(rec["llm_s"], 3),
                llm_calls=len(calls), retries=rec["retries"], errors=";".join(rec["errors"]),
                prompt_tokens=sum(x["prompt"] for x in calls), output_tokens=sum(x["output"] for x in calls),
                thinking_tokens=sum(x["thoughts"] for x in calls), total_tokens=sum(x["total"] for x in calls),
                prompt_chars=rec["prompt_chars"], retrieved="|".join(f"{i}:{s}" for i, s in rec["retrieved"]),
            )
            rows.append(row)
            print(f"{row['scenario_id']} http={row['http_status']} {row['status']}/{row['intent']} "
                  f"lat={row['latency_s']}s ret={row['retrieval_s']}s llm={row['llm_s']}s tok={row['total_tokens']} err={row['errors']}")
            time.sleep(args.pause)

    out = EVAL / "runs"
    out.mkdir(exist_ok=True)
    path = out / f"{args.suite}_{args.tag}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    meta = dict(model=args.model, index_build_s=round(build_s, 2), n=len(rows), ts=time.strftime("%Y-%m-%d %H:%M:%S"))
    json.dump(meta, open(out / f"{args.suite}_{args.tag}.meta.json", "w"))
    print("saved", path, meta)


if __name__ == "__main__":
    main()
