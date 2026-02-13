# Use an official lightweight Python image.
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_HOME=/app

# Set work directory
WORKDIR $APP_HOME

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY config/ ./config/
# copy other necessary dirs if they are required for runtime
# COPY data/ ./data/ # Excluded in .dockerignore but might be needed as mount point

# Create a non-root user for security
RUN addgroup --system nexusgroup && adduser --system --group nexususer
RUN chown -R nexususer:nexusgroup $APP_HOME

# Switch to non-root user
USER nexususer

# Expose port 8000
EXPOSE 8000

# Run uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
