#!/bin/bash

echo "Starting Client Service..."
IP=$(hostname -i)
export IP

terminate() {
  echo "Termination signal received, shutting down..."
  kill -SIGTERM "$HYPERCORN_PID"
  wait "$HYPERCORN_PID"
  echo "Hypercorn terminated"
}

trap terminate SIGTERM SIGINT

hypercorn --bind 0.0.0.0:8000 app.main:app &
HYPERCORN_PID=$!
wait "$HYPERCORN_PID"