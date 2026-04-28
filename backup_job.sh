#!/bin/bash
# backup_job.sh
# Veritabanı yedeklerini (PostgreSQL & MongoDB) alır ve DO Spaces (S3) üzerine yükler.

# !!! ÖNEMLİ
# Bu script otomatik çalışmaz. Sunucuyu (Droplet'i) kurduktan sonra terminale girip,  
# Linux'un alarm saati/zamanlanmış görev aracı olan "cron" aracılığıyla bu scripte bir saat ayarlamalısın.

# Hata durumunda betiği durdur
set -e

# .env dosyasının olduğu dizine geç
cd "$(dirname "$0")"
source .env

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="$(pwd)/backups"
mkdir -p "$BACKUP_DIR"

echo "[$TIMESTAMP] Backup process is starting..."

# 1. PostgreSQL Yedeği
PG_BACKUP_FILE="postgres_backup_$TIMESTAMP.dump"
echo "Creating a PostgreSQL backup...."
# twitter_metrics veritabanının yedeğini al (Özelleştirilmiş formatta)
docker exec postgres pg_dump -U $POSTGRES_USER -d twitter_metrics -F c -f /tmp/$PG_BACKUP_FILE
docker cp postgres:/tmp/$PG_BACKUP_FILE "$BACKUP_DIR/$PG_BACKUP_FILE"
docker exec postgres rm /tmp/$PG_BACKUP_FILE

# 2. MongoDB Yedeği
MONGO_BACKUP_FILE="mongo_backup_$TIMESTAMP.archive"
echo "Creating a MongoDB backup...."
# twitter_hpa veritabanının yedeğini al
docker exec mongo mongodump --archive=/tmp/$MONGO_BACKUP_FILE --db twitter_hpa
docker cp mongo:/tmp/$MONGO_BACKUP_FILE "$BACKUP_DIR/$MONGO_BACKUP_FILE"
docker exec mongo rm /tmp/$MONGO_BACKUP_FILE

# 3. DO Spaces'e Yükleme (amazon/aws-cli imajı kullanılarak sunucuya aws-cli kurmadan)
echo "Backups are being uploaded to DO Spaces..."
docker run --rm \
  -v "$BACKUP_DIR:/backups" \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION=$AWS_REGION \
  amazon/aws-cli \
  s3 cp /backups/$PG_BACKUP_FILE s3://$S3_BUCKET_NAME/backups/postgres/ --endpoint-url $S3_ENDPOINT_URL

docker run --rm \
  -v "$BACKUP_DIR:/backups" \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION=$AWS_REGION \
  amazon/aws-cli \
  s3 cp /backups/$MONGO_BACKUP_FILE s3://$S3_BUCKET_NAME/backups/mongo/ --endpoint-url $S3_ENDPOINT_URL

# 4. Eski yerel yedekleri temizle (Sadece sunucuda yer kaplamaması için son 3 günü tut, DO Spaces'te tüm yedekler kalır)
find "$BACKUP_DIR" -type f -mtime +3 -exec rm {} \;

echo "[$TIMESTAMP] Backup process completed successfully!"
