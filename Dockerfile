# Use a lightweight Python image
FROM python:3.12-slim

# Install system dependencies, including Poppler
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && apt-get clean

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Set the working directory
WORKDIR /app

# Copy requirements.txt
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Expose the port Flask will run on
EXPOSE 5000

# Command to run the Flask application
CMD ["python", "server.py"]
