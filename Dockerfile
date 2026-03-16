FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2, etc.
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy everything except what .dockerignore filters out
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Streamlit: listen on all interfaces inside container
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_PORT=8501

EXPOSE 8501

CMD ["streamlit", "run", "app/demo.py"]