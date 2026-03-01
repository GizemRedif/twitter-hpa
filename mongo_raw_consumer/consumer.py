"""
Kafka tweets.raw topic'ini dinleyen ve MongoDB'ye yazan consumer.
AVRO formatındaki mesajları Confluent Schema Registry üzerinden deserialize eder.
Tüm tweet'leri tweets_raw koleksiyonuna kaydeder.
"""

import time
from datetime import datetime, timezone

from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import SerializationContext, MessageField
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


# -------- Ayarlar --------
KAFKA_BOOTSTRAP = "kafka:29092"
KAFKA_TOPIC = "tweets.raw"
KAFKA_GROUP_ID = "mongo-raw-consumer-group"

SCHEMA_REGISTRY_URL = "http://schema-registry:8081"

MONGO_URI = "mongodb://mongo:27017"
MONGO_DB = "twitter_hpa"
MONGO_COLLECTION = "tweets_raw"

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
            # Schema Registry şemayı biliyor, AvroDeserializer o şemaya göre binary veriyi otomatik olarak Python dict'e çeviriyor. Ekstra parse'a gerek yok.
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


# ---------------- Ana akış -----------------------------
def main():
    print("Starting MongoDB Raw Tweets Consumer...")

    # MongoDB bağlantısı
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    collection = db[MONGO_COLLECTION]
    print(f"Connected to MongoDB ({MONGO_DB}.{MONGO_COLLECTION})")

    # Kafka consumer + AVRO deserializer
    consumer, avro_deserializer = create_consumer()

    saved_count = 0
    skipped_count = 0

    print("Listening for raw tweets...")
    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
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

            # MongoDB document'ı hazırla
            doc = {
                "tweet_id": tweet["tweet_id"],
                "airline": tweet["airline"],
                "airline_sentiment": tweet["airline_sentiment"],
                "text": tweet["text"],
                "retweet_count": tweet["retweet_count"],
                "tweet_created": tweet["tweet_created"],
                "created_at": datetime.now(timezone.utc),
            }

            try:
                # tweet_id üzerinden upsert: aynı tweet tekrar gelirse güncelle
                collection.update_one(
                    {"tweet_id": doc["tweet_id"]},
                    {"$set": doc},
                    upsert=True,
                )
                saved_count += 1

                if saved_count % 100 == 0:
                    print(f"[INFO] {saved_count} tweets saved, {skipped_count} skipped")

            except DuplicateKeyError:
                skipped_count += 1
            except Exception as e:
                print(f"[ERROR] MongoDB write failed: {e}")

    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        consumer.close()
        mongo_client.close()


if __name__ == "__main__":
    main()
