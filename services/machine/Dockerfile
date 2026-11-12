FROM python:3.10-slim

WORKDIR /app

COPY services/machine/requirements.txt .
COPY vendor/microservice_chassis-0.5.0-py3-none-any.whl /wheels/microservice_chassis-0.5.0-py3-none-any.whl
RUN pip install --no-cache-dir --no-deps /wheels/microservice_chassis-0.5.0-py3-none-any.whl && \
    pip install --no-cache-dir -r requirements.txt

COPY services/machine/app ./app
COPY service_logging ./service_logging

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
