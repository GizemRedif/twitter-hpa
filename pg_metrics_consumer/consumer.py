"""
Kafka tweets.metrics topic'ini dinleyen ve PostgreSQL'e yazan consumer.
AVRO formatındaki mesajları Confluent Schema Registry üzerinden deserialize eder.
Her 1 dakikalık pencere için airline bazlı metrik kayıtlarını tweet_metrics tablosuna yazar.
"""

import time
from datetime import datetime, timezone

from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import SerializationContext, MessageField
import psycopg2


# -------- Ayarlar ------------
KAFKA_BOOTSTRAP = "kafka:29092"
KAFKA_TOPIC = "tweets.metrics"
KAFKA_GROUP_ID = "pg-metrics-consumer-group-v3"

SCHEMA_REGISTRY_URL = "http://schema-registry:8081"

PG_HOST = "postgres"
PG_PORT = 5432
PG_DB = "twitter_metrics"
PG_USER = "airflow"
PG_PASSWORD = "airflow"

# AVRO şeması: TweetMetrics yapısı (DataStreamJob'daki metricsSchema ile aynı)
METRICS_SCHEMA = """{
  "type": "record",
  "name": "TweetMetrics",
  "namespace": "com.twitter.hpa",
  "fields": [
    {"name": "airline", "type": "string"},
    {"name": "tweet_count", "type": "long"},
    {"name": "positive_count", "type": "long"},
    {"name": "negative_count", "type": "long"},
    {"name": "neutral_count", "type": "long"},
    {"name": "positive_ratio", "type": "double"},
    {"name": "negative_ratio", "type": "double"},
    {"name": "avg_retweet", "type": "double"},
    {"name": "max_retweet", "type": "long"},
    {"name": "tweet_rate", "type": "double"},
    {"name": "window_start", "type": "string"},
    {"name": "window_end", "type": "string"}
  ]
}"""

INSERT_SQL = """
    INSERT INTO tweet_metrics
        (airline, tweet_count, positive_count, negative_count, neutral_count,
         positive_ratio, negative_ratio, avg_retweet, max_retweet,
         tweet_rate, window_start, window_end, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


# -------- Kafka + Schema Registry bağlantısı (retry ile) ------------
def create_consumer(max_retries: int = 30, retry_interval: int = 5):
    """Kafka ve Schema Registry hazır olana kadar yeniden bağlanmayı dener."""
    for attempt in range(1, max_retries + 1):
        try:
            # Schema Registry client
            sr_client = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})

            # AVRO Deserializer
            avro_deserializer = AvroDeserializer(
                schema_registry_client=sr_client,
                schema_str=METRICS_SCHEMA,
            )

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


# -------- PostgreSQL bağlantısı (retry ile) ------------
def create_pg_connection(max_retries: int = 30, retry_interval: int = 5):
    """PostgreSQL hazır olana kadar yeniden bağlanmayı dener."""
    for attempt in range(1, max_retries + 1):
        try:
            conn = psycopg2.connect(
                host=PG_HOST,
                port=PG_PORT,
                dbname=PG_DB,
                user=PG_USER,
                password=PG_PASSWORD,
            )
            conn.autocommit = True
            print(f"Connected to PostgreSQL ({PG_DB})! (attempt {attempt})")
            return conn
        except Exception as e:
            print(f"PostgreSQL not ready, retrying... ({attempt}/{max_retries}): {e}")
            time.sleep(retry_interval)

    raise RuntimeError("Could not connect to PostgreSQL after maximum retries")


# ---------------- Ana akış -----------------------------
def main():
    print("Starting PostgreSQL Metrics Consumer (AVRO)...")

    # PostgreSQL bağlantısı
    pg_conn = create_pg_connection()
    cursor = pg_conn.cursor()

    # Kafka consumer + AVRO deserializer
    consumer, avro_deserializer = create_consumer()

    saved_count = 0
    skipped_count = 0

    print("Listening for tweet metrics...")
    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue
            if msg.error():
                print(f"[ERROR] Kafka error: {msg.error()}")
                continue

            # AVRO mesajını deserialize et → Python dict
            try:
                metric = avro_deserializer(
                    msg.value(),
                    SerializationContext(KAFKA_TOPIC, MessageField.VALUE),
                )
            except Exception as e:
                print(f"[WARN] Deserialization failed: {e}")
                skipped_count += 1
                continue

            if metric is None:
                skipped_count += 1
                continue

            # PostgreSQL'e yaz
            try:
                cursor.execute(INSERT_SQL, (
                    metric["airline"],
                    metric["tweet_count"],
                    metric["positive_count"],
                    metric["negative_count"],
                    metric["neutral_count"],
                    metric["positive_ratio"],
                    metric["negative_ratio"],
                    metric["avg_retweet"],
                    metric["max_retweet"],
                    metric["tweet_rate"],
                    metric["window_start"],
                    metric["window_end"],
                    datetime.now(timezone.utc),
                ))
                saved_count += 1

                if saved_count % 50 == 0:
                    print(f"[INFO] {saved_count} metrics saved, {skipped_count} skipped")

            except Exception as e:
                print(f"[ERROR] PostgreSQL write failed: {e}")
                skipped_count += 1
                # Bağlantı kopmuş olabilir, yeniden bağlan
                try:
                    pg_conn = create_pg_connection()
                    cursor = pg_conn.cursor()
                except Exception:
                    pass

    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        consumer.close()
        cursor.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
