FROM python:3.12-slim-bookworm

# Configuration will be done as root
USER root

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Update pip, copy requirements file and install dependencies
RUN pip install --no-cache-dir --upgrade pip;
WORKDIR /build
COPY services/order/requirements.txt ./requirements.txt
COPY vendor/microservice_chassis-0.5.0-py3-none-any.whl /wheels/microservice_chassis-0.5.0-py3-none-any.whl
RUN pip install --no-cache-dir --no-deps /wheels/microservice_chassis-0.5.0-py3-none-any.whl && \
    pip install --no-cache-dir -r requirements.txt

# We will be working on this folder
WORKDIR /home/pyuser/code

# Create a non root user
RUN useradd -u 1000 -d /home/pyuser -m pyuser && \
    chown -R pyuser:pyuser /home/pyuser

# Copy the entrypoint script (executed when the container starts) and add execution permissions
COPY services/order/entrypoint.sh /home/pyuser/code/entrypoint.sh
RUN chmod +x /home/pyuser/code/entrypoint.sh

# Switch user so container is run as non-root user
USER 1000

# Copy the app to the container
COPY services/order/app /home/pyuser/code/app

# Run the application
ENTRYPOINT ["./entrypoint.sh"]


