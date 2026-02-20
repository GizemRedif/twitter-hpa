"""
Kafka tweets.alert topic'ini dinleyen ve MongoDB'ye yazan consumer.
Flink'in GenericRecord.toString() çıktısını parse ederek alanları çıkarır.
"""

import json
import re
import time
from datetime import datetime, timezone

from kafka import KafkaConsumer
from kafka.errors import NoBrokerAvailable
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


# ── Ayarlar ──────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP = "kafka:29092"
KAFKA_TOPIC = "tweets.alert"
KAFKA_GROUP_ID = "mongo-alert-consumer-group"

MONGO_URI = "mongodb://mongo:27017"
MONGO_DB = "twitter_hpa"
MONGO_COLLECTION = "tweet_alerts"


# ── Flink GenericRecord.toString() çıktısını parse et ───────────────────────
def parse_alert_message(raw: str) -> dict | None:
    """
    Flink'ten gelen ham string mesajı (GenericRecord.toString()) parse edip bir Python dict'e çevirir.
    Bu çıktı genelde JSON'a benzer ama her zaman geçerli JSON olmayabilir.
    Bu yüzden iki aşamalı bir parse stratejisi uygulanır:
      1) JSON parse — hızlı ve güvenilir, çoğu durumda çalışır
      2) Regex parse — JSON başarısız olursa, metin içindeki alanları regex ile çıkarır
    """

    # ── 1. AŞAMA: JSON parse ────────────────────────────────────────────────
    # GenericRecord.toString() çıktısı çoğunlukla geçerli JSON formatındadır.
    # json.loads() ile doğrudan parse etmeyi deneriz
    try:
        data = json.loads(raw)

        # JSON başarılı oldu, alanları dict olarak döndür.
        return {
            "tweet_id": str(data.get("tweet_id", "")),
            "airline": str(data.get("airline", "")),
            "airline_sentiment": str(data.get("airline_sentiment", "")),
            "text": str(data.get("text", "")),
            "retweet_count": int(data.get("retweet_count", 0)),
            "tweet_created": str(data.get("tweet_created", "")),
        }
    except (json.JSONDecodeError, TypeError):
        # JSON parse başarısız oldu (örn. tweet metninde tırnak veya özel karakter var).
        # 2. aşamaya geç.
        pass

    # ── 2. AŞAMA: Regex (düzenli ifade) ile parse ───────────────────────────
    # JSON çalışmazsa, metni regex ile tarayarak "key": "value" çiftlerini buluruz.
    # Bu yöntem daha esnek ama daha yavaştır.
    try:
        pattern = r'"(\w+)":\s*(?:"((?:[^"\\]|\\.)*)"|(\d+))'
        raw_matches = re.findall(pattern, raw)

        # Her eşleşmede grup2 (string) veya grup3 (sayı) dolu olur
        matches = {key: (str_val if str_val else num_val) for key, str_val, num_val in raw_matches}

        # tweet_id alanı varsa geçerli bir tweet mesajı olarak kabul et
        if "tweet_id" in matches:
            return {
                "tweet_id": matches.get("tweet_id", ""),
                "airline": matches.get("airline", ""),
                "airline_sentiment": matches.get("airline_sentiment", ""),
                "text": matches.get("text", ""),
                "retweet_count": int(matches.get("retweet_count", 0)),
                "tweet_created": matches.get("tweet_created", ""),
            }
    except Exception:
        pass

    # ── Her iki aşama da başarısız oldu ──────────────────────────────────────
    # Mesaj parse edilemedi, uyarı logla ve None döndür (bu mesaj atlanacak).
    print(f"[WARN] Could not parse message: {raw[:200]}")
    return None


# ── Kafka bağlantısı (retry ile) ────────────────────────────────────────────
def create_kafka_consumer(max_retries: int = 30, retry_interval: int = 5) -> KafkaConsumer:
    """Kafka broker hazır olana kadar yeniden bağlanmayı dener."""
    for attempt in range(1, max_retries + 1):
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id=KAFKA_GROUP_ID,
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                value_deserializer=lambda m: m.decode("utf-8"),
            )
            print(f"Connected to Kafka! (attempt {attempt})")
            return consumer
        except NoBrokerAvailable:
            print(f"Kafka not ready, retrying... ({attempt}/{max_retries})")
            time.sleep(retry_interval)

    raise RuntimeError("Could not connect to Kafka after maximum retries")


# ── Ana akış ─────────────────────────────────────────────────────────────────
def main():
    print("Starting MongoDB Alert Consumer...")

    # MongoDB bağlantısı
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    collection = db[MONGO_COLLECTION]
    print(f"Connected to MongoDB ({MONGO_DB}.{MONGO_COLLECTION})")

    # Kafka consumer
    consumer = create_kafka_consumer()

    saved_count = 0
    skipped_count = 0

    print("Listening for tweet alerts...")
    try:
        for message in consumer:
            raw_value = message.value
            parsed = parse_alert_message(raw_value)

            if parsed is None:
                skipped_count += 1
                continue

            # MongoDB'ye yazılma zamanı ekle
            parsed["created_at"] = datetime.now(timezone.utc)

            try:
                # tweet_id üzerinden upsert: aynı tweet tekrar gelirse güncelle
                collection.update_one(
                    {"tweet_id": parsed["tweet_id"]},
                    {"$set": parsed},
                    upsert=True,
                )
                saved_count += 1

                if saved_count % 50 == 0:
                    print(f"[INFO] {saved_count} alerts saved, {skipped_count} skipped")

            except DuplicateKeyError:
                skipped_count += 1
            except Exception as e:
                print(f"[ERROR] MongoDB write failed: {e}")

    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        # Kafka consumer ve MongoDB bağlantılarını düzgün kapat
        consumer.close()
        mongo_client.close()


if __name__ == "__main__":
    main()
