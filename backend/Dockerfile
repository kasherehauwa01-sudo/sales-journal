FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod 644 /app/start_backend.py && useradd -r -u 10001 app && mkdir -p /data/imports && chown -R app:app /data
USER app
EXPOSE 8000
CMD ["python", "-B", "start_backend.py"]
