## Twitter HPA (High Performance Analysis) - Lambda Architecture + Cloud Deployment Plan

Bu proje, Twitter verilerini gerçek zamanlı ve toplu olarak işlemek amacıyla **Lambda Mimari** prensipleri doğrultusunda geliştirilmiştir.

### Proje Durumu
| ⚡ **Speed Layer** Tamamlandı
| 🗄️ **Serving Layer** Tamamlandı
| 📦 **Batch Layer** Tamamlandı 
| ☁️ **Cloud Deployment** Planlanıyor

> **Not:** Bu projede temel hedef, elimdeki veri setine göre optimizasyon yapmak değil; gerçek dünya standartlarında bir sistem mimarisi kurmayı öğrenmektir. Bu nedenle şu anda projemde fazla tahsis edilmiş (over-provisioned) kaynaklar bulunmaktadır (Partition sayıları, Flink parallelism ayarı gibi). Lambda architecture tamamlandığında bir bulut ortamına (büyük ihtimalle DigitalOcean) taşıma planlanmaktadır.
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
| Konteynerleştirme | Docker & Docker Compose |

---

### Mimari Akış
![Data Flow Diagram](Data%20Flow%20Diagram%20-%20DFD.png)

#### 1. Veri Üretimi ve Entegrasyon (Ingestion)
- **Producer:** Python tabanlı `kafka-producer`, ham tweet verilerini (`Tweets.csv`) okur.
- **Serialization:** Veriler, Confluent Schema Registry üzerindeki **Avro** şemasına göre serialize edilerek binary formatta iletilir. (Verinin ağ üzerinde en az yer kaplamasını sağlamak için)
- **Kafka Topic:** Hazırlanan mesajlar `tweets.raw` topic'ine asenkron olarak gönderilir.

#### 2. Şema Yönetimi (Schema Management)
- Tüm bileşenler (Producer, Flink, Consumer) merkezi **Schema Registry**'yi kullanır. Bu yaklaşım veri tutarlılığını sağlar ve şema evrimini (schema evolution) kolaylaştırır.

#### 3. Hız Katmanı — Anlık İşleme (Speed Layer)
- **Flink Job:** `tweets.raw` topic'ini kesintisiz dinler.
- **Event Time & Watermarks:** Tweetlerin orijinal zaman damgaları (`tweet_created`) kullanılır; geç gelen veriler için 5 saniyelik Watermark toleransı uygulanır.
- **Pencereleme (Windowing):** Veriler havayolu bazlı gruplanır (`keyBy`) ve 1 dakikalık `TumblingEventTimeWindows` pencerelerinde toplanır.
- **Çıktılar:**
  - "Negative" duygu etiketli tweetler → `tweets.alert` topic'i
  - Dakikalık istatistikler (tweet sayısı, ortalama retweet vb.) → `tweets.metrics` topic'i
 
#### 4. Toplu İşleme Katmanı (Batch Layer)
- **Parquet Raw Consumer:** Kafka'daki `tweets.raw` topic'ini dinler ve ham tweetleri **Parquet** formatında Data Lake'e yazar. Bu yapı Lambda Mimarisi'ndeki "Immutable Master Dataset" (Değiştirilemez Ana Veri Seti) konseptini sağlar.
- **PySpark Job:** `spark/batch_job.py`, Data Lake'teki (Parquet formatındaki) ham tweet verilerini periyodik olarak okur ve 1 saatlik pencereler bazında toplu analizler üretir.
- **Airflow DAG:** `airflow/dags/` altındaki DAG, PySpark işini zamanlanmış (scheduled) biçimde tetikler ve orkestre eder.

#### 5. Sunum Katmanı — Depolama ve Tüketim (Serving Layer)
- **Consumers:** Python consumer'ları Kafka'dan gelen sonuçları kalıcı depolara yazar:
  - **MongoDB:** Kritik uyarılar (`tweet_alerts`) saklanır
  - **PostgreSQL:** Analitik metrikler hem hız katmanından (`tweet_metrics`) hem de toplu işleme katmanından (`batch_tweet_metrics`) gelen verilerle burada saklanır.
- **Spark Output:** PySpark job sonuçları hem PostgreSQL'e yazar hem de uzun süreli saklama için **Parquet** formatında dosya sistemine (Data Lake) arşivler.

#### 6. Otomasyon (Infrastructure as Code)
- **Docker Compose:** Tüm ekosistem tek komutla ayağa kalkar.
- **Self-Healing Startup:** `docker-entrypoint-override.sh` scripti; servislerin hazır olmasını, topic'lerin oluşturulmasını bekler ve Flink job'unu otomatik olarak cluster'a teslim eder.

---

### Kurulum ve Çalıştırma

#### Ön Gereksinimler
- Docker & Docker Desktop
- Herhangi bir terminal (PowerShell, Bash vb.)

#### 1. Ortam Değişkenlerini (Env) Ayarlama
Proje dizinindeki şifreleri ayarlamak için ilk olarak `twitter-hpa` klasörü altında bulunan `.env.example` dosyasının adını `.env` olarak değiştirin. 

#### 2. Sistemi Başlatma
Proje dizininde şu komutu çalıştırarak tüm sistemi (build dahil) ayağa kaldırabilirsiniz:
```powershell
docker compose up --build -d
```

#### Sistemi Durdurma ve Sıfırlama
Tüm konteynerleri durdurmak ve verileri tamamen temizlemek için:
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
- **Kullanıcı / Şifre:** `admin` / `admin`
- **Veritabanı:** `twitter_metrics`
- **Tablo:** `tweet_metrics` (Speed Layer)
- **Tablo:** `batch_tweet_metrics` (Batch Layer)

#### Flink Dashboard
- **Adres:** [http://localhost:8082](http://localhost:8082)

#### Airflow UI
- **Adres:** [http://localhost:8081](http://localhost:8081)
- **Kullanıcı / Şifre:** `admin` / `admin`

#### Schema Registry (Avro Şemaları)
- **Adres:** [http://localhost:8083](http://localhost:8083)
- **Şemaları Listele:** [http://localhost:8083/subjects](http://localhost:8083/subjects)

#### Spark Master UI
- **Adres:** [http://localhost:8084](http://localhost:8084)

#### Data Lake (Parquet)
- **Ham Veri (Girdi):** `/opt/spark-data/raw_tweets` (Konteyner içi) / `./data/raw_tweets` (Host cihaz)
- **Batch Çıktı (Arşiv):** `/opt/spark-data/batch_output` (Konteyner içi) / `./data/batch_output` (Host cihaz)
- **Format:** Snappy Sıkıştırılmış Parquet
- **Partition Yapısı (Çıktı):** `airline` bazlı klasörleme (Partitioning)

---

### Kritik Ayarlar
- **Flink Parallelism:** `env.setParallelism(2)` olarak yapılandırılmıştır.
- **Kafka Partitions:** Üretim standardına göre ayarlanmıştır (over-provisioned).
