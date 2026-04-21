"""
Veri Kalite Kontrol (Data Quality Check) Script'i — Twitter HPA Lambda Architecture

Üç veri katmanını kontrol eder:
1. Parquet Data Lake (raw_tweets)  — dosya varlığı, şema, null, sentiment, duplicate
2. PostgreSQL Serving Layer        — tweet_metrics (speed) ve batch_tweet_metrics (batch)
3. MongoDB Alert Store             — tweet_alerts koleksiyonu

Her kontrol PASS veya FAIL olarak loglanır.
Sonunda özet rapor yazdırılır.
Tüm kontroller geçerse exit code 0, herhangi biri başarısız olursa exit code 1.
"""

import os
import sys
import s3fs # type: ignore
from datetime import datetime, timezone
import pandas as pd  # type: ignore
import pyarrow.parquet as pq  # type: ignore
import psycopg2  # type: ignore
from pymongo import MongoClient  # type: ignore


# ============================================================
# Ayarlar
PARQUET_S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "your_bucket_name")
PARQUET_S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "https://nyc3.digitaloceanspaces.com")
AWS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PARQUET_PATH = f"s3://{PARQUET_S3_BUCKET}/raw_tweets"

PG_HOST = "postgres"
PG_PORT = 5432
PG_DB = os.environ.get("ANALYTICS_DB", "twitter_metrics")
PG_USER = os.environ.get("POSTGRES_USER", "admin")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "admin")

MONGO_URI = "mongodb://mongo:27017"
MONGO_DB = "twitter_hpa"
MONGO_COLLECTION = "tweet_alerts"

# Beklenen şema alanları
EXPECTED_PARQUET_COLUMNS = ["tweet_id", "airline", "airline_sentiment", "text", "retweet_count", "tweet_created", "ingested_at"]

VALID_SENTIMENTS = {"positive", "negative", "neutral"}


# ============================================================
# Sonuç Takibi
# ============================================================
class DQResults:
    """It monitors the quality control results."""

    def __init__(self):
        self.results = []

    def add(self, layer: str, check_name: str, passed: bool, detail: str = ""):
        status = "PASS" if passed else "FAIL"
        self.results.append({
            "layer": layer,  # Şu an hangi veritabanını test ettiğimizi belirtmek için kullanılıyor.
            "check": check_name, 
            "passed": passed,
            "detail": detail,
        })
        msg = f"  {status} | {check_name}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed
        return total, passed, failed


# ============================================================
# 1) Parquet Data Lake Kontrolleri
# ============================================================
def check_parquet(dq: DQResults):
    layer = "Parquet Data Lake"
    print(f"\n{'='*60}")
    print(f"  {layer}")
    print(f"{'='*60}")

    # Hatırlatma - add(layer, check, passed, detail)

    # 1a. Dosya varlığı kontrolü (S3 üzerinden)
    fs = s3fs.S3FileSystem(
        key=AWS_KEY,
        secret=AWS_SECRET,
        client_kwargs={'endpoint_url': PARQUET_S3_ENDPOINT, 'region_name': AWS_REGION}
    )
    
    try:
        parquet_files = fs.glob(f"{PARQUET_PATH}/*.parquet")
    except Exception as e:
        dq.add(layer, "S3 Connection & Parquet file existence", False, str(e))
        return

    has_files = len(parquet_files) > 0
    dq.add(layer, "Parquet file existence", has_files, f"{len(parquet_files)} files found" if has_files else "No .parquet files found!")

    # Eğer parquet dosyası yoksa, diğer kontrolleri return ile atlayacak. 
    if not has_files:
        print(" !! Parquet file not found, other checks are being skipped.")
        return

    # Parquet dosyası mevcutsa devam edilir

    # Tüm parquet dosyalarını oku
    # S3'teki dosyaları DataFrame'e çeviriyoruz.
    try:
        # Prepend 's3://' to the paths returned by fs.glob since read_parquet needs the protocol
        s3_files = [f"s3://{f}" for f in parquet_files]
        df = pd.concat([pd.read_parquet(f, storage_options={
            "key": AWS_KEY,
            "secret": AWS_SECRET,
            "client_kwargs": {'endpoint_url': PARQUET_S3_ENDPOINT, 'region_name': AWS_REGION}
        }) for f in s3_files], ignore_index=True) 
    except Exception as e:
        dq.add(layer, "Parquet reading from S3", False, str(e))
        return

    # 1b. Kayıt sayısı
    row_count = len(df)
    dq.add(layer, "Row count > 0", row_count > 0, f"{row_count} rows")

    # 1c. Şema doğrulama
    # Beklenen sütunlar listesi ile şu an bulunan gerçek sütunları karşılaştırıyor.
    actual_cols = set(df.columns.tolist())
    expected_cols = set(EXPECTED_PARQUET_COLUMNS)
    missing = expected_cols - actual_cols
    schema_ok = len(missing) == 0
    dq.add(layer, "Schema validation (7 columns)", schema_ok, 
            f"Missing columns: {missing}" if not schema_ok else f"Columns: {sorted(actual_cols)}")

    # 1d. Null kontrolü 
    # Kritik alanlarda null değer olup olmadığını kontrol ediyor.
    for col in ["tweet_id", "airline", "airline_sentiment"]:
        if col in df.columns:
            null_count = df[col].isnull().sum()
            dq.add(layer, f"Null check: {col}", null_count == 0, 
                    f"{null_count} null values" if null_count > 0 else "No nulls")

    # 1e. Sentiment değerleri kontrolü
    # Duygu durumunun sadece 3 geçerli değeri olmalı: positive, negative, neutral
    if "airline_sentiment" in df.columns:     # Eğer tabloda airline_sentiment diye bir sütun gerçekten varsa işlemi başlatır.
        unique_sentiments = set(df["airline_sentiment"].dropna().unique())  
        invalid = unique_sentiments - VALID_SENTIMENTS   # len(invalid) > 0 ise hata var demektir.
        dq.add(layer, "Are the Sentiment values valid?", len(invalid) == 0, 
                f"Invalid values: {invalid}" if invalid else f"Values: {unique_sentiments}")


