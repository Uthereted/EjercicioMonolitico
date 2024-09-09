FROM python:3.12-slim-bookworm

# Configuration will be done as root
USER root

# Install some packages
RUN set -eux; \
	apt-get update; \
	apt-get install -y --no-install-recommends \
		curl \
		openssl && \
    rm -rf /var/lib/apt/lists/*

# Update pip, copy requirements file and install dependencies
RUN pip install --no-cache-dir --upgrade pip;
COPY ./monolithic/requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

# We will be working on this folder
WORKDIR /home/pyuser/code

# Create a non root user
RUN useradd -u 1000 -d /home/pyuser -m pyuser && \
    chown -R pyuser:pyuser /home/pyuser

# Switch to non-root user
USER 1000

# Some variables
ENV CERT_PATH=/home/pyuser/certs

# Create ssl certificates
RUN mkdir ${CERT_PATH} && \
    openssl req -x509 -newkey rsa:4096 -nodes \
    -out ${CERT_PATH}/cert.pem \
    -keyout ${CERT_PATH}/key.pem -days 365 -subj '/CN=localhost'

# Copy the app to the container
COPY monolithic/app /home/pyuser/code/app

# More variables
ENV BIND_ADDRESS=0.0.0.0
ENV HTTPS_PORT=8443
ENV HTTP_PORT=8000
ENV LOG_CONFIG_FILENAME=/home/pyuser/code/app/logging.ini

# Run the application
ENTRYPOINT ["hypercorn", "--certfile", "/home/pyuser/certs/cert.pem", \
			"--keyfile", "/home/pyuser/certs/key.pem", \
			"--log-config", "/home/pyuser/code/app/logging.ini", \
            "--bind", "0.0.0.0:8443", \
            "--insecure-bind", "0.0.0.0:8000", \
			"app.main:app"]

