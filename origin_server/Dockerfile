FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Garante que a pasta de storage existe dentro da imagem
RUN mkdir -p /app/storage

EXPOSE 8000

CMD ["python", "main.py"]
