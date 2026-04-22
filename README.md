## Twitter HPA (High Performance Analysis) - Lambda Architecture + Cloud Deployment

Bu proje, Twitter verilerini gerçek zamanlı ve toplu olarak işlemek amacıyla **Lambda Mimari** prensipleri doğrultusunda geliştirilmiştir.

### Proje Durumu
| ⚡ **Speed Layer** 
| 🗄️ **Serving Layer** 
| 📦 **Batch Layer**
| ☁️ **Cloud Deployment** 

> **Not:** Bu projede temel hedef, elimdeki veri setine göre optimizasyon yapmak değil; gerçek dünya standartlarında bir sistem mimarisi kurmayı öğrenmektir. Bu nedenle şu anda projemde fazla tahsis edilmiş (over-provisioned) kaynaklar bulunmaktadır (Partition sayıları, Flink parallelism ayarı gibi). 
---

### Teknoloji Yığını

| Kategori | Teknoloji |
|----------|-----------|
| Mesaj Kuyruğu | Apache Kafka |
| Anlık İşleme | Apache Flink (Java, Maven) |
| Toplu İşleme | Apache Spark (PySpark) |
| İş Orkestrasyonu | Apache Airflow |
| Şema Yönetimi | Confluent Schema Registry (Avro) |
| Veri Depolama (NoSQL) | MongoDB 7.0 |
| Veri Depolama (RDBMS) | PostgreSQL 15 |
| Veri Depolama (Data Lake) | Apache Parquet (Snappy ile sıkıştırma) |
| Bulut Depolama (Cloud Storage) | DigitalOcean Spaces (S3 Uyumlu) |
| Konteynerleştirme | Docker & Docker Compose |

---

### Mimari Akış
![Architecture - Data Flow Diagram](Architecture%20-%20Data%20Flow%20Diagram.jpeg)

#### 1. Veri Üretimi ve Entegrasyon (Ingestion) - S3 Landing Zone
- **Producer:** Python tabanlı `kafka-producer`, ham tweet verilerini lokal disk yerine doğrudan buluttan (**DO Spaces - s3://.../landing-zone/Tweets.csv**) `s3fs` kullanarak okur.
- **Serialization:** Veriler, Confluent Schema Registry üzerindeki **Avro** şemasına göre serialize edilerek binary formatta iletilir. (Verinin ağ üzerinde en az yer kaplamasını sağlamak için)
- **Kafka Topic:** Hazırlanan mesajlar `tweets.raw` topic'ine asenkron olarak gönderilir.

#### 2. Şema Yönetimi (Schema Management)
- Tüm bileşenler (Producer, Flink, Consumer) merkezi **Schema Registry**'yi kullanır. Bu yaklaşım veri tutarlılığını sağlar ve şema evrimini (schema evolution) kolaylaştırır.

#### 3. Hız Katmanı — Anlık İşleme (Speed Layer)
- **Flink Job:** `tweets.raw` topic'ini kesintisiz dinler. Flink'in durum (state) verileri ve yedeği **(Checkpoints)** veri kaybını sıfıra indirmek için doğrudan **DO Spaces (S3)** üzerine yazılır.
- **Event Time & Watermarks:** Tweetlerin orijinal zaman damgaları (`tweet_created`) kullanılır; geç gelen veriler için 5 saniyelik Watermark toleransı uygulanır.
- **Pencereleme (Windowing):** Veriler havayolu bazlı gruplanır (`keyBy`) ve 1 dakikalık `TumblingEventTimeWindows` pencerelerinde toplanır.
- **Çıktılar:**
  - "Negative" duygu etiketli tweetler → `tweets.alert` topic'i
  - Dakikalık istatistikler (tweet sayısı, ortalama retweet vb.) → `tweets.metrics` topic'i
 
#### 4. Toplu İşleme Katmanı (Batch Layer) & Cloud Data Lake
- **Parquet Raw Consumer:** Kafka'daki `tweets.raw` topic'ini dinler ve ham tweetleri **Parquet** formatında doğrudan **DO Spaces (S3)** Data Lake'ine yazar. (Konteyner diski kullanılmaz, mimari tamamen stateless'tır).
- **PySpark Job:** `spark/batch_job.py`, S3 üzerindeki (Parquet formatındaki) ham tweet verilerini periyodik olarak okur ve 1 saatlik pencereler bazında toplu analizler üretir.
- **Airflow DAG:** `airflow/dags/` altındaki DAG, PySpark işini zamanlanmış (scheduled) biçimde tetikler. Airflow görev logları sunucu diski dolmasın diye **DO Spaces'e (S3 Remote Logging)** yazılır.

#### 5. Sunum Katmanı — Depolama ve Tüketim (Serving Layer)
- **Consumers:** Python consumer'ları Kafka'dan gelen sonuçları kalıcı depolara yazar:
  - **MongoDB:** Kritik uyarılar (`tweet_alerts`) saklanır.
  - **PostgreSQL:** Analitik metrikler hem hız katmanından (`tweet_metrics`) hem de toplu işleme katmanından (`batch_tweet_metrics`) gelen verilerle burada saklanır.
