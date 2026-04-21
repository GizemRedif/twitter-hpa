"""
PySpark Batch Layer — Twitter HPA Lambda Architecture 

Data Lake'teki (Parquet formatındaki) ham tweet verilerini okur,
1 saatlik pencereler halinde airline bazlı batch metrikleri hesaplar
ve sonuçları hem PostgreSQL'e hem Parquet dosyalarına yazar.

Kullanım:
    spark-submit --packages org.postgresql:postgresql:42.7.4 batch_job.py
"""

from pyspark.sql import SparkSession # type: ignore
from pyspark.sql import functions as F # type: ignore
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, TimestampType, LongType # type: ignore
import shutil, os
import psycopg2 # type: ignore


# -------- Ayarlar --------
# S3 / DO Spaces ayarları
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "your_bucket_name")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "https://nyc3.digitaloceanspaces.com")
AWS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY")

# Ham tweet verilerinin Parquet formatında saklandığı S3 dizini (Data Lake)
# Spark s3a:// protokolünü kullanır.
RAW_PARQUET_PATH = f"s3a://{S3_BUCKET_NAME}/raw_tweets"

PG_URL = "jdbc:postgresql://postgres:5432/" + os.environ.get("ANALYTICS_DB", "twitter_metrics")
PG_TABLE = "batch_tweet_metrics"
PG_USER = os.environ.get("POSTGRES_USER", "admin")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "admin")

# Batch metriklerinin Parquet formatında saklanacağı S3 dizini
PARQUET_OUTPUT_PATH = f"s3a://{S3_BUCKET_NAME}/batch_output"


# Spark uygulamasını başlatır. 
def create_spark_session():
    """MongoDB, PostgreSQL ve S3 (DO Spaces) bağlantıları için yapılandırılmış SparkSession oluşturur."""
    spark = (
        SparkSession.builder
        .appName("Twitter HPA - Batch Layer")
        .config("spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version", "2")
        .config("spark.sql.files.ignoreMissingFiles", "true")
        .config("spark.sql.files.ignoreCorruptFiles", "true")
        .config("spark.sql.parquet.cacheMetadata", "false")
        .getOrCreate()   # Zaten bir session varsa onu kullanır, yoksa yenisini oluşturur.
    )
    
    # S3 Hadoop Ayarları
    sc = spark.sparkContext
    sc._jsc.hadoopConfiguration().set("fs.s3a.access.key", AWS_KEY)
    sc._jsc.hadoopConfiguration().set("fs.s3a.secret.key", AWS_SECRET)
    sc._jsc.hadoopConfiguration().set("fs.s3a.endpoint", S3_ENDPOINT_URL)
    sc._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true") # Gerekli (özellikle DO Spaces/Minio için)
    sc._jsc.hadoopConfiguration().set("fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    sc._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")
    
    return spark


def get_raw_tweet_schema():
    """Ham Parquet dosyaları için beklenen şema."""
    return StructType([
        StructField("tweet_id", StringType(), True),
        StructField("airline", StringType(), True),
        StructField("airline_sentiment", StringType(), True),
        StructField("text", StringType(), True),
        StructField("retweet_count", LongType(), True),
        StructField("tweet_created", StringType(), True),
        StructField("ingested_at", StringType(), True)
    ])


def read_from_parquet(spark):
    """Data Lake'teki (Parquet formatındaki) ham tweet verilerini okur."""
    print(f"[INFO] Reading raw tweets from Parquet: {RAW_PARQUET_PATH}")

    # recursiveFileLookup: Spark'ın sildiğimiz eski dosyaları bellekte tutup FileNotFoundException
    # atmasını engellemek için dizini tekrar taramasını sağlar.
    schema = get_raw_tweet_schema()
    
    df = (
        spark.read
        .schema(schema)
        .option("pathGlobFilter", "*.parquet")
        .option("recursiveFileLookup", "true")
        .parquet(RAW_PARQUET_PATH)
    )

    # Kaç tweet okunduğunu loglar.
    row_count = df.count()
    print(f"[INFO] {row_count} tweets read from Parquet (Data Lake).")
    return df


