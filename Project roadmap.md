# Twitter HPA — Project Roadmap

Projenin teknik gelişim süreci, ilk tasarımdan production deployment'a kadar adım adım belgelenmiştir.

---

<details>
<summary><h3>1. Genel Proje Tasarımı</h3></summary>

- Kullanılacak araçlar ve amaçları belirlendi.
- Kafka topic'leri belirlendi:

| Topic | Kullanım | Partition | Retention |
|---|---|---|---|
| tweets.raw | Producer'dan gelen ham veriler (AVRO) | 3 | 7 gün |
| tweets.alert | Flink'in filtrelediği negatif tweet'ler (AVRO) | 1 | 1 gün |
| tweets.metrics | Flink'in hesapladığı 1 dakikalık pencere metrikleri (AVRO) | 2 | 3 gün |

- Partition ve replication factor belirlendi.
- Proje süreci boyunca bazı tasarım kararları değişti (örn. Spark'ın kaynağı MongoDB yerine Parquet Data Lake olarak güncellendi). 

**Kullanılan Depolama Sistemleri ve Amaçları:**
- **Apache Kafka:** Gerçek zamanlı veri akışını yönetmek ve servisler arası geçici tampon bölge (buffer/log) oluşturmak için kullanıldı. Belirli bir süre (retention) veriyi tutar, ardından siler. 
- **DigitalOcean Spaces (S3 - Data Lake):** Sınırsız ölçeklenebilir ve uygun maliyetli Cold Storage. Ham tweet'leri (`raw_tweets` parquet olarak), Spark batch sonuçlarını, Flink checkpoint'lerini ve Airflow loglarını barındırır. Droplet sunucusunu stateless hale getirmemizi sağlar. 
- **PostgreSQL (Serving Layer):** Hızlı ve ilişkisel SQL sorguları için Sunum Katmanı. Spark (Batch) ve Flink'in (Speed) hesapladığı karmaşık metrikleri, Dashboard'ların saniyeler içinde okuyabilmesi için vitrin görevi görür. Ayrıca Airflow'un metadata veritabanıdır. 
Spark sonuçları sadece S3'e yazılıyor olsaydı dashboard her yenilendiğinde S3'ten okuma yapacaktı ve inanılmaz yavaş olacaktı. 
Spark her çalıştığında Flink'in anlık hesapladığı ve `tweet_metrics` tablosuna yazdığı veriler otomatik silinir. Bunun amacı gereksiz depolama kullanılmamasıdır. Çünkü bir saat içerisinde sorgulama gerekirse Flink çıktıları kullanılacak, Spark hesaplama yaptıktan sonra kesin sonuçlar olduğu için Spark çıktıları kullanılacak ve artık Flink'in hesapladıklarına ihtiyacımız kalmayacak. 
Ayrıca veritabanında `batch_tweet_metrics_staging` isimli geçici bir tablo daha bulunur. Spark uzun süren hesaplamalarını doğrudan ana tabloya yazmak yerine önce bu tabloya yazar, yazma bitince tablolar anında (Atomic Swap ile) yer değiştirir. Bu sayede Spark yazarken Dashboard'larda "sıfır veri kesintisi (Zero Data Downtime)" sağlanır.
- **MongoDB:** Yüksek hızlı ve esnek şemalı (NoSQL) doküman veritabanı. Flink'in anlık yakaladığı "Negatif Duygu" alert tweet'lerini çok hızlı bir şekilde kaydetmek ve endekslemek için kullanıldı.  

#### Notlar & Tasarım Kararları
- Producer başlangıçta JSON üretiyordu, Schema Registry eklenerek AVRO'ya geçildi.
- `Tweet.java` POJO başlangıçta kullanılıyordu; işlem karmaşıklığı artınca `GenericRecord` kullanımına geçildi ve `Tweet.java` silindi.
- Spark başlangıçta MongoDB'den okuyacak şekilde planlandı; Parquet Data Lake'e geçilmesiyle Spark'ın kaynağı değiştirildi.
- Tüm hassas bilgiler (şifreler vs.) `.env` dosyasına taşındı, `.env.example` hazırlandı, `.gitignore` güncellendi.
- İnterpreterden kaynaklı hata olarak gösterilen, ama aslında kodun çalışmasında hiçbir şekilde sıkıntı oluşturmayan hatalar `# type: ignore` yorum satırı ile gizlenmiştir. Proje içinde `# type: ignore` yorum satırları bu nedenle vardır.

</details>

---

<details>
<summary><h3>2. Local Infrastructure</h3></summary>

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

</details>

---

<details>
<summary><h3>3. Kafka Data Ingestion</h3></summary>

- Kafka Producer (`kafka_producer/producer.py`) yazıldı:
  - İlk aşamada veriler **JSON** formatında `tweets.raw` topic'ine gönderildi.
  - `Tweets.csv` CSV dosyası okundu, her satır bir Kafka mesajı olarak gönderildi.
  - Mesajlar arasına rastgele gecikme (random delay) eklendi.
- Daha sonra **Confluent Schema Registry** altyapıya eklendi ve producer **AVRO formatına** geçirildi:
  - `avro-schemas/tweets_raw.avsc` şeması oluşturuldu.
  - `AvroSerializer` ile veriler Schema Registry'e kaydedilerek AVRO olarak gönderildi.

</details>

---

<details>
<summary><h3>4. Flink Speed Layer</h3></summary>

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

</details>

---

<details>
<summary><h3>5. Data Persistence — Python Kafka Consumer'ları</h3></summary>

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

</details>

---

<details>
<summary><h3>6. PySpark Batch Layer</h3></summary>

- `spark/batch_job.py` PySpark job'u oluşturuldu.
- Spark kümesi: 1 Master + 1 Worker (1 core, 1G memory) + 1 Submit container.
- **Kaynak:** Parquet Data Lake (`data/raw_tweets/`)
- **Şema:** `retweet_count` için `LongType` (INT64) kullanıldı (PyArrow uyumluluğu).
- **Hesaplanan Batch Metrikler** (1 saatlik pencere):
  - Airline başına: `tweet_count`, `positive/negative/neutral_count`, `positive/negative_ratio`, `avg/max_retweet`, `tweet_rate`
- **Çıktılar:**
  - PostgreSQL `twitter_metrics.batch_tweet_metrics` tablosu
  - `data/batch_output/` dizinine Parquet

</details>

---

<details>
<summary><h3>7. Airflow Orkestrasyonu</h3></summary>

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

</details>

---

<details>
<summary><h3>8. Veri Kalite Kontrolü — Data Quality Module</h3></summary>

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

</details>

---

<details>
<summary><h3>9. Bug Fixes & Konfigürasyon Düzeltmeleri</h3></summary>

#### Spark-Submit Container Ayakta Tutma
- **Sorun:** Airflow DAG'ı `docker exec spark-submit ...` ile batch job tetikliyordu, ancak `spark-submit` container'ı ilk job bittikten sonra kapanıyordu (`restart: "no"`). Bu durumda Airflow'un saatlik tetiklemeleri `docker exec` hatası alıyordu.
- **Çözüm:** `docker-compose.yml`'deki `spark-submit` servisi düzeltildi:
  - İlk batch job çalıştıktan sonra `tail -f /dev/null` komutu ile container sürekli ayakta tutulur.
  - `restart: "no"` → `restart: unless-stopped` olarak değiştirildi.
  - Böylece Airflow her saat başı `docker exec` ile yeni batch job tetikleyebilir.

#### PostgreSQL DB Adı Tutarsızlığı Giderme
- **Sorun:** `POSTGRES_DB` env var'ı hem postgres container'ın default DB'si (`airflow`) hem de analytics servislerinin bağlandığı DB (`twitter_metrics`) için kullanılıyordu.
- **Çözüm:** Analytics DB için ayrı bir `ANALYTICS_DB` env var'ı tanımlandı.

#### Kafka Healthcheck Eklenmesi
- **Sorun:** Kafka servisinde healthcheck tanımlı değildi. Bağımlı servisler Kafka hazır olmadan başlayabiliyordu.
- **Çözüm:** Kafka servisine `kafka-broker-api-versions` tabanlı healthcheck eklendi.

#### # type: ignore yorum satırları ekleme
- **Sorun:** Interpreterle alakalı bir sorun oldu ve python importları için local bilgisayara bakıldığından hata çizgileri gösterildi.
- **Çözüm:** `# type: ignore` yorum satırı ile bu hataların görünmesi engellendi. 

#### Data quality check düzenlendi
Data lake duplicate kontrolü kaldırıldı.

</details>

---

<details>
<summary><h3>10. Cloud Migration — DigitalOcean Spaces (S3) Integration</h3></summary>

> commit: feat: Migrate Data Lake to DigitalOcean Spaces (S3) and make architecture stateless

Lokal dosya sistemine dayalı Data Lake yapısı, ölçeklenebilir ve bulut uyumlu **DigitalOcean Spaces (S3 API)** altyapısına taşındı:

#### Data Lake — S3 Geçişi
- **Ham Veriler (Raw tweets):** `parquet_raw_consumer` güncellendi; artık verileri lokal disk yerine doğrudan `s3://twitter-hpa-datalake/raw_tweets/` adresine yazıyor.
- **İşlenmiş Veriler (Batch output):** Spark batch job çıktısı `s3://twitter-hpa-datalake/batch_output/` adresine taşındı.

#### Spark Bulut Entegrasyonu
- Spark imajlarına S3 ile haberleşebilmesi için gerekli JAR paketleri (`hadoop-aws`, `aws-java-sdk-bundle`) eklendi.
- `batch_job.py` içerisinde S3A protokolü yapılandırması tamamlandı.

#### PostgreSQL "Dependent View" Çözümü
- **Sorun:** Spark'ın default `overwrite` modu, `unified_metrics` view'u nedeniyle hata alıyordu.
- **Çözüm:** Manuel **TRUNCATE CASCADE** + Spark `append` modu.

#### Data Quality Cloud Update
- `dq_check.py` güncellendi; artık S3 üzerindeki veriler üzerinden kontrol yapıyor.

#### Altyapı Temizliği
- Yerel `volumes` tanımları kaldırılarak sunucu "stateless" hale getirildi.

> commit: feat: complete cloud-native migration with S3 landing zone, remote logging, and flink checkpoints

#### Flink Checkpoint → S3 Taşıması
- Flink'in resmi S3 plugin'i (`flink-s3-fs-hadoop`) entegre edildi.
- Checkpoint'ler `s3://twitter-hpa-datalake/flink-checkpoints/` adresine yazılıyor.
- **Sonuç:** Job çökse bile tam kaldığı Kafka offset'inden devam eder (exactly-once semantics).

#### Airflow Remote Logging → S3 Taşıması
- `apache-airflow-providers-amazon` paketi eklendi.
- Tüm DAG çalıştırma logları bulutta saklanıyor.

#### S3 Landing Zone (Raw Data Ingestion) Geçişi
- `kafka_producer` ham CSV dosyasını artık S3'ten okuyor (`s3fs` entegrasyonu).
- **Sonuç:** Droplet tamamen "Stateless" bir "Compute" birimine dönüştü.

> commit: fix: resolve Data Downtime Flaw using Atomic Table Swap

#### Data Downtime Flaw Çözümü (Atomic Table Swap)
- **Sorun:** Spark batch job'u `TRUNCATE CASCADE` sonrası append ile yazarken tablo dakikalarca boş kalıyordu.
- **Çözüm:** `batch_tweet_metrics_staging` tablosu eklendi. Spark önce staging'e yazar, sonra `ALTER TABLE RENAME` ile milisaniyeler içinde swap yapılır.

</details>

---

<details>
<summary><h3>11. Database & Performance Optimization</h3></summary>

> Commit: Add database indexing and table partitioning optimizations

#### MongoDB Compound Indexing
- Tek alanlı indexler yerine **Compound Indexler** tanımlandı:
  - `{ airline: 1, created_at: -1 }` → Airline bazlı filtreleme + zaman sıralaması
  - `{ airline: 1, airline_sentiment: 1 }` → Airline + sentiment analiz sorguları
  - `{ created_at: -1 }` → Global zaman bazlı sıralama
- **Sonuç:** Daha az index ile daha fazla sorgu paterni hızlandırıldı.

#### PostgreSQL Indexing & Range Partitioning
- **Speed Layer Indexleri (`tweet_metrics`):**
  - `idx_tweet_metrics_window_start` → `unified_metrics` view filtresi için
  - `idx_tweet_metrics_window_end` → `prune_speed_layer` DELETE sorgusu için
  - `idx_tweet_metrics_airline` → Dashboard airline filtreleme için
- **Batch Layer Range Partitioning (`batch_tweet_metrics`):**
  - Tablo `PARTITION BY RANGE (window_start)` ile aylık bölümlendirildi.
  - Staging tablosu da aynı partitioned yapıda oluşturularak Atomic Table Swap uyumluluğu sağlandı.
- **Atomic Table Swap Güncellendi:** Partitioned tablolarda swap mekanizması `DROP CASCADE` + rename + dinamik timestamp ile yeniden tasarlandı.

</details>

---

<details>
<summary><h3>12. Production Readiness & Cloud Optimization</h3></summary>

> Commit: feat: implement data persistence, backups and logging limits for cloud deployment

#### Veritabanı Kalıcılığı (Data Persistence)
- `docker-compose.yml` dosyasına `postgres_data` ve `mongo_data` named volume'ları eklendi.
- `docker compose down` komutuyla verilerin silinmesi engellendi.

#### Otomatik Bulut Yedekleme (Automated Backups)
- `backup_job.sh` scripti oluşturuldu.
- **amazon/aws-cli** konteyneri üzerinden doğrudan **DO Spaces (S3)** bucket'ına yedek yüklenir.
- 3 günden eski yerel yedekler otomatik temizlenir.

#### Kaynak ve Disk Yönetimi (Logging Limits)
- Global log limitleri: `max-size: 10m`, `max-file: 3`.
- YAML Anchor kullanılarak tüm servislerde standart log yönetimi sağlandı.

</details>

---

<details>
<summary><h3>13. Production Deployment (DigitalOcean Droplet) ✅</h3></summary>

Proje, yerel geliştirme ortamından çıkarılarak Frankfurt (FRA1) bölgesindeki bir DigitalOcean Droplet üzerinde canlıya alındı. 

#### Sunucu Yapılandırması ve Güvenlik
- **Ubuntu 24.04 LTS:** Modern ve stabil bir işletim sistemi üzerine kurulum yapıldı.
- **Swap (Sanal Bellek):** JVM tabanlı araçların (Kafka, Flink, Spark) bellek sıçramaları için 4 GB Swap yapılandırıldı.
- **Firewall (UFW):** Sadece SSH ve Web UI portları dışarıya açıldı; veritabanı portları Docker ağı içinde izole edildi.

#### Canlı Yayına Geçiş
- **S3 Landing Zone:** Veri girişi doğrudan bulut üzerinden (DO Spaces) akacak şekilde ayarlandı.
- **Stateless Architecture:** Flink checkpoint'leri, Airflow logları ve ham veri S3'te tutularak sunucunun "stateless" kalması sağlandı.
- **Continuous Execution:** Tüm pipeline ayağa kaldırıldı ve saniyeler içinde binlerce tweetin işlendiği canlı akış doğrulandı.

</details>

---
*Proje teknik hedeflerine başarıyla ulaştı ve production ortamında stabil bir şekilde çalışmaktadır.* 🚀
