FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN addgroup --system postmortem \
    && adduser --system --ingroup postmortem --home /nonexistent postmortem

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir '.[strands]'

USER postmortem
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "postmortem_backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