# Bu fonksiyon şunları yapar: Tarih dönüşümü, Gruplama + aggregation, Oran hesaplaması, Flatten
def transform_and_compute_metrics(df):
    """
    Ham tweet verisini 1 saatlik pencereler halinde airline bazlı metriklere dönüştürür.

    Algoritma:
    1. tweet_created alanını timestamp'e çevir
    2. Saatlik pencere + airline bazlı gruplama ve metrik hesaplama
    3. Her pencere + airline grubu için metrikleri hesapla:
       - tweet_count, positive/negative/neutral_count
       - positive_ratio, negative_ratio
       - avg_retweet, max_retweet
       - tweet_rate (tweet sayısı / 60)
    """
    print("[INFO] Starting metric calculation...")

    # tweet_created string'ini timestamp'a çevirir (Format: "2015-02-24 11:35:52 -0800")
    df_parsed = df.withColumn("tweet_ts", F.to_timestamp(F.substring(F.col("tweet_created"), 1, 19), "yyyy-MM-dd HH:mm:ss"))

    # Geçersiz tarihleri filtrele
    df_parsed = df_parsed.filter(F.col("tweet_ts").isNotNull())

    # 1 saatlik pencere + airline bazlı gruplama ve metrik hesaplama
    metrics_df = (df_parsed
        # groupBy: Tweetleri 1 saatlik pencere + airline bazında gruplar
        .groupBy( 
            F.window(F.col("tweet_ts"), "1 hour"),        # 1 saatlik tumbling window
            F.col("airline")
        )
        # Gruplama sonrası aşağıdaki metrik hesaplamaları yapılır
        .agg(
            F.count("*").alias("tweet_count"),   # Toplam tweet sayısı

            # Sentiment sayıları (pozitif, negatif, nötr) 
            F.sum(F.when(F.col("airline_sentiment") == "positive", 1).otherwise(0)).alias("positive_count"),
            F.sum(F.when(F.col("airline_sentiment") == "negative", 1).otherwise(0)).alias("negative_count"),
            F.sum(F.when(F.col("airline_sentiment") == "neutral", 1).otherwise(0)).alias("neutral_count"),

            # Retweet istatistikleri (Ortalama ve maksimum retweet sayısı)
            F.avg("retweet_count").alias("avg_retweet"),
            F.max("retweet_count").alias("max_retweet"),
        )
        # Oran hesaplamaları (pozitif/negatif tweet oranı)
        .withColumn("positive_ratio", F.when(F.col("tweet_count") > 0, F.col("positive_count") / F.col("tweet_count")).otherwise(0.0))
        .withColumn("negative_ratio", F.when(F.col("tweet_count") > 0, F.col("negative_count") / F.col("tweet_count")).otherwise(0.0))
        # Tweet rate: tweet sayısı / 60 dakika
        .withColumn("tweet_rate", F.col("tweet_count") / F.lit(60.0))
        
        # Window Alanlarını Düzleştirme - Flatten (Neden yapıldığı en altta)
        .withColumn("window_start", F.col("window.start"))
        .withColumn("window_end", F.col("window.end"))
        .drop("window")  # window struct kolonunu kaldır, düz kolonlarla devam et
    )

    result_count = metrics_df.count()
    print(f"[INFO] {result_count} batch metrik satırı hesaplandı.")
    return metrics_df


def write_to_postgresql(metrics_df):
    """Batch metriklerini PostgreSQL batch_tweet_metrics tablosuna JDBC ile yazar."""
    print("[INFO] Truncating table with psycopg2...")
    
    # JDBC'nin tabloyu silmeye (DROP) çalışıp View'ları bozmasını engellemek için 
    # tabloyu manuel olarak truncate ediyoruz.
    try:
        conn_params = {
            "host": "postgres",
            "port": 5432,
            "database": os.environ.get("ANALYTICS_DB", "twitter_metrics"),
            "user": PG_USER,
            "password": PG_PASSWORD
        }
        with psycopg2.connect(**conn_params) as conn:
            with conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {PG_TABLE} CASCADE;")
                print(f"[INFO] Table {PG_TABLE} truncated successfully.")
    except Exception as e:
        print(f"[WARNING] Truncate failed: {e}. Attempting to proceed with Spark write.")

    print("[INFO] Writing to PostgreSQL via Spark (append mode)...")

    # Mode 'append' kullanıyoruz çünkü tabloyu az önce manuel boşalttık.
    # Bu sayede Spark tabloyu silmeye (drop) çalışmayacak ve View'larımız sağlam kalacak.
    (
        metrics_df.write.format("jdbc")
        .option("url", PG_URL)
        .option("dbtable", PG_TABLE)
        .option("user", PG_USER)
        .option("password", PG_PASSWORD)
        .option("driver", "org.postgresql.Driver")
        .mode("append") 
        .save()
    )

    print("[INFO] PostgreSQL write completed.")


