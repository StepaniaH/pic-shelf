# Pic Shelf — container image (self-hosted image library: Web / TUI / CLI)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PICSHELF_DATA=/data

WORKDIR /app

# Install the package (hatchling build; README is referenced by pyproject metadata)
COPY pyproject.toml README.md LICENSE ./
COPY picshelf ./picshelf
RUN pip install .

# Run as a non-root user; /data is the persistent volume mount point.
# If the host UID is not 1000, override with `user: "UID:GID"` in compose or chown ./data.
RUN useradd --uid 1000 --create-home picshelf \
 && mkdir -p /data \
 && chown picshelf:picshelf /data
USER picshelf

EXPOSE 39431

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:39431/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["picshelf", "serve", "--host", "0.0.0.0", "--port", "39431"]
