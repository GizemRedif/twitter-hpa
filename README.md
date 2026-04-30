## Twitter HPA (High Performance Analysis) - Lambda Architecture + Cloud Deployment

Bu proje, Twitter verilerini gerçek zamanlı ve toplu olarak işlemek amacıyla **Lambda Mimari** prensipleri doğrultusunda geliştirilmiştir.

#### Proje Durumu: 🟢 Canlıda (Production)
| ⚡ **Speed Layer** 
| 🗄️ **Serving Layer** 
| 📦 **Batch Layer**
| ☁️ **Cloud Deployment**
| 🚀 **DigitalOcean Droplet**

---
### Architecture Flow
![Architecture - Data Flow Diagram](Architecture%20-%20Data%20Flow%20Diagram.jpeg)

---
### Technology Stack

| Category | Technology |
|----------|-----------|
| Message Queue | Apache Kafka |
| Stream Processing | Apache Flink (Java, Maven) |
| Batch Processing | Apache Spark (PySpark) |
| Workflow Orchestration | Apache Airflow |
| Schema Management | Confluent Schema Registry (Avro) |
| Data Storage (NoSQL) | MongoDB 7.0 |
| Data Storage (RDBMS) | PostgreSQL 15 |
| Data Storage (Data Lake) | Apache Parquet (Snappy compression) |
| Cloud Storage | DigitalOcean Spaces (S3 Compatible) |
| Containerization | Docker & Docker Compose |
| Server (Production) | DigitalOcean Droplet (Ubuntu 24.04 LTS) |

---

<details>
<summary><h3>📋 Pipeline Details</h3></summary>

#### 1. Data Ingestion - S3 Landing Zone
- **Producer:** Python tabanlı `kafka-producer`, ham tweet verilerini doğrudan buluttan (**DO Spaces**) `s3fs` kullanarak okur.
- **Serialization:** Veriler, Confluent Schema Registry üzerindeki **Avro** şemasına göre serialize edilerek binary formatta iletilir.
- **Kafka Topic:** Mesajlar `tweets.raw` topic'ine asenkron olarak gönderilir.

#### 2. Schema Management
- Tüm bileşenler (Producer, Flink, Consumer) merkezi **Schema Registry**'yi kullanır. Bu yaklaşım veri tutarlılığını sağlar ve şema evrimini (schema evolution) kolaylaştırır.

#### 3. Speed Layer — Stream Processing
- **Flink Job:** `tweets.raw` topic'ini kesintisiz dinler. Flink'in durum verileri **(Checkpoints)** doğrudan **DO Spaces (S3)** üzerine yazılır.
- **Event Time & Watermarks:** Tweetlerin orijinal zaman damgaları kullanılır; geç gelen veriler için 5 saniyelik Watermark toleransı uygulanır.
- **Windowing:** Veriler havayolu bazlı gruplanır (`keyBy`) ve 1 dakikalık `TumblingEventTimeWindows` pencerelerinde toplanır.
- **Outputs:** "Negative" tweetler → `tweets.alert`, dakikalık istatistikler → `tweets.metrics`
 
#### 4. Batch Layer & Cloud Data Lake
- **Parquet Raw Consumer:** Ham tweetleri **Parquet** formatında doğrudan **DO Spaces (S3)** Data Lake'ine yazar (stateless mimari).
- **PySpark Job:** S3 üzerindeki ham verileri periyodik olarak okur ve 1 saatlik pencereler bazında toplu analizler üretir.
- **Airflow DAG:** PySpark işini zamanlanmış (scheduled) biçimde tetikler. Görev logları **DO Spaces'e (S3 Remote Logging)** yazılır.

#### 5. Serving Layer — Storage & Consumption
- **MongoDB:** Kritik uyarılar (`tweet_alerts`) saklanır.
- **PostgreSQL:** Analitik metrikler hem Speed Layer'dan (`tweet_metrics`) hem Batch Layer'dan (`batch_tweet_metrics`) gelir.
- **Unified View:** `unified_metrics` view'ı, batch ve real-time verileri otomatik birleştirir.
- **Lambda Loop (Pruning):** Batch job tamamlandığında, Speed Layer'daki eski verileri temizler.

#### 6. Automation & Infrastructure as Code
- **Docker Compose:** Tüm ekosistem tek komutla ayağa kalkar.
- **Self-Healing Startup:** `docker-entrypoint-override.sh` scripti; servislerin hazır olmasını bekler ve Flink job'unu otomatik olarak cluster'a teslim eder.

</details>

---

<details>
<summary><h3>🚀 Kurulum ve Çalıştırma</h3></summary>

> Bu proje herhangi bir bilgisayarda (Windows, Mac, Linux) Docker ile çalıştırılabilir. DigitalOcean Droplet'e deploy etmek **zorunlu değildir**.