def prune_speed_layer(spark, metrics_df):
    """
    Batch job başarıyla tamamlandıktan sonra, batch'e giren eski verileri 
    Speed Layer (PostgreSQL - tweet_metrics) tablosundan temizler.
    Bu, Lambda mimarisinde 'Recomputation' sonrası temizlik adımıdır.
    """
    print("[INFO] Pruning old data from Speed Layer (tweet_metrics)...")
    
    # Batch'e giren en son window_end zamanını bul
    max_window_end = metrics_df.select(F.max("window_end")).collect()[0][0]
    
    if max_window_end:
        print(f"[INFO] Pruning real-time metrics older than or equal to: {max_window_end}")
        
        # JDBC ile değil, direkt psycopg2 ile DELETE sorgusu çalıştırıyoruz
        try:
            # os.environ.get logic'ini kullanarak DB adını al
            dbname = os.environ.get("ANALYTICS_DB", "twitter_metrics")
            user = os.environ.get("POSTGRES_USER", "admin")
            pw = os.environ.get("POSTGRES_PASSWORD", "admin")
            
            conn = psycopg2.connect(
                host="postgres",
                database=dbname,
                user=user,
                password=pw
            )
            cur = conn.cursor()
            
            # Batch'in kapsadığı verileri speed layer tablosundan sil
            cur.execute("DELETE FROM tweet_metrics WHERE window_end <= %s", (max_window_end,))
            deleted_rows = cur.rowcount
            
            conn.commit()
            cur.close()
            conn.close()
            print(f"[INFO] Successfully pruned {deleted_rows} rows from tweet_metrics.")
        except Exception as e:
            print(f"[ERROR] Speed layer pruning failed: {e}")
    else:
        print("[WARN] No window_end found, skipping pruning.")


def write_to_parquet(metrics_df):
    """Batch metriklerini Parquet formatında airline bazlı partition'layarak S3'e yazar."""
    print(f"[INFO] Writing to S3 Parquet: {PARQUET_OUTPUT_PATH}")

    # S3 üzerinde olduğumuz için yerel dizinlerdeki "Device or resource busy" hatasıyla karşılaşmayız.
    # Bu yüzden Spark'ın standart mode("overwrite") özelliğini güvenle kullanabiliriz.
    (
        metrics_df.write
        .mode("overwrite")
        .partitionBy("airline")
        .parquet(PARQUET_OUTPUT_PATH)
    )
    print(f"[INFO] Parquet write to S3 completed.")


# Tüm adımları sırayla çalıştıran orkestratör
def main():
    print("=" * 60)
    print("  Twitter HPA - PySpark Batch Layer")
    print("  Parquet (Data Lake) -> Calculate Metrics -> PostgreSQL + Parquet")
    print("=" * 60)

    # 1. SparkSession oluştur
    spark = create_spark_session()
    print(f"[INFO] SparkSession created: {spark.sparkContext.appName}")

    try:
        # 2. Data Lake'ten oku (Parquet formatındaki ham tweet verileri)
        raw_df = read_from_parquet(spark)

        # 3. Metrikleri hesapla (1 saatlik pencereler, airline bazlı)
        metrics_df = transform_and_compute_metrics(raw_df)

        # 4. Sonuçları PostgreSQL'e yaz (Serving Layer)
        write_to_postgresql(metrics_df)

        # 5. Sonuçları Parquet'e yaz (Data Lake / Arşiv)
        write_to_parquet(metrics_df)

        # 6. Speed Layer (tweet_metrics) temizliği yap (Lambda Loop Completion)
        prune_speed_layer(spark, metrics_df)

        print("=" * 60)
        print("  BATCH JOB COMPLETED!")
        print("=" * 60)

    except Exception as e:
        print(f"[ERROR] Batch job failed: {e}")
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    main()



# Window Alanlarını Düzleştirme - Flatten neden yapıyoruz: 
# Spark'ın F.window() fonksiyonu sonucu bir struct (iç içe yapı) döner:
# window: { start: "2025-02-24 11:00:00", end: "2025-02-24 12:00:00" }
# PostgreSQL böyle iç içe struct'ları anlayamaz. Bu yüzden window.start ve window.end'i ayrı düz kolonlara çıkarıyorsun:
# window_start: "2025-02-24 11:00:00"
# window_end:   "2025-02-24 12:00:00"
# Çünkü PostgreSQL ve Parquet'e yazabilmek için veriyi düz tablo formatına getirmek gerekiyor.
