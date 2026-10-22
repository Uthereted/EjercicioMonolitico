FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/pyuser/code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY entrypoint.sh .
COPY app ./app

EXPOSE 8000

CMD ["sh", "entrypoint.sh"]
