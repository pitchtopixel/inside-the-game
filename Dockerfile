# Inside the Game - match intelligence app (Azure Container Apps)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LLM_PROVIDER=mock
WORKDIR /app

# pandas is only used by the Metrica dev scripts, not the app
COPY requirements.txt .
RUN grep -v '^pandas' requirements.txt > req-app.txt && pip install --no-cache-dir -r req-app.txt

COPY *.py ./
COPY agents/ agents/
COPY static/ static/
COPY data/synthetic/ data/synthetic/
COPY data/cache/ data/cache/

EXPOSE 8600
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8600", "--speed", "20", "--mock-latency", "0"]
