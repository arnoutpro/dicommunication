FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DICOMM_DATA_DIR=/app/data \
    PORT=8080

WORKDIR /app

# BusyBox ping is a static binary, so the image does not need apt-get.
# Debian mirrors often fail during `docker compose --build` (exit code 100).
COPY --from=busybox:1.37.0-uclibc /bin/busybox /usr/local/bin/ping

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

LABEL org.opencontainers.image.title="Dicommunication" \
      org.opencontainers.image.description="Low-code DICOM communication validator and PACS admin toolkit" \
      org.opencontainers.image.source="https://github.com/arnoutpro/dicommunication"
