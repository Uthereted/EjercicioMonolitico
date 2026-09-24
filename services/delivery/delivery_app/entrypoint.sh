#!/bin/bash

echo "Service: ${SERVICE_NAME}"

IP=$(hostname -i)
export IP
echo "IP: ${IP}"

terminate() {
  echo "Termination signal received, shutting down..."
  kill -SIGTERM "$HYPERCORN_PID"
  wait "$HYPERCORN_PID"
  echo "Hypercorn has been terminated"
}

trap terminate SIGTERM SIGINT

hypercorn \
  --bind 0.0.0.0:8000 \
  app.main:app &

HYPERCORN_PID=$!

wait "$HYPERCORN_PID"