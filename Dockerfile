# Tank monitor: portable container image (any OS with Docker).
#   docker build -t tank-monitor .
#   docker run --rm -p 8000:8000 --env-file .env tank-monitor
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install dependencies first so rebuilds are fast when only code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY schema.sql run.py ./
COPY tools ./tools

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=3)" || exit 1

# One worker: the login rate limit is held in memory. Threads handle concurrency.
# Behind a proxy (Render, Fly, nginx) also set BEHIND_PROXY=1 and COOKIE_SECURE=1.
CMD ["sh", "-c", "exec gunicorn 'app:create_app()' --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 60 --access-logfile -"]