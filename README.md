# Twitter HPA (High Performance Analysis) - Lambda Architecture + Cloud Deployment Plan

Bu proje, Twitter verilerini gerçek zamanlı olarak işlemek ve analiz etmek amacıyla **Lambda Mimari** prensipleri doğrultusunda geliştirilmiştir.

## Proje Durumu: Lambda Architecture
Sistem şu an itibariyle Lambda mimarisinin temel katmanlarını barındırmaktadır:

*   **Speed Layer (Hız Katmanı) - [TAMAMLANDI]:** Apache Flink ve Kafka kullanılarak veriler milisaniyeler içinde işlenir ve anlık metrikler üretilir.
*   **Serving Layer (Sunum Katmanı) - [TAMAMLANDI]:** İşlenen veriler MongoDB (Hızlı Erişim/Alertler) ve PostgreSQL (Yapılandırılmış Metrikler) üzerinde sorguya hazır hale getirilir.
*   **Batch Layer (Toplu İşleme Katmanı) - [YAPILACAK]:**

## Proje kaynak kullanımı ve proje planlaması hakkında
Bu projedeki amacım, elimdeki veri setine göre optimizasyon yapmak değil, gerçek dünya standartlarında bir sistem mimarisi kurmayı öğrenmektir. Bu nedenle şu anda projemde fazla tahsis edilmiş (over-provisioned) kaynaklar bulunmaktadır (Partition sayıları, Flink parallelism ayarı gibi).
Lambda architecture tamamlandığında bir bulut ortamına (büyük ihtimalle DigitalOcean'a) taşıma yapılacaktır. 

---

## Teknoloji Yığını
- **Veri Akışı:** Apache Kafka
- **Anlık İşleme:** Apache Flink (Java, Maven)
- **Veri Depolama (NoSQL):** MongoDB (Versiyon 7.0)
- **Veri Depolama (RDBMS):** PostgreSQL (Versiyon 15)
- **Konteynerleştirme:** Docker & Docker Compose
- **Şema Yönetimi:** Confluent Schema Registry (Avro)

---

## Mimari Akış

![Data Flow Diagram](Data%20Flow%20Diagram%20-%20DFD.png)

### 1. Veri Üretimi ve Entegrasyon (Ingestion)
- **Producer:** Python tabanlı `kafka-producer`, ham tweet verilerini (Tweets.csv) okur.
- **Serialization:** Veriler, Confluent Schema Registry üzerindeki **Avro** şemasına göre serialize edilir. Bu işlem, verinin ağ üzerinde en az yer kaplayacak şekilde (binary) iletilmesini sağlar.
- **Kafka Topics:** Hazırlanan mesajlar Kafka'daki `tweets.raw` topic'ine asenkron olarak gönderilir.

### 2. Şema Yönetimi (Schema Management)
- Tüm bileşenler (Producer, Flink, Consumer) merkezi **Schema Registry**'yi sorgular (Docker Compose dosyasında belirtilmiştir). Bu, veri tutarlılığını sağlar ve gelecekteki şema değişikliklerini (schema evolution) yönetmeyi kolaylaştırır.

### 3. Hız Katmanı - Analiz ve İşleme (Speed Layer)
- **Flink Job:** Apache Flink, Kafka `tweets.raw` topic'ini kesintisiz dinler.
- **Event Time & Watermarks:** Tweetlerin içindeki orijinal zaman damgaları (`tweet_created`) kullanılarak veriler işlenir. Geç gelen veriler için 5 saniyelik tolerans (Watermark) uygulanır. 
- **Pencereleme (Windowing):** Veriler havayolu bazlı gruplanır (`keyBy`) ve 1 dakikalık `TumblingEventTimeWindows` pencerelerinde toplanır.
- **Çıktı Üretimi:** 
    - Duygu analizi sonucunda "negative" olarak işaretlenen tweetler anlık olarak ayıklanıp `tweets.alert` topic'ine basılır.
    - Her dakika sonunda hesaplanan istatistikler (toplam tweet, ortalama retweet vb.) `tweets.metrics` topic'ine gönderilir.

### 4. Sunum Katmanı - Depolama ve Tüketim (Serving Layer)
- **Consumers:** Özel Python consumer'ları Kafka'dan gelen sonuçları kalıcı depolara yazar:
    - **MongoDB:** Ham tweet yedekleri (`tweets_raw`) ve kritik uyarılar (`tweet_alerts`) burada saklanır.
    - **PostgreSQL:** Analitik metrikler (`tweet_metrics`) burada depolanır.

### 5. Otomasyon (Infrastructure as Code)
- **Docker Compose:** Tüm ekosistem (Kafka, Flink, Mongo, Postgres) tek bir orkestrasyonla ayağa kalkar.
- **Self-Healing Startup:** Flink konteyneri içindeki `docker-entrypoint-override.sh` scripti; servislerin hazır olmasını, topic'lerin otomatik oluşturulmasını bekler ve her şey hazır olduğunda job'u Flink Cluster'ına otomatik olarak teslim eder.

---

## Kurulum ve Çalıştırma

### Ön Gereksinimler
- Docker ve Docker Desktop
- Herhangi bir terminal (PowerShell, Bash vb.)

### Sistemi Başlatma
Proje dizininde şu komutu çalıştırarak tüm sistemi (build dahil) ayağa kaldırabilirsiniz:
```powershell
docker compose up --build -d
```

### Sistemi Durdurma ve Sıfırlama
Tüm konteynerleri durdurmak ve verileri tamamen temizlemek için:
```powershell
docker compose down -v
```

---

## Verilere Erişim (Bağlantı Bilgileri)

### MongoDB (Ham Veriler ve Alertler)
- **Adres:** `mongodb://localhost:27018`
- **Veritabanı:** `twitter_hpa`
- **Koleksiyonlar:** `tweets_raw`, `tweet_alerts`

### PostgreSQL (Analitik Metrikler)
- **Adres:** `localhost:5432`
- **Kullanıcı/Şifre:** `airflow` / `airflow`
- **Veritabanı:** `twitter_metrics`
- **Tablo:** `tweet_metrics`

### Flink Dashboard
- **Adres:** `http://localhost:8082`

### Schema Registry (Avro Şemaları)
- **Adres:** `http://localhost:8083`
- **Şemaları Listele:** `http://localhost:8083/subjects`

---

## Bazı Kritik Ayarlar
- **Parallelism:** Flink işi şu an `env.setParallelism(2)` ile çalışacak şekilde ayarlanmıştır.
