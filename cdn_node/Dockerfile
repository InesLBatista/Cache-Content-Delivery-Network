FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pasta de cache — será sobreposta pelo volume persistente em runtime
RUN mkdir -p /app/cache

EXPOSE 8081

CMD ["python", "main.py"]
