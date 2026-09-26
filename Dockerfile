# Crease: one image, one origin. Stage 1 builds the React app; stage 2 runs
# FastAPI, which serves the API at /api and the built frontend everywhere else.

# ---- 1. frontend build ------------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. runtime -------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist

RUN useradd --create-home crease
USER crease

EXPOSE 8000
# Render's free tier has no release phase, so migrate on every start
# (a no-op when up to date), then serve.
CMD ["sh", "-c", "python -m backend.scripts.migrate && exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
