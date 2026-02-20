import csv
import time
import random
from confluent_kafka import SerializingProducer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer

# AVRO şeması: Kafka'ya gönderilecek tweet verisinin yapısını tanımlar.
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

# Schema Registry client: şema yönetimi için Confluent Schema Registry'ye bağlanır.
schema_registry_client = SchemaRegistryClient({"url": "http://schema-registry:8081"})

# AVRO Serializer: Python dict'i AVRO formatına çevirir ve şemayı Schema Registry'ye kaydeder.
avro_serializer = AvroSerializer(
    schema_registry_client=schema_registry_client,
    schema_str=TWEET_SCHEMA
)

# Kafka producer: AVRO formatında mesaj gönderen producer oluşturulur.
print("It connects to Kafka....")
producer = SerializingProducer({
    "bootstrap.servers": "kafka:29092",
    "value.serializer": avro_serializer
})
print("It connected to Kafka!")

# Mesaj gönderim sonucunu kontrol eden callback fonksiyonu.
def delivery_report(err, msg):
    if err is not None:
        print(f"Message delivery failed: {err}")

# CSV dosyasını okur ve her satırı AVRO formatında Kafka'ya gönderir.
count = 0
with open("Tweets.csv", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    for row in reader:
        msg = {
            "tweet_id": row["tweet_id"],
            "airline": row["airline"],
            "airline_sentiment": row["airline_sentiment"],
            "text": row["text"],
            "retweet_count": int(row["retweet_count"] or 0),
            "tweet_created": row["tweet_created"]
        }

        # Mesajı AVRO formatında Kafka'ya gönderir.
        producer.produce(
            topic="tweets.raw",
            value=msg,
            on_delivery=delivery_report
        )
        producer.poll(0)
        count += 1
        if count % 100 == 0:
            print(f"{count} tweet sent...")
        # Gönderimler arasında rastgele bekleme süresi (0.05-0.2 saniye).
        time.sleep(random.uniform(0.05, 0.2))

# Gönderilen tüm mesajların Kafka'ya ulaştığından emin olmak için kullanılır.
producer.flush()
print(f"All {count} tweets sent to Kafka.")
