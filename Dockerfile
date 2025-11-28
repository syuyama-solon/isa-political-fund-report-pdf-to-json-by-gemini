FROM python:3.11-slim

# poppler-utils (pdf2image dependency)
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Cloud Run uses PORT environment variable
ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 main:app
