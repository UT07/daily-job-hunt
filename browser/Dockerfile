FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium (skip Firefox/WebKit)
RUN playwright install chromium

# Copy application code
COPY *.py ./

# Entrypoint
CMD ["python", "browser_session.py"]
