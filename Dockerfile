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

# Guard against Railway caching an unexpanded '${PORT:-8001}' in its start command
RUN printf '#!/usr/bin/env python3\nimport os, sys\nfrom uvicorn.main import main\nsys.argv = [os.environ.get("PORT", "8001") if ("PORT" in a and ("$" in a or "{" in a)) else a for a in sys.argv]\nsys.exit(main())\n' > /usr/local/bin/uvicorn && chmod +x /usr/local/bin/uvicorn

EXPOSE 8001
CMD ["python", "-m", "src.server"]
