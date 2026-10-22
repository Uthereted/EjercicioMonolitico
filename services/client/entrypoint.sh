#!/bin/sh

echo "Starting Client Service..."
IP=$(hostname -i)
export IP

terminate() {
  echo "Termination signal received, shutting down..."
  kill -TERM "$HYPERCORN_PID"
  wait "$HYPERCORN_PID"
  echo "Hypercorn terminated"
}

trap terminate TERM INT

hypercorn --bind 0.0.0.0:8000 app.main:app &
HYPERCORN_PID=$!
wait "$HYPERCORN_PID"
