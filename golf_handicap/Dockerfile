FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN mkdir -p /data
EXPOSE 5000
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]
