FROM python:3.11-slim

RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install deps first so this layer is cached when only app code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app/ ./app/
COPY static/ ./static/
COPY templates/ ./templates/
COPY create_icons.py .

# Generate placeholder PWA icons if not present
RUN python create_icons.py

RUN mkdir -p /app/data && chown appuser:appuser /app/data

USER appuser

EXPOSE 5000

# Single worker: scheduler runs in-process; 1 worker avoids double-scheduling
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:create_app()"]
