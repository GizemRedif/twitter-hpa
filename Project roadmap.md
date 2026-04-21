### 1. Genel Proje Tasarımı
- Kullanılacak araçlar ve amaçları belirlendi.
- Kafka topic'leri belirlendi:

| Topic | Kullanım | Partition | Retention |
|---|---|---|---|
| tweets.raw | Producer'dan gelen ham veriler (AVRO) | 3 | 7 gün |
| tweets.alert | Flink'in filtrelediği negatif tweet'ler (AVRO) | 1 | 1 gün |
| tweets.metrics | Flink'in hesapladığı 1 dakikalık pencere metrikleri (AVRO) | 2 | 3 gün |

- Partition ve replication factor belirlendi.
- Proje süreci boyunca bazı tasarım kararları değişti (örn. Spark'ın kaynağı MongoDB yerine Parquet Data Lake olarak güncellendi).

---

### 2. Local Infrastructure 
Docker Compose (`docker-compose.yml`) ile aşağıdaki servisler kuruldu:
- Zookeeper (healthcheck ile)
- Kafka (Zookeeper'a bağımlı, healthcheck ile)
- Schema Registry (Confluent, Kafka'ya bağımlı)
- MongoDB
- PostgreSQL
- Airflow (LocalExecutor, PostgreSQL backend'i)
- Flink (JobManager + TaskManager)
- Spark (Master + Worker + Submit)
- `init-kafka` container'ı ile topic'ler otomatik oluşturuldu.

---

### 3. Kafka Data Ingestion
- Kafka Producer (`kafka_producer/producer.py`) yazıldı:
  - İlk aşamada veriler **JSON** formatında `tweets.raw` topic'ine gönderildi.
  - `Tweets.csv` CSV dosyası okundu, her satır bir Kafka mesajı olarak gönderildi.
  - Mesajlar arasına rastgele gecikme (random delay) eklendi.
- Daha sonra **Confluent Schema Registry** altyapıya eklendi ve producer **AVRO formatına** geçirildi:
  - `avro-schemas/tweets_raw.avsc` şeması oluşturuldu.
  - `AvroSerializer` ile veriler Schema Registry'e kaydedilerek AVRO olarak gönderildi.

---

### 4. Flink Speed Layer 

#### Temel Akış
- Maven ile Flink projesi oluşturuldu (`flink-tweets-stream/`).
- `DataStreamJob.java` yazıldı.
- `tweets.raw` topic'i `KafkaSource` ile tüketildi.
- Confluent Schema Registry destekli `ConfluentRegistryAvroDeserializationSchema` entegre edildi.
- `Tweet.java` POJO modeli oluşturuldu (sonradan `GenericRecord` kullanımına geçildi ve silindi).

#### Reliability
- `env.enableCheckpointing(5000)` → Her 5 saniyede bir checkpoint alınıyor.
- `env.setParallelism(2)` → Paralellik derecesi 2 olarak ayarlandı.

#### Gerçek Zamanlı Filtreleme (Alert Pipeline)
- Negatif sentiment'li tweet'ler `filter()` ile ayrıştırıldı.
- `KafkaSink` ile `tweets.alert` topic'ine AVRO formatında yazıldı. (Not: Sink için kaynak schema olan `tweetSchema` kullanılıyor, fakat `avro-schemas/tweets_alert.avsc` şeması da tasarlandı).

#### Stream Aggregation (Metrics Pipeline)
- Event-time tabanlı işleme için `WatermarkStrategy` tanımlandı:
  - `BoundedOutOfOrderness`: 5 saniyelik gecikme toleransı.
  - `withIdleness`: Boşta kalan partition'lar 30 saniye sonra kapatılıyor.
  - Timestamp kaynağı olarak tweet içindeki `tweet_created` alanı kullanıldı.
- `keyBy(airline)` → Airline'a göre gruplandırma.
- `TumblingEventTimeWindows.of(Time.minutes(1))` → 1 dakikalık tumbling window.
- `AggregateFunction` ile pencere başına şu metrikler hesaplandı:
  - `tweet_count`, `positive_count`, `negative_count`, `neutral_count`
  - `positive_ratio`, `negative_ratio`, `avg_retweet`, `max_retweet`, `tweet_rate`
  - `window_start`, `window_end`
- `TweetMetrics` POJO sınıfı yazıldı (DataStreamJob.java içerisinde). `avro-schemas/tweet_metrics.avsc` şeması oluşturuldu.
- Sonuçlar `KafkaSink` ile `tweets.metrics` topic'ine AVRO formatında yazıldı.

#### Auto-Deployment
- `flink-tweets-stream/Dockerfile` ile Maven build → fat JAR → Flink image pipeline kuruldu.
- `docker-entrypoint-override.sh` ile JobManager ayağa kalktıktan sonra job otomatik submit ediliyor.

---

### 5. Data Persistence — Python Kafka Consumer'ları

#### Alert Consumer (`mongo_alert_consumer/`)
- `tweets.alert` topic'i dinlendi.
- AVRO mesajları `AvroDeserializer` ile deserialize edildi.
- Negatif tweet'ler MongoDB `twitter_hpa.tweet_alerts` koleksiyonuna yazıldı.
- `upsert=True` ile aynı tweet_id tekrar gelirse güncelleniyor.
- Index stratejisi (`mongo-init.js` ile):
  - `tweet_id` → unique index
  - `airline` → normal index
  - `created_at` → descending index

#### Raw Data Consumer — Mimari Değişimi
- **İlk tasarım:** `mongo_raw_consumer/` ile `tweets.raw` topic'i MongoDB'ye yazılıyordu.
- **Değişim:** Parquet Data Lake mimarisine geçilerek MongoDB raw consumer kaldırıldı.
- **Yeni tasarım:** `parquet_raw_consumer/` ile `tweets.raw` verileri `data/raw_tweets/` klasörüne **Parquet** formatında yazılıyor.
  - PyArrow kullanıldı; dosyalar timestamp bazlı benzersiz isimlerle (`raw_tweets_YYYYMMDD_HHMMSS_ffffff.parquet`) düz dizine yazılıyor. (Parquet formatında veri yazmanın Python dünyasındaki en performanslı ve endüstri standardı yolu Pandas ile PyArrow'u birlikte kullanmaktır.)
  - Flush ayarları: 500 tweet birikince veya 60 saniye geçince → her batch ayrı bir Parquet dosyası.

#### Metrics Consumer (`pg_metrics_consumer/`)
- `tweets.metrics` topic'i dinlendi.
- AVRO mesajları deserialize edildi.
- Her pencere sonucu PostgreSQL `twitter_metrics.tweet_metrics` tablosuna yazıldı (Speed Layer tablosu).

---

### 6. PySpark Batch Layer 
- `spark/batch_job.py` PySpark job'u oluşturuldu.
- Spark kümesi: 1 Master + 1 Worker (1 core, 1G memory) + 1 Submit container.
- **Kaynak:** Parquet Data Lake (`data/raw_tweets/`)
- **Şema:** `retweet_count` için `LongType` (INT64) kullanıldı (PyArrow uyumluluğu).
- **Hesaplanan Batch Metrikler** (1 saatlik pencere):
  - Airline başına: `tweet_count`, `positive/negative/neutral_count`, `positive/negative_ratio`, `avg/max_retweet`, `tweet_rate`
- **Çıktılar:**
  - PostgreSQL `twitter_metrics.batch_tweet_metrics` tablosu
  - `data/batch_output/` dizinine Parquet

---

### 7. Airflow Orkestrasyonu
- Airflow DAG oluşturuldu: `airflow/dags/spark_batch_dag.py`
- Pipeline sırası:

```
Spark Batch Job → Data Quality Check
```

- **Spark Submit Task** (`BashOperator`): `docker exec spark-submit spark-submit ... batch_job.py`
- **Data Quality Check Task** (`BashOperator`): `docker exec data-quality-check python dq_check.py`
- Scheduling: `@hourly`
- Retry politikası: `retries=1`, `retry_delay=5 dakika`

> **Not:** Kafka Producer, streaming mimarisi gereği Airflow döngüsünden bağımsız çalışır. `docker-compose up kafka-producer` ile tek seferlik başlatılır; Airflow yalnızca batch (Spark) ve kalite kontrol adımlarını orkestre eder.

---

### 8. Veri Kalite Kontrolü — Data Quality Module
`data_quality/dq_check.py` scripti oluşturuldu. Kontrol edilen şeyler:

- **Parquet Data Lake:**
  - Şema doğrulama (gerekli sütunların varlığı)
  - Null kontrolü
  - Sentiment değer kontrolü (`positive`, `negative`, `neutral`)
- **PostgreSQL (Speed Layer — `tweet_metrics`):**
  - Toplam satır sayısı
  - Null kontrolü
  - Oran aralığı (0-1 arası)
- **PostgreSQL (Batch Layer — `batch_tweet_metrics`):**
  - Toplam satır sayısı
  - Null kontrolü
- **MongoDB (`tweet_alerts`):**
  - Toplam alert sayısı
  - Null kontrolü
  - Tüm dokümanların `negative` sentiment olduğunun doğrulanması

---

### 9. Bug Fixes & Konfigürasyon Düzeltmeleri

#### Spark-Submit Container Ayakta Tutma
- **Sorun:** Airflow DAG'ı `docker exec spark-submit ...` ile batch job tetikliyordu, ancak `spark-submit` container'ı ilk job bittikten sonra kapanıyordu (`restart: "no"`). Bu durumda Airflow'un saatlik tetiklemeleri `docker exec` hatası alıyordu.
- **Çözüm:** `docker-compose.yml`'deki `spark-submit` servisi düzeltildi:
  - İlk batch job çalıştıktan sonra `tail -f /dev/null` komutu ile container sürekli ayakta tutulur.
  - `restart: "no"` → `restart: unless-stopped` olarak değiştirildi.
  - Böylece Airflow her saat başı `docker exec` ile yeni batch job tetikleyebilir.

#### PostgreSQL DB Adı Tutarsızlığı Giderme
- **Sorun:** `POSTGRES_DB` env var'ı hem postgres container'ın default DB'si (`airflow`) hem de analytics servislerinin bağlandığı DB (`twitter_metrics`) için kullanılıyordu. `docker-compose.yml`'de `POSTGRES_DB: airflow` olarak hardcoded iken, `.env`'de `POSTGRES_DB=twitter_metrics` olarak tanımlıydı — karışıklığa yol açıyordu.
- **Çözüm:** Analytics DB için ayrı bir `ANALYTICS_DB` env var'ı tanımlandı:
  - `.env.example`: `POSTGRES_DB` → `ANALYTICS_DB=twitter_metrics` olarak değiştirildi.
  - `batch_job.py`, `pg_metrics_consumer/consumer.py`, `dq_check.py`: `ANALYTICS_DB` env var'ını kullanacak şekilde güncellendi.
  - `docker-compose.yml`: Postgres servisine iki DB mimarisini açıklayan yorum eklendi (Airflow metadata: `airflow`, Analytics: `twitter_metrics`).

#### Kafka Healthcheck Eklenmesi
- **Sorun:** Kafka servisinde healthcheck tanımlı değildi. `init-kafka` ve `schema-registry` gibi bağımlı servisler, Kafka broker tamamen hazır olmadan başlayabiliyordu (race condition riski).
- **Çözüm:** Kafka servisine `kafka-broker-api-versions` tabanlı healthcheck eklendi. Bağımlı servisler (`init-kafka`, `schema-registry`) `condition: service_healthy` ile güncellendi.

#### # type: ignore yorum satırları ekleme
- **Sorun:** Sanırım interpreterle alakalı bir sorun oldu ve python importları için local bilgisayarıma bakıldığından ve localde kurulu olmadığından hata çizgileri gösterildi. Ancak bu hata, projenin çalışmasına engel değil çünkü proje çalıştığında tüm eksikler docker ile gideriliyor. 
- **Çözüm:** `# type: ignore` yorum satırı ile bu nedenle hata mesajı alınan yerlerde hataların görünmesini engelledik. 

#### Data quality check düzenlendi
Data lake duplice kontrolü kaldırıldı.

---

### Notlar & Tasarım Kararları
- Producer başlangıçta JSON üretiyordu, Schema Registry eklenerek AVRO'ya geçildi.
- `Tweet.java` POJO başlangıçta kullanılıyordu; işlem karmaşıklığı artınca `GenericRecord` kullanımına geçildi ve `Tweet.java` silindi.
- Spark başlangıçta MongoDB'den okuyacak şekilde planlandı; Parquet Data Lake'e geçilmesiyle Spark'ın kaynağı değiştirildi.
- Tüm hassas bilgiler (şifreler vs.) `.env` dosyasına taşındı, `.env.example` hazırlandı, `.gitignore` güncellendi.

---

### 10. Cloud Migration — DigitalOcean Spaces (S3) Integration
Lokal dosya sistemine dayalı Data Lake yapısı, ölçeklenebilir ve bulut uyumlu (cloud-native) **DigitalOcean Spaces (S3 API)** altyapısına taşındı (Henüz droplet yok, sadece spaces):

#### Data Lake — S3 Geçişi
- **Ham Veriler (Raw tweets):** `parquet_raw_consumer` güncellendi; artık verileri lokal disk yerine doğrudan `s3://twitter-hpa-datalake/raw_tweets/` adresine yazıyor. (`s3fs` kütüphanesi entegre edildi).
- **İşlenmiş Veriler (Batch output):** Spark batch job çıktısı lokal diskten çıkarılarak `s3://twitter-hpa-datalake/batch_output/` adresine taşındı.

#### Spark Bulut Entegrasyonu
- Spark Submit/Master/Worker imajlarına S3 ile haberleşebilmesi için gerekli JAR paketleri (`hadoop-aws`, `aws-java-sdk-bundle`) eklendi.
- `batch_job.py` içerisinde S3A protokolü yapılandırması tamamlandı; erişim anahtarları (Access Key/Secret Key) güvenli bir şekilde `.env` üzerinden enjekte edildi.

#### PostgreSQL "Dependent View" Çözümü
- **Sorun:** Spark'ın default `overwrite` modu tabloyu silmeye (DROP) çalıştığı için, bu tabloya bağlı olan `unified_metrics` view'u nedeniyle hata alınıyordu.
- **Çözüm:** Spark yazma işlemi öncesinde `psycopg2` ile manuel **TRUNCATE CASCADE** komutu çalıştıran bir mantık eklendi. Spark yazma modu `append` olarak değiştirilerek veritabanı görünümlerinin bozulması engellendi.

#### Data Quality Cloud Update
- `dq_check.py` güncellendi; artık dosya varlığı, şema kontrolü ve veri analizlerini S3 üzerindeki veriler üzerinden gerçekleştiriyor.

#### Altyapı Temizliği
- `docker-compose.yml` içerisindeki Data Lake ve Batch Output klasörlerine ait yerel `volumes` tanımları kaldırılarak sunucu "stateless" (durumsuz) hale getirildi.

---

### 11. Database & Performance Optimization (Planlanıyor...)
- [ ] **MongoDB Indexing:** Alert aramaları için airline ve sentiment bazlı kompleks indekslerin oluşturulması.
- [ ] **PostgreSQL Partitioning:** Büyüyen batch verilerinin tarih bazlı parçalanması (Partitioning).

---