#### Ön Gereksinimler
- Docker & Docker Compose
- **S3 uyumlu bulut depolama hesabı:** [DigitalOcean Spaces](https://www.digitalocean.com/products/spaces), [AWS S3](https://aws.amazon.com/s3/) veya herhangi bir S3 uyumlu servis.

#### 1. S3 Bucket Oluşturma ve Erişim Anahtarları

Bu proje, veri okuma/yazma işlemlerini bulut üzerinden (S3) yapar.

**DigitalOcean Spaces kullanıyorsanız:**
1. [DigitalOcean](https://cloud.digitalocean.com/) panelinden bir Space oluşturun (örn: `twitter-hpa-datalake`).
2. Bölge olarak **Frankfurt (FRA1)** seçin.
3. **API** → **Spaces Keys** kısmından `Access Key` ve `Secret Key` oluşturun.

#### 2. S3 Landing Zone Hazırlığı
- Bucket içinde `landing-zone` adında bir klasör açın.
- `Tweets.csv` dosyasını bu klasörün içine yükleyin.

#### 3. Environment Variables (.env)
`.env.example` dosyasını `.env` olarak kopyalayıp S3 anahtarlarınızı girin:
```env
AWS_ACCESS_KEY_ID=buraya_access_key
AWS_SECRET_ACCESS_KEY=buraya_secret_key
AWS_REGION=fra1
S3_ENDPOINT_URL=https://fra1.digitaloceanspaces.com
S3_BUCKET_NAME=twitter-hpa-datalake
```

#### 4. Sistemi Başlatma
```bash
docker compose up --build -d
```

#### Production Deployment (Opsiyonel — DigitalOcean Droplet)

👉 **[DEPLOYMENT.md](DEPLOYMENT.md)** — Droplet üzerinde canlıya geçiş rehberi.

</details>

---

<details>
<summary><h3>🔗 Service Endpoints & Data Lake Paths</h3></summary>

| Service | Address | Details |
|--------|-------|-------|
| **Airflow UI** | `localhost:8081` | Creds: `.env` |
| **Flink Dashboard** | `localhost:8082` | — |
| **Schema Registry** | `localhost:8083` | Endpoint: `/subjects` |
| **Spark Master UI** | `localhost:8084` | — |
| **PostgreSQL** | `localhost:5432` | DB: `twitter_metrics` |
| **MongoDB** | `localhost:27018` | DB: `twitter_hpa` |

#### Cloud Data Lake (S3 Paths)
| Path | Description |
|-----|----------|
| `s3://.../landing-zone/` | Input (Tweets.csv) |
| `s3://.../raw_tweets/` | Raw Data (Parquet) |
| `s3://.../batch_output/` | Batch Output (Archive) |
| `s3://.../flink-checkpoints/` | Flink Checkpoints |
| `s3://.../airflow-logs/` | Airflow Logs |
| `s3://.../backups/` | DB Backups |

</details>

---

<details>
<summary><h3>📁 Project Structure</h3></summary>

```
twitter-hpa/
├── airflow/                  # Airflow DAGs & Dockerfile
├── avro-schemas/             # Avro Schema Definitions
├── data_quality/             # Data quality control scripts
├── flink-tweets-stream/      # Flink Java Project (Maven)
├── kafka_producer/           # Kafka Producer (Python)
├── mongo_alert_consumer/     # MongoDB Alert Consumer (Python)
├── parquet_raw_consumer/     # Parquet Raw Data Consumer (Python)
├── pg_metrics_consumer/      # PostgreSQL Metrics Consumer (Python)
├── spark/                    # PySpark Batch Job
├── docker-compose.yml        # Service Orchestration
├── postgres-init.sql         # PostgreSQL Schema & Tables
├── mongo-init.js             # MongoDB Index & Collections
├── backup_job.sh             # Automated S3 Backup Script
├── DEPLOYMENT.md             # Production Deployment Guide
├── .env.example              # Env template
└── Project roadmap.md        # Technical roadmap
```

</details>

---

<details>
<summary><h3>📌 Notes & Configuration</h3></summary>
 
> **Not:** Bu projede temel hedef, elimdeki veri setine göre optimizasyon yapmak değil; gerçek dünya standartlarında bir sistem mimarisi kurmayı öğrenmektir. Bu nedenle şu anda projemde over-provisioned kaynaklar bulunmaktadır (Partition sayıları, Flink parallelism ayarı gibi). 

#### ⚙️ Kritik Ayarlar

| Component | Setting | Value |
|-----------|---------|-------|
| **Flink** | Parallelism | `2` |
| **Flink** | Checkpoint Interval | `5000 ms` (5 saniye) |
| **Flink** | Watermark Tolerance | `5 saniye` (BoundedOutOfOrderness) |
| **Flink** | Idle Partition Timeout | `30 saniye` |
| **Flink** | Window Size | `1 dakika` (TumblingEventTimeWindows) |
| **Kafka** | `tweets.raw` Partitions / Retention | `3` / `7 gün` |
| **Kafka** | `tweets.alert` Partitions / Retention | `1` / `1 gün` |
| **Kafka** | `tweets.metrics` Partitions / Retention | `2` / `3 gün` |
| **Airflow** | Batch Schedule | `@hourly` |
| **Parquet Consumer** | Flush Batch Size | `500 mesaj` |
| **Parquet Consumer** | Flush Interval | `60 saniye` |
| **Docker Logging** | Max Log Size / File Count | `10 MB` / `3 dosya` |
| **Production** | Minimum RAM / Swap | `8 GB` / `4 GB` |

</details>