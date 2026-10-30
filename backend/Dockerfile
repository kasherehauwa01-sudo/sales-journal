FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Normalize source permissions only; do not open configuration, keys or storage.
RUN find /app/app /app/alembic /app/scripts -type d \( -name keys -o -name secrets -o -name storage -o -name .git -o -name .ssh \) -prune -o -type d -exec chmod 755 {} + \
    && find /app/app /app/alembic /app/scripts -type d \( -name keys -o -name secrets -o -name storage -o -name .git -o -name .ssh \) -prune -o -type f -name "*.py" -exec chmod 644 {} + \
    && chmod 644 /app/start_backend.py && useradd -r -u 10001 app && mkdir -p /data/imports && chown -R app:app /data
USER app
EXPOSE 8000
CMD ["python", "-B", "start_backend.py"]
