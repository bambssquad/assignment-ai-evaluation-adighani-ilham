# Data mentah run

CSV hasil `evaluation/run_eval.py` (model `gemini-3.5-flash-lite`, 2026-11-11). Satu kali jalan per kasus.

Catatan format: pada kasus SAF-11 (query 1001 karakter `a`), kolom `query` dan kutipan `input` di kolom `response` diringkas menjadi `<karakter a x200 (dipotong)>` dan `<karakter a x1001>` agar file tetap terbaca. Panjang asli ada di kolom `query_len`. Karakter kontrol pada SAF-09 ditulis sebagai teks `\x00\x07`.
