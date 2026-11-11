FROM python:3.11-slim

WORKDIR /app

# Salin requirements dulu agar layer instalasi dependency di-cache selama requirements tidak berubah
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Salin source code dan data (API key TIDAK disalin: diberikan saat runtime lewat environment variable)
COPY app/ app/
COPY data/ data/

EXPOSE 8000

# 0.0.0.0 agar endpoint dapat diakses dari luar container
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
