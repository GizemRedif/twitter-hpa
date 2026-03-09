# Parquet Raw Consumer - Docker Image Build Tarifi (Dockerfile)
# Kafka'dan ham tweet verilerini okuyup Parquet formatında diske yazan consumer.
# docker-compose.yml'deki "parquet-raw-consumer" servisi bu Dockerfile'ı kullanır.

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

# Parquet çıktı dizinini oluştur
RUN mkdir -p /app/data/raw_tweets

# Container ayağa kalktığında varsayılan olarak çalıştırılacak komut
CMD ["python", "consumer.py"]