# ============================================================
# 2) PostgreSQL Kontrolleri
# Postgre içerisinde veri yapısı tamamen aynı iki tablo olduğundan, tekrarı önlemek amacıyla kontrolü iki ayrı fonksiyon ile yapıyoruz.
# 1. Fonksiyon (check_pg_table): Tablo içindeki hataları bulan işçi fonksiyon.
# 2. Fonksiyon (check_postgresql): Veritabanına bağlanıp işçi fonksiyona kontrol edeceği tabloları bildiren fonksiyon.
# ============================================================
def check_pg_table(dq: DQResults, cursor, table_name: str, layer: str):
    """It checks the quality controls for a specific PostgreSQL table."""
    print(f"\n{'='*60}")
    print(f"  {layer} — {table_name}")
    print(f"{'='*60}")

    # Hatırlatma - add(layer, check, passed, detail)

    # 2a. Kayıt sayısı
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    dq.add(layer, "Number of records > 0", count > 0, f"{count} records")

    # Eğer tablo boşsa, diğer kontrolleri atla.
    if count == 0:
        print(" !! Table is empty, other checks are skipped.")
        return

    # Tablo boş değilse devam edilir
   
    # 2b. Null kontrolü (kritik alanlar için)
    for col in ["airline", "tweet_count"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {col} IS NULL")
        null_count = cursor.fetchone()[0] # fetchone() ile sadece ilk satırı alıyoruz, [0] ile de o satırdaki ilk (ve tek) değeri alıyoruz.
        dq.add(layer, f"Null check: {col}", null_count == 0,
               f"{null_count} null values" if null_count > 0 else "No nulls")

    # 2c. Oran aralığı kontrolü (0-1 arası)
    for col in ["positive_ratio", "negative_ratio"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {col} < 0 OR {col} > 1")
        out_of_range = cursor.fetchone()[0]
        dq.add(layer, f"Ratio range (0-1): {col}", out_of_range == 0,
               f"{out_of_range} records out of range" if out_of_range > 0 else "All in range 0-1")

    # 2d. Negatif sayı kontrolü
    for col in ["tweet_count", "positive_count", "negative_count"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {col} < 0")
        neg_count = cursor.fetchone()[0]
        dq.add(layer, f"Negative value check: {col}", neg_count == 0,
               f"{neg_count} negative records" if neg_count > 0 else "No negatives")

    # 2e. Toplam tutarlılık kontrolü
    cursor.execute(f"""
        SELECT COUNT(*) FROM {table_name}
        WHERE (positive_count + negative_count + neutral_count) > tweet_count
        """)
    inconsistent = cursor.fetchone()[0]
    dq.add(layer, "Total consistency (pos+neg+neu ≤ tweet_count)", inconsistent == 0,
           f"{inconsistent} inconsistent records" if inconsistent > 0 else "All consistent")


def check_postgresql(dq: DQResults):
    """It checks both tables in PostgreSQL."""
    try:
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD)
        conn.autocommit = True
        cursor = conn.cursor()  # cursor: veritabanı üzerinde işlem yapmanı sağlayan bir işaretçi/temsilci. Örneğin bir sorguyu cursor'a söylersin, o da veritabanına iletir.
        print(f"\n ++ PostgreSQL connection successful. ({PG_DB})")
    except Exception as e:
        print(f"\n -- PostgreSQL connection error: {e}")
        dq.add("PostgreSQL", "Connection", False, str(e))
        return

    try:
        # Speed Layer tablosu için kontrol
        check_pg_table(dq, cursor, "tweet_metrics", "PostgreSQL (Speed Layer)")

        # Batch Layer tablosu için kontrol
        check_pg_table(dq, cursor, "batch_tweet_metrics", "PostgreSQL (Batch Layer)")
    except Exception as e:
        print(f"\n -- PostgreSQL query error: {e}")
        dq.add("PostgreSQL", "Query Execution", False, str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 3) MongoDB Kontrolleri
# ============================================================
def check_mongodb(dq: DQResults):
    layer = "MongoDB (Alerts)"
    print(f"\n{'='*60}")
    print(f"  {layer}")
    print(f"{'='*60}")

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # Bağlantıyı test et
        client.admin.command("ping")
        db = client[MONGO_DB]
        collection = db[MONGO_COLLECTION]
        print(f"MongoDB connection successful. ({MONGO_DB}.{MONGO_COLLECTION})")
    except Exception as e:
        print(f"MongoDB connection error: {e}")
        dq.add(layer, "Connection", False, str(e))
        return

    try:
        # 3a. Kayıt sayısı
        count = collection.count_documents({})
        dq.add(layer, "Record count > 0", count > 0, f"{count} alert documents")

        if count == 0:
            print(" !! Collection is empty, other checks are skipped.")
            return

        # 3b. Null kontrolü (tweet_id ve airline boş olmamalı)
        for field in ["tweet_id", "airline"]:
            null_count = collection.count_documents({"$or": [{field: None}, {field: {"$exists": False}}, {field: ""}]})
            dq.add(layer, f"Null check: {field}", null_count == 0,
                   f"{null_count} null values" if null_count > 0 else "No nulls")

        # 3c. Sentiment tutarlılığı (tüm alert'ler negative olmalı)
        non_negative = collection.count_documents({"airline_sentiment": {"$ne": "negative"}})
        dq.add(layer, "Sentiment consistency (all negative?)", non_negative == 0,
               f"{non_negative} alerts not negative" if non_negative > 0 else "All negative")

    except Exception as e:
        print(f"\n -- MongoDB query error: {e}")
        dq.add(layer, "Query Execution", False, str(e))
    finally:
        client.close()


# ============================================================
# Ana Akış
# ============================================================
def main():
    start_time = datetime.now(timezone.utc)
    print("=" * 60)
    print("Twitter HPA — Data Quality Control Report")
    print(f"Date: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    # Initialize DQResults object to store results 
    dq = DQResults()

    # 1. Parquet Data Lake kontrolleri
    check_parquet(dq)

    # 2. PostgreSQL kontrolleri
    check_postgresql(dq)

    # 3. MongoDB kontrolleri
    check_mongodb(dq)

    # Özet Rapor
    total, passed, failed = dq.summary()
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

    print(f"\n{'='*60}")
    print(f" SUMMARY REPORT")
    print(f"{'='*60}")
    print(f"Total checks : {total}")
    print(f"Passed : {passed}")
    print(f"Failed : {failed}")
    print(f"Duration : {elapsed:.1f} seconds")
    print(f"{'='*60}")

    if failed > 0:
        print("\n -- SOME CHECKS FAILED! Details above.\n")
        sys.exit(1)
    else:
        print("\n ++ ALL CHECKS PASSED!\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
