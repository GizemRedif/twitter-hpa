# Docker konteynerın ayağa kalktığı anda "ilk çalıştırılan talimat listesi" gibidir.
#!/bin/bash
# =============================================================
# Flink JobManager için özel başlatma scripti
# =============================================================
# Bu script, JobManager'ın başlatılmasından sonra Flink job'unu
# otomatik olarak submit eder. Böylece "docker compose up" ile
# tüm sistem tek komutla ayağa kalkar.
#
# Akış:
#   1. Flink JobManager normal şekilde başlatılır (arka planda)
#   2. JobManager'ın REST API'si hazır olana kadar beklenir
#   3. Job JAR'ı submit edilir (flink run komutu ile)
#   4. Script foreground'da JobManager sürecini bekler
# =============================================================


# ---------- 1. Flink JobManager'ı arka planda başlat ----------
# Orijinal Flink entrypoint'ini çağırarak JobManager'ı başlatıyoruz.
# "&" ile arka plana gönderiyoruz ki script devam edebilsin.
/docker-entrypoint.sh jobmanager & 

# Arka plandaki JobManager sürecinin PID'ini kaydediyoruz.
# Script sonunda bu PID'e "wait" yaparak container'ın kapanmamasını sağlıyoruz.
FLINK_PID=$!


# ---------- 2. JobManager REST API hazır olana kadar bekle ----------
# JobManager ~10-30 saniyede ayağa kalkar. REST API (8081 portu)
# hazır olmadan job submit edilemez. Bu yüzden döngü ile bekliyoruz.
echo "[AUTO-SUBMIT] Waiting for JobManager REST API..."
until curl -sf http://localhost:8081/overview > /dev/null 2>&1; do
    sleep 2
done
echo "[AUTO-SUBMIT] JobManager REST API is ready!"


# ---------- 3. Kafka topic'lerinin oluşturulmasını bekle ----------
# init-kafka container'ı topic'leri asenkron olarak oluşturur.
# Flink job'u "tweets.raw" topic'ine bağlanmaya çalıştığında topic yoksa hata alır.
# Bu yüzden topic oluşturulana kadar döngü ile bekliyoruz.
echo "[AUTO-SUBMIT] Waiting for Kafka topics to be created..."
MAX_TOPIC_WAIT=60
TOPIC_WAIT=0
while [ $TOPIC_WAIT -lt $MAX_TOPIC_WAIT ]; do
    # Schema Registry'nin hazır olup olmadığını kontrol et (Flink AVRO deserialize için gerekli)
    SR_READY=$(curl -sf http://schema-registry:8081/subjects 2>/dev/null)
    if [ $? -eq 0 ]; then
        echo "[AUTO-SUBMIT] Schema Registry is ready!"
        break
    fi
    echo "[AUTO-SUBMIT] Waiting for Schema Registry... ($TOPIC_WAIT/$MAX_TOPIC_WAIT)"
    sleep 3
    TOPIC_WAIT=$((TOPIC_WAIT + 3))
done

# Topic'lerin oluşturulması için ek bekleme (init-kafka'nın işini bitirmesi)
echo "[AUTO-SUBMIT] Waiting additional time for topic creation..."
sleep 15


# ---------- 4. Flink job'unu submit et ----------
# /opt/flink/usrlib/flink-tweets-stream.jar dosyası Dockerfile'da
# build aşamasında bu dizine kopyalanmıştır.
echo "[AUTO-SUBMIT] Submitting Flink job..."
/opt/flink/bin/flink run -d /opt/flink/usrlib/flink-tweets-stream.jar

# Submit sonucunu kontrol et
if [ $? -eq 0 ]; then
    echo "[AUTO-SUBMIT] Flink job submitted successfully!"
else
    echo "[AUTO-SUBMIT] Failed to submit Flink job. Check logs."
fi


# ---------- 5. JobManager sürecini foreground'da bekle ----------
# Container'ın kapanmaması için arka plandaki Flink sürecini bekliyoruz.
# Bu olmazsa script biter ve container kapanır.
wait $FLINK_PID
