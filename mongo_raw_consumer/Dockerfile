# MongoDB Raw Consumer - Docker Image Build Tarifi (Dockerfile)
# docker-compose.yml'deki "mongo-raw-consumer" servisi bu Dockerfile'ı kullanarak image build eder.

# Python 3.11'in hafif versiyonu (slim = gereksiz paketler çıkarılmış)
FROM python:3.11-slim

# Container içindeki çalışma dizini. Tüm komutlar bu dizinde çalışır.
WORKDIR /app

# Önce sadece requirements.txt kopyalanır ve bağımlılıklar yüklenir.
# Neden ayrı adım? Docker cache: kod değişse bile bağımlılıklar tekrar yüklenmez.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Consumer script'ini container'a kopyala
COPY consumer.py .

# Container ayağa kalktığında varsayılan olarak çalıştırılacak komut
CMD ["python", "consumer.py"]
