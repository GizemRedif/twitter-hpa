import csv
import time
import random
import os
import s3fs # type: ignore
from confluent_kafka import SerializingProducer # type: ignore
from confluent_kafka.schema_registry import SchemaRegistryClient # type: ignore
from confluent_kafka.schema_registry.avro import AvroSerializer # type: ignore

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
avro_serializer = AvroSerializer(schema_registry_client=schema_registry_client, schema_str=TWEET_SCHEMA)

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

# CSV dosyasını DO Spaces (S3) Landing Zone'dan okur, tarih sırasına dizer ve her satırı AVRO formatında Kafka'ya gönderir.
# Tarih sırasına dizme nedenimiz: Flink'in event time özelliği ile verileri doğru zaman sırasına göre işlemesini sağlamak. (bu nedenle önce all_data ile liste yapıyoruz)
# Aksi takdirde, veriler rastgele bir sırayla gelebilir ve Flink'in windowing işlemleri yanlış sonuçlar üretebilir.
# S3 bağlantı ayarları
s3 = s3fs.S3FileSystem(
    key=os.environ.get("AWS_ACCESS_KEY_ID"),
    secret=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    client_kwargs={'endpoint_url': os.environ.get("S3_ENDPOINT_URL")}
)
bucket_name = os.environ.get("S3_BUCKET_NAME", "twitter-hpa-datalake")
s3_file_path = f"s3://{bucket_name}/landing-zone/Tweets.csv"

count = 0
all_data = []

print(f"Reading CSV file from S3: {s3_file_path}...")
with s3.open(s3_file_path, 'r', encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    for row in reader:
        all_data.append(row)

# tweet_created alanına göre kronolojik sıralama (artan sırada)
# Format: "2015-02-24 11:35:52 -0800"
print(f"Sorting {len(all_data)} tweets by date...")
all_data.sort(key=lambda x: x["tweet_created"])

# Sıralanmış veriyi kafka'ya gönderiyoruz.
print("Sending sorted tweets to Kafka...")
for row in all_data:
    msg = {
        "tweet_id": row["tweet_id"],
        "airline": row["airline"],
        "airline_sentiment": row["airline_sentiment"],
        "text": row["text"],
        "retweet_count": int(row["retweet_count"] or 0),
        "tweet_created": row["tweet_created"]
    }

    # Mesajı AVRO formatında Kafka'ya gönderir.
    producer.produce(topic="tweets.raw", value=msg, on_delivery=delivery_report)
    producer.poll(0)
    count += 1
    if count % 500 == 0:
        print(f"{count} tweets sent...")
    # Gönderimler arasında rastgele gecikme (gerçek zamanlı akışı simüle eder).
    time.sleep(random.uniform(0.005, 0.02))

# Gönderilen tüm mesajların Kafka'ya ulaştığından emin olmak için kullanılır.
producer.flush()
print(f"All {count} tweets sent to Kafka.")
