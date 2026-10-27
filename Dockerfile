# Stage 1: Build the React + Vite frontend (copies public/animations/*.html into dist/)
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python + FastAPI + DuckDB runtime
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CJ_DB_PATH=/app/data/journeys.duckdb

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Pre-generate the deterministic DuckDB dataset during image build so container boot is fast
RUN python scripts/generate_dataset.py --db-path /app/data/journeys.duckdb --sessions-per-version 150000 --overwrite

EXPOSE 8001
CMD ["sh", "-c", "uvicorn --factory src.server:create_app --host 0.0.0.0 --port ${PORT:-8001}"]
