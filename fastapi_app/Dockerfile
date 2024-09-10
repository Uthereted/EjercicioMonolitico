FROM python:3.12-slim-bookworm

# Configuration will be done as root
USER root

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

# Copy the app to the container
COPY monolithic/app /home/pyuser/code/app

# Run the application
ENTRYPOINT ["hypercorn", \
            "--bind", "0.0.0.0:8000", \
			"app.main:app"]

