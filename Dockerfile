# Use official lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir httpx google-generativeai

# Copy all project files
COPY . .

# Expose port
EXPOSE 8080

# Set Python path so imports like `from app...` work
ENV PYTHONPATH=/app/backend

# Command to run the application (Cloud Run requires port 8080 by default)
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]

#tried this for deploying on google cloud but couldn't due to regional payment billing issues