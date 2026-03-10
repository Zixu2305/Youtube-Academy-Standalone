FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements/ /app/requirements/
RUN pip install --no-cache-dir -r requirements/dev.txt

# Copy source code
COPY api/ /app/api/
COPY pipelines/ /app/pipelines/
COPY frontend/ /app/frontend/

# Expose port
EXPOSE 8000

# Run the Frontend application
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]