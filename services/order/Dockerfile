FROM python:3.12-slim-bookworm

# Configuration will be done as root
USER root

# Update pip, copy requirements file and install dependencies
RUN pip install --no-cache-dir --upgrade pip;
WORKDIR /build/services/order
COPY chassis /build/chassis
COPY services/order/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

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


