# Production Deployment Rehberi — DigitalOcean Droplet

Bu rehber, Twitter HPA projesini bir **DigitalOcean Droplet** üzerinde 7/24 çalışacak şekilde deploy etmek isteyenler için hazırlanmıştır.

> **Not:** Bu adımlar opsiyoneldir. Proje, herhangi bir bilgisayarda `docker compose up` ile lokal olarak da çalıştırılabilir.

---

## 1. Droplet Oluşturma

[DigitalOcean](https://cloud.digitalocean.com/) panelinden **Create → Droplets** seçeneğine tıklayın.

| Ayar | Değer |
|------|-------|
| **Region** | Frankfurt (FRA1) — Spaces ile aynı bölge |
| **Image** | Ubuntu 22.04 veya 24.04 LTS |
| **Size** | **Minimum 8 GB RAM** (önerilen: 16 GB) |
| **Disk** | 160 GB SSD veya daha fazla |
| **Authentication** | SSH Key (önerilen) veya Password |

> **⚠️ Uyarı:** Projede 13+ konteyner aynı anda çalışır (Kafka, Flink, Spark, Airflow vb.). 4 GB RAM kesinlikle yetmez, OOM (Out of Memory) hatası alırsınız.

---

## 2. Droplet'e Bağlanma

Terminalde (PowerShell, CMD veya Bash):
```bash
ssh root@DROPLET_IP_ADRESI
```
İlk bağlantıda gelen fingerprint uyarısına `yes` yazın.

---

## 3. Swap Bellek Oluşturma (4 GB)

Kafka, Flink ve Spark gibi JVM tabanlı araçlar anlık bellek sıçramaları yapar. Swap olmazsa sistem kilitlenebilir.

```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Doğrulama:
```bash
free -h
# Swap satırında 4.0G görmelisiniz
```

---

## 4. Docker Kurulumu

```bash
# Sistem güncellemesi
apt update && apt upgrade -y

# Docker kurulumu (Resmi script)
curl -fsSL https://get.docker.com | sh

# Doğrulama
docker --version
docker compose version
```

---

## 5. Docker Log Limiti (Global)

Streaming araçları (Kafka, Flink) saniyede yüzlerce satır log üretir. Limit koymazsanız disk dolar.

```bash
cat <<EOF > /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF

systemctl restart docker
```

> **Not:** Bu global ayar, `docker-compose.yml` içindeki `x-logging` ayarıyla aynı işi yapar. İkisi birlikte olması sorun çıkarmaz; çift güvenlik görevi görür.

---

## 6. Firewall (Güvenlik Duvarı)

Veritabanı portlarının dışarıya açık kalmaması için güvenlik duvarı kurun:

```bash
ufw allow 22       # SSH — Bu olmadan bağlantınız kopar!
ufw allow 8081     # Airflow Web UI
ufw allow 8082     # Flink Web UI (opsiyonel, debug için)
ufw allow 8084     # Spark Master Web UI (opsiyonel, debug için)
ufw enable
```

> **⚠️ Kritik:** `ufw allow 22` satırını **mutlaka** `ufw enable`'dan **önce** çalıştırın! Aksi halde SSH bağlantınız kesilir ve Droplet'e bir daha giremezsiniz.

**Kapalı kalan portlar (güvenli):** PostgreSQL (5432), MongoDB (27018), Kafka (9092), Zookeeper (2181) — bunlar dışarıdan erişilemez, yalnızca Docker ağı içinden erişilir.

---

## 7. Projeyi Clone'lama ve .env Ayarı

```bash
cd /root
git clone https://github.com/GizemRedif/twitter-hpa.git
cd twitter-hpa

# .env dosyasını oluşturun
cp .env.example .env
nano .env
```

`.env` dosyasını kendi bilgilerinizle doldurun:
```env
POSTGRES_USER=admin
POSTGRES_PASSWORD=sizin_sifreniz
ANALYTICS_DB=twitter_metrics

AIRFLOW_USER=admin
AIRFLOW_PASSWORD=sizin_sifreniz
AIRFLOW_EMAIL=sizin_emailiniz

AWS_ACCESS_KEY_ID=sizin_access_key
AWS_SECRET_ACCESS_KEY=sizin_secret_key
AWS_REGION=fra1
S3_ENDPOINT_URL=https://fra1.digitaloceanspaces.com
S3_BUCKET_NAME=twitter-hpa-datalake
```
Kaydetmek için: `CTRL+O` → Enter → `CTRL+X`

---

## 8. S3 Landing Zone Kontrolü

Producer, ham veriyi S3'ten okur. Eğer henüz yapmadıysanız:

1. DigitalOcean Spaces paneline gidin.
2. Bucket'ınızda `landing-zone/` klasörü oluşturun.
3. `Tweets.csv` dosyasını bu klasöre yükleyin.

> **⚠️ Uyarı:** Bu adım yapılmazsa `kafka-producer` konteyneri hata verip kapanır.

---

## 9. Sistemi Başlatma

```bash
cd /root/twitter-hpa
docker compose up -d --build
```

İlk build süresi ~5-10 dakika sürebilir (Flink Maven build + Spark JAR indirme).

### Kontrol Komutları

```bash
# Tüm konteynerlerin durumunu görme
docker compose ps

# Belirli bir servisin loglarını takip etme
docker compose logs -f kafka-producer

# Bellek ve CPU kullanımını izleme
docker stats
```

Tüm konteynerler `Up` veya `Up (healthy)` durumunda olmalıdır. `init-kafka` konteyneri `Exited (0)` olabilir — bu normaldir (topic'leri oluşturup kapanır).

---

## 10. Backup Otomasyonu (Cron)

```bash
# Script'e çalıştırma yetkisi verin
chmod +x /root/twitter-hpa/backup_job.sh

# Cron görevini ayarlayın
crontab -e
```

Açılan editörün en altına şu satırı ekleyin:
```
0 3 * * * /root/twitter-hpa/backup_job.sh >> /var/log/backup_job.log 2>&1
```

Bu sayede her gece **03:00**'te PostgreSQL ve MongoDB yedekleri otomatik olarak DO Spaces'e yüklenecektir.

---

## Web Arayüzlerine Erişim

| Servis | Adres |
|--------|-------|
| **Airflow** | `http://DROPLET_IP:8081` |
| **Flink Dashboard** | `http://DROPLET_IP:8082` |
| **Spark Master UI** | `http://DROPLET_IP:8084` |

---
