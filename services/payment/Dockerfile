FROM python:3.12-slim-bookworm

# Configuration will be done as root
USER root

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Update pip, copy requirements file and install dependencies
RUN pip install --no-cache-dir --upgrade pip;
WORKDIR /build
COPY services/payment/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# We will be working on this folder
WORKDIR /home/pyuser/code

# Create a non root user
RUN useradd -u 1000 -d /home/pyuser -m pyuser && \
    chown -R pyuser:pyuser /home/pyuser

# Copy the entrypoint script (executed when the container starts) and add execution permissions
COPY services/payment/entrypoint.sh /home/pyuser/code/entrypoint.sh
RUN chmod +x /home/pyuser/code/entrypoint.sh

# Switch user so container is run as non-root user
USER 1000

# Copy the app to the container
COPY services/payment/app /home/pyuser/code/app

# Run the application
ENTRYPOINT ["./entrypoint.sh"]


