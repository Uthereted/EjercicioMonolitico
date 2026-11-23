#!/bin/sh

exec hypercorn --bind 0.0.0.0:8000 --certfile /tls/server.crt --keyfile /tls/server.key app.main:app
