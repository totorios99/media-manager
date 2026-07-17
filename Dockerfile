FROM python:3.12-slim

# handbrake-cli: Debian build — check DV/libdovi support before heavy encodes;
# override HANDBRAKE_CLI if you mount a dovi-capable build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    handbrake-cli mkvtoolnix ffmpeg tmux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi uvicorn

COPY app.py commands.py jobs.py scan.py ./
COPY static/ static/

ENV MM_NO_SYSTEMD=1 \
    MM_DB_PATH=/app/data/media.db \
    MM_LOG_DIR=/app/data/logs \
    HANDBRAKE_CLI=HandBrakeCLI \
    MEDIA_ROOT=/media/hdd1/Movies

EXPOSE 8500
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8500"]
