# Main bot image: Telegram bot + web dashboard (port 9000).
# Bakes in tesseract-ocr (ita+eng) for card OCR, gcc/g++ for any wheels that
# need to compile on linux/arm64, and libgomp1 for prophet/numpy.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Rome

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-ita \
        libgomp1 \
        tzdata \
        gcc \
        g++ \
        curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python deps in a dedicated layer — cached when only source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the source. .dockerignore keeps the build context lean.
COPY src/ ./src/
COPY run.py main.py ./

# Writable persistence dir for SQLite + future runtime caches.
RUN mkdir -p /app/data

# Healthcheck: imports a leaf utility module to verify the python install is
# coherent. Avoids importing bot/main (which would also trigger Settings()
# validation and Telegram connection setup at healthcheck time).
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "from src.utils.condition import detect_card_condition; from src.utils.expansions import get_registry; get_registry()" || exit 1

EXPOSE 9000

# Default entrypoint runs both the Telegram bot and the web dashboard.
# Override with `command: ["python", "main.py"]` in compose to run bot-only.
CMD ["python", "run.py"]
