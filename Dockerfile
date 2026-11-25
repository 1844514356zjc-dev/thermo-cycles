# syntax=docker/dockerfile:1
# Keep both stages on the same Python/Debian ABI; no host Python is required.
FROM python:3.10-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

# Full transitive dependency lock, including package artifact SHA-256 hashes.
COPY requirements.txt ./requirements.txt
COPY requirements/docker.txt requirements/docker.txt
RUN python -m pip install --prefix=/install --require-hashes \
    --only-binary=:all: -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --prefix=/install --no-deps .

FROM python:3.10-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1
WORKDIR /app

RUN useradd --create-home --uid 10001 --user-group thermo
COPY --from=builder /install /usr/local
COPY --chown=thermo:thermo src ./src
COPY --chown=thermo:thermo .streamlit ./.streamlit
COPY --chown=thermo:thermo README.md LICENSE ./
USER thermo

EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).read()"]

CMD ["python", "-m", "streamlit", "run", "src/ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
