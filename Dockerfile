FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY pyproject.toml constraints.lock README.md LICENSE ./
COPY media_uploader ./media_uploader

RUN pip install --no-cache-dir --constraint constraints.lock . \
    && mkdir -p /app/uploads /app/audit \
    && chown -R app:app /app

USER app

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; host=(os.getenv('TRUSTED_HOSTS') or 'localhost').split(',')[0]; port=os.getenv('PORT', '5000'); request=urllib.request.Request(f'http://127.0.0.1:{port}/healthz', headers={'Host': host}); urllib.request.urlopen(request, timeout=3).read()" || exit 1

CMD ["media-uploader"]
