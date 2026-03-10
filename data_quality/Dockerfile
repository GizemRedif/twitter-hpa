FROM python:3.11-slim

WORKDIR /app

# Bağımlılıkları yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama dosyasını kopyala
COPY dq_check.py .

# Container'ı ayakta tut — Airflow "docker exec" ile dq_check.py'yi tetikleyecek.
# Doğrudan çalıştırmak için: docker exec data-quality-check python dq_check.py
CMD ["sleep", "infinity"]

# Eğer iş yaptıktan sonra container'i kapatsaydık, Airflow sonradan "kontrol yap" dediğinde, container zaten kapalı olurdu → hata!
# Bu yüzden container'ı ayakta tutuyoruz.