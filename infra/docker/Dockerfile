# Usa una imagen base ligera
FROM python:3.11-slim

# Avoid Python .pyc files and allow real time logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system needed dependencies
RUN apt-get update && apt-get install -y curl
#    build-essential \
#   curl \
#   software-properties-common \
#   && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose Streamlit port
EXPOSE 8501

# Run Streamlit
CMD ["streamlit", "run", "app/ui/main.py", "--server.address=0.0.0.0", "--server.port=8501"]
#ENTRYPOINT ["streamlit", "run", "app/ui/main.py", "--server.port=8501", "--server.address=0.0.0.0"]