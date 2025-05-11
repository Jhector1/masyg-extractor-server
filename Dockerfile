############################
# 1) Builder: compile wheels
############################
FROM python:3.12-slim AS builder

# Install build tools + heavy libs for wheel compilation
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      poppler-utils \
      tesseract-ocr \
      ghostscript \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels

# Copy only requirements
COPY requirements.txt ./

# Build wheels for all dependencies *and* the spaCy model wheel
RUN pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt \
 && pip wheel --no-cache-dir --wheel-dir=/wheels en-core-web-sm==3.8.0


#########################
# 2) Final: minimal image
#########################
FROM python:3.12-slim

# Install only runtime system libraries
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      poppler-utils \
      tesseract-ocr \
      ghostscript \
 && rm -rf /var/lib/apt/lists/*

# Copy in pre-built wheels and install them (no pip cache!)
COPY --from=builder /wheels /wheels
RUN pip install --no-index --no-cache-dir /wheels/*.whl

# App code
WORKDIR /app
COPY . /app

# Env
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 5000

# Launch Uvicorn directly (model already installed)
ENTRYPOINT ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "${PORT:-5000}"]





## Use a lightweight Python image
#FROM python:3.12-slim
#
## Install system dependencies, including Ghostscript, Poppler, Tesseract OCR, SWIG, and build tools.
#RUN apt-get update && apt-get install -y \
#    ghostscript \
#    tesseract-ocr \
#    poppler-utils \
#    build-essential \
# && apt-get clean
#
## Set environment variables
#ENV PYTHONUNBUFFERED=1 \
#    PYTHONDONTWRITEBYTECODE=1
#
## Set the working directory
#WORKDIR /app
#
## Copy requirements.txt and install Python dependencies.
#COPY requirements.txt /app/
#RUN pip install --no-cache-dir -r requirements.txt
#
## Download spaCy's English model
#RUN python -m spacy download en_core_web_sm
#
## Copy the rest of your application code.
#COPY . /app/
#
## Expose the port Flask will run on (this is internal to the container).
#EXPOSE 5000
#
## Command to run the ASGI application using uvicorn.
## This uses a shell command so that the $PORT environment variable is expanded.
#CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-5000}"]