- **Unified View:** PostgreSQL'deki `unified_metrics` view'ı, batch ve real-time verileri otomatik olarak birleştirir.
- **Lambda Loop (Pruning):** Batch job başarıyla tamamlandığında, `tweet_metrics` tablosundaki eski (redundant) verileri temizleyerek "re-computation" döngüsünü tamamlar.
- **Spark Output:** PySpark job sonuçları hem PostgreSQL'e yazar hem de uzun süreli saklama için **Parquet** formatında bulut veri gölüne (DO Spaces) arşivler.

#### 6. Otomasyon (Infrastructure as Code)
- **Docker Compose:** Tüm ekosistem tek komutla ayağa kalkar.
- **Self-Healing Startup:** `docker-entrypoint-override.sh` scripti; servislerin hazır olmasını, topic'lerin oluşturulmasını bekler ve Flink job'unu otomatik olarak cluster'a teslim eder.

---

### Kurulum ve Çalıştırma

#### Ön Gereksinimler
- Docker & Docker Desktop
- Herhangi bir terminal (PowerShell, Bash vb.)

#### 1. Ortam Değişkenlerini (Env) Ayarlama
Proje dizinindeki şifreleri ayarlamak için ilk olarak `twitter-hpa` klasörü altında bulunan `.env.example` dosyasının adını `.env` olarak değiştirin. İçerisine DigitalOcean Spaces (S3) `AWS_ACCESS_KEY_ID` ve `AWS_SECRET_ACCESS_KEY` değerlerinizi girin.

#### 2. S3 Landing Zone Hazırlığı
Sistem "stateless" (durumsuz) çalıştığı için, ham veriyi (Tweets.csv) konteyner içinde tutmaz. 
- DO Spaces panelinizde `twitter-hpa-datalake` (veya .env'de belirlediğiniz) bucket'ı oluşturun.
- Bucket içinde `landing-zone` adında bir klasör açın.
- Elinizdeki `Tweets.csv` dosyasını bu klasörün içine yükleyin.

#### 3. Sistemi Başlatma
Proje dizininde şu komutu çalıştırarak tüm sistemi (build dahil) ayağa kaldırabilirsiniz:
```powershell
docker compose up --build -d
```

#### Sistemi Durdurma ve Sıfırlama
Tüm konteynerleri durdurmak ve veritabanlarını temizlemek için:
```powershell
docker compose down -v
```

---

### Bağlantı Bilgileri

> **Not:** Sistem genelindeki kullanıcı adı/şifre bilgileri projenin ana dizinindeki `.env` dosyasından yönetilmektedir. Değişiklik yaptığınızda servisleri yeniden başlatmanız (`docker compose up`) yeterlidir.

#### MongoDB (Alertler)
- **Adres:** `mongodb://localhost:27018`
- **Veritabanı:** `twitter_hpa`
- **Koleksiyonlar:** `tweet_alerts`

#### PostgreSQL (Analitik Metrikler)
- **Adres:** `localhost:5432`
- **Kullanıcı / Şifre:** `.env` dosyasındaki bilgiler
- **Veritabanı:** `twitter_metrics`
- **Tablo:** `tweet_metrics` (Speed Layer)
- **Tablo:** `batch_tweet_metrics` (Batch Layer)
- **Görünüm (View):** `unified_metrics` (Unified Serving Layer)

#### Flink Dashboard
- **Adres:** [http://localhost:8082](http://localhost:8082)

#### Airflow UI
- **Adres:** [http://localhost:8081](http://localhost:8081)
- **Kullanıcı / Şifre:** `.env` dosyasındaki bilgiler (örn: `admin` / `admin`)

#### Schema Registry (Avro Şemaları)
- **Adres:** [http://localhost:8083](http://localhost:8083)
- **Şemaları Listele:** [http://localhost:8083/subjects](http://localhost:8083/subjects)

#### Spark Master UI
- **Adres:** [http://localhost:8084](http://localhost:8084)

#### Cloud Data Lake (DigitalOcean Spaces / S3)
- **Girdi (Landing Zone):** `s3://twitter-hpa-datalake/landing-zone/Tweets.csv`
- **Ham Veri (Raw Parquet):** `s3://twitter-hpa-datalake/raw_tweets/`
- **Batch Çıktı (Arşiv):** `s3://twitter-hpa-datalake/batch_output/`
- **Flink Checkpoints:** `s3://twitter-hpa-datalake/flink-checkpoints/`
- **Airflow Logs:** `s3://twitter-hpa-datalake/airflow-logs/`
- **Format:** Snappy Sıkıştırılmış Parquet (Veri Gölleri için)

---

### Kritik Ayarlar
- **Flink Parallelism:** `env.setParallelism(2)` olarak yapılandırılmıştır.
- **Kafka Partitions:** Üretim standardına göre ayarlanmıştır (over-provisioned).
