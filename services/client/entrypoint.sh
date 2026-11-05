#!/bin/sh

exec hypercorn --bind 0.0.0.0:8000 app.main:app
