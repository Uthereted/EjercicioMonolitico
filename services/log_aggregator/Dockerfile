FROM python:3.12-slim-bookworm
WORKDIR /app
COPY vendor/microservice_chassis-0.6.1-py3-none-any.whl /wheels/
COPY services/log_aggregator/requirements.txt .
RUN pip install --no-cache-dir --no-deps /wheels/microservice_chassis-0.6.1-py3-none-any.whl && \
    pip install --no-cache-dir -r requirements.txt
COPY services/log_aggregator/main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--ssl-certfile", "/tls/server.crt", "--ssl-keyfile", "/tls/server.key", "--no-access-log"]
