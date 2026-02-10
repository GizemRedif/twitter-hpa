import csv
import json
import time
import random
from kafka import KafkaProducer

# Kafka'ya bağlanmak için producer oluşturulur.
print("It connects to Kafka....")
producer = KafkaProducer(
    bootstrap_servers="kafka:29092",  # Docker ağı (network) içerisindeki diğer konteynerlerden Kafka'ya erişmek için kullanılır.
    value_serializer=lambda v: json.dumps(v).encode("utf-8")
)
print("It connected to Kafka!")

# CSV dosyasını okur ve her satırı Kafka'ya gönderir.
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

        # Mesajı Kafka'ya gönderir.
        producer.send("tweets_topic", msg)
        count += 1
        if count % 100 == 0:
            print(f"{count} tweet sent...")
        # Gönderimler arasında rastgele bekleme süresi (0.05-0.2 saniye).
        time.sleep(random.uniform(0.05, 0.2))

# Gönderilen tüm mesajların Kafka'ya ulaştığından emin olmak için kullanılır.
producer.flush()
print(f"All {count} tweets sent to Kafka.")

