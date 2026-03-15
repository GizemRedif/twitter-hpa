"""
Kafka tweets.raw topic'ini dinleyen ve ham tweet verilerini Parquet formatında diske yazan consumer.
AVRO formatındaki mesajları Confluent Schema Registry üzerinden deserialize eder.
Belirli sayıda mesaj biriktiğinde veya belirli bir süre geçtiğinde Parquet dosyası olarak flush eder.

Bu consumer, Lambda Architecture'daki "Master Dataset" (Immutable Raw Data) katmanını oluşturur.
Spark Batch Layer bu Parquet dosyalarını okuyarak toplu analiz yapar.
"""

import time
import os
from datetime import datetime, timezone

import pandas as pd # type: ignore
import pyarrow as pa # type: ignore
import pyarrow.parquet as pq # type: ignore
from confluent_kafka import Consumer # type: ignore
from confluent_kafka.schema_registry import SchemaRegistryClient # type: ignore
from confluent_kafka.schema_registry.avro import AvroDeserializer # type: ignore
from confluent_kafka.serialization import SerializationContext, MessageField # type: ignore


# -------- Ayarlar --------
KAFKA_BOOTSTRAP = "kafka:29092"
KAFKA_TOPIC = "tweets.raw"
KAFKA_GROUP_ID = "parquet-raw-consumer-group"

SCHEMA_REGISTRY_URL = "http://schema-registry:8081"

# Parquet dosyalarının yazılacağı dizin (Docker volume ile host'a bağlanır)
PARQUET_OUTPUT_DIR = "/app/data/raw_tweets"

# Flush ayarları: Kaç mesaj birikince veya kaç saniye geçince Parquet dosyası yazılsın?
FLUSH_BATCH_SIZE = 500       # 500 mesaj birikince flush et
FLUSH_INTERVAL_SEC = 60      # Veya 60 saniye geçince flush et (hangisi önce olursa)

# AVRO şeması: producer ile aynı yapı
TWEET_SCHEMA = """{
  "type": "record",
  "name": "Tweet",
  "namespace": "com.twitter.hpa",
  "fields": [
    {"name": "tweet_id", "type": "string"},
    {"name": "airline", "type": "string"},
    {"name": "airline_sentiment", "type": "string"},
    {"name": "text", "type": "string"},
    {"name": "retweet_count", "type": "int"},
    {"name": "tweet_created", "type": "string"}
  ]
}"""


# -------- Kafka + Schema Registry bağlantısı (retry ile) ------------
def create_consumer(max_retries: int = 30, retry_interval: int = 5):
    """Kafka ve Schema Registry hazır olana kadar yeniden bağlanmayı dener."""
    for attempt in range(1, max_retries + 1):
        try:
            # Schema Registry client
            sr_client = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})

            # AVRO Deserializer
            avro_deserializer = AvroDeserializer(schema_registry_client=sr_client, schema_str=TWEET_SCHEMA)

            # Kafka Consumer
            consumer = Consumer({
                "bootstrap.servers": KAFKA_BOOTSTRAP,
                "group.id": KAFKA_GROUP_ID,
                "auto.offset.reset": "earliest",
            })
            consumer.subscribe([KAFKA_TOPIC])

            print(f"Connected to Kafka & Schema Registry! (attempt {attempt})")
            return consumer, avro_deserializer

        except Exception as e:
            print(f"Not ready, retrying... ({attempt}/{max_retries}): {e}")
            time.sleep(retry_interval)

    raise RuntimeError("Could not connect after maximum retries")


# -------- Parquet'e yazma fonksiyonu --------
def flush_to_parquet(buffer: list):
    """
    Bellekteki tweet listesini bir Parquet dosyasına yazar.
    Dosya adı: raw_tweets_<timestamp>.parquet şeklinde benzersiz (unique) olur.
    Bu sayede her flush işlemi yeni bir dosya oluşturur (append-only / immutable).
    """
    if not buffer:
        return

    # Çıktı dizinini oluştur (yoksa)
    os.makedirs(PARQUET_OUTPUT_DIR, exist_ok=True)

    # Benzersiz dosya adı oluştur (timestamp bazlı)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    file_path = os.path.join(PARQUET_OUTPUT_DIR, f"raw_tweets_{timestamp}.parquet")

    df = pd.DataFrame(buffer)
    table = pa.Table.from_pandas(df)

    # Convert all large_string columns to regular string (utf8) to ensure Spark compatibility
    for i, field in enumerate(table.schema): # type: ignore
        if pa.types.is_large_string(field.type):
            table = table.set_column(i, field.name, table[i].cast(pa.string())) # type: ignore
    
    # Write to a temporary file first, then rename.
    tmp_file_path = file_path + ".tmp"
    pq.write_table(table, tmp_file_path, compression="snappy")
    os.rename(tmp_file_path, file_path)

    print(f"[INFO] Flushed {len(buffer)} tweets to {file_path}")


# ---------------- Ana akış -----------------------------
def main():
    print("Starting Parquet Raw Tweets Consumer...")
    print(f"Output directory: {PARQUET_OUTPUT_DIR}")
    print(f"Flush settings: every {FLUSH_BATCH_SIZE} messages or {FLUSH_INTERVAL_SEC} seconds")

    # Kafka consumer + AVRO deserializer
    consumer, avro_deserializer = create_consumer()

    buffer = []              # Bellekte biriken tweet listesi
    last_flush_time = time.time()
    total_saved = 0
    skipped_count = 0

    print("Listening for raw tweets...")
    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                # Mesaj gelmese bile süre dolmuşsa flush et
                if buffer and (time.time() - last_flush_time >= FLUSH_INTERVAL_SEC):
                    flush_to_parquet(buffer)
                    total_saved += len(buffer)
                    buffer = []
                    last_flush_time = time.time()
                continue

            if msg.error():
                print(f"[ERROR] Kafka error: {msg.error()}")
                continue

            # AVRO mesajını deserialize et
            try:
                tweet = avro_deserializer(
                    msg.value(),
                    SerializationContext(KAFKA_TOPIC, MessageField.VALUE),
                )
            except Exception as e:
                print(f"[WARN] Deserialization failed: {e}")
                skipped_count += 1
                continue

            if tweet is None:
                skipped_count += 1
                continue

            # Tweet'i buffer'a ekle (ingested_at alanı ile birlikte)
            tweet["ingested_at"] = datetime.now(timezone.utc).isoformat()
            buffer.append(tweet)

            # Buffer dolduğunda flush et
            if len(buffer) >= FLUSH_BATCH_SIZE:
                flush_to_parquet(buffer)
                total_saved += len(buffer)
                buffer = []
                last_flush_time = time.time()
                print(f"[INFO] Total: {total_saved} saved, {skipped_count} skipped")

    except KeyboardInterrupt:
        print("Shutting down...")
        # Kalan verileri flush et
        if buffer:
            flush_to_parquet(buffer)
            total_saved += len(buffer)
            print(f"[INFO] Final flush: {len(buffer)} tweets")
    finally:
        consumer.close()
        print(f"[INFO] Consumer closed. Total saved: {total_saved}, skipped: {skipped_count}")


if __name__ == "__main__":
    main()
