FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/pyuser/code

COPY services/client/requirements.txt .
COPY vendor/microservice_chassis-0.5.0-py3-none-any.whl /wheels/microservice_chassis-0.5.0-py3-none-any.whl
RUN pip install --no-cache-dir --no-deps /wheels/microservice_chassis-0.5.0-py3-none-any.whl && \
    pip install --no-cache-dir -r requirements.txt

COPY services/client/entrypoint.sh .
COPY services/client/app ./app

EXPOSE 8000

CMD ["sh", "entrypoint.sh"]
