"""
PySpark Batch Layer — Twitter HPA Lambda Architecture 

MongoDB'deki tweets_raw koleksiyonundan ham tweet verilerini okur,
1 saatlik pencereler halinde airline bazlı batch metrikleri hesaplar
ve sonuçları hem PostgreSQL'e hem Parquet dosyalarına yazar.

Kullanım:
    spark-submit --packages org.mongodb.spark:mongo-spark-connector_2.12:10.4.0,org.postgresql:postgresql:42.7.4 batch_job.py
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import TimestampType


# -------- Ayarlar --------
MONGO_URI = "mongodb://mongo:27017"
MONGO_DB = "twitter_hpa"
MONGO_COLLECTION = "tweets_raw"

PG_URL = "jdbc:postgresql://postgres:5432/twitter_metrics"
PG_TABLE = "batch_tweet_metrics"
PG_USER = "airflow"
PG_PASSWORD = "airflow"

PARQUET_OUTPUT_PATH = "/opt/spark-data/batch_output"


# Spark uygulamasını başlatır. 
def create_spark_session():
    """MongoDB ve PostgreSQL bağlantıları için yapılandırılmış SparkSession oluşturur."""
    return (
        SparkSession.builder
        .appName("Twitter HPA - Batch Layer")
        .config("spark.mongodb.read.connection.uri", f"{MONGO_URI}/{MONGO_DB}.{MONGO_COLLECTION}")
        .getOrCreate()   # Zaten bir session varsa onu kullanır, yoksa yenisini oluşturur.
    )


def read_from_mongodb(spark):
    """MongoDB tweets_raw koleksiyonundan ham tweet verilerini okur."""
    print("[INFO] Reading the tweets_raw collection from MongoDB...")

    # MongoDB'deki tweets_raw koleksiyonundaki tüm ham tweetleri bir Spark DataFrame'e yükler.
    df = (
        spark.read
        .format("mongodb")
        .option("connection.uri", f"{MONGO_URI}/{MONGO_DB}.{MONGO_COLLECTION}")
        .load()
    )

    # Kaç tweet okunduğunu loglar.
    row_count = df.count()
    print(f"[INFO] {row_count} tweets read from MongoDB.")
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
    df_parsed = df.withColumn("tweet_ts", F.to_timestamp(F.col("tweet_created"), "yyyy-MM-dd HH:mm:ss Z"))

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
        .withColumn("window_start", F.col("window.start").cast("string"))
        .withColumn("window_end", F.col("window.end").cast("string"))
        .drop("window")  # window struct kolonunu kaldır, düz kolonlarla devam et
    )

    result_count = metrics_df.count()
    print(f"[INFO] {result_count} batch metrik satırı hesaplandı.")
    return metrics_df


def write_to_postgresql(metrics_df):
    """Batch metriklerini PostgreSQL batch_tweet_metrics tablosuna JDBC ile yazar."""
    print("[INFO] Writing to PostgreSQL...")

    # Hesaplanan metrikleri JDBC ile PostgreSQL'deki batch_tweet_metrics tablosuna yazar. 
    (
        metrics_df.write.format("jdbc")
        .option("url", PG_URL)
        .option("dbtable", PG_TABLE)
        .option("user", PG_USER)
        .option("password", PG_PASSWORD)
        .option("driver", "org.postgresql.Driver")
        .mode("overwrite")       # Her çalışmada tabloyu yeniden yaz (eski veriyi siler)
        .save()
    )

    print("[INFO] PostgreSQL write completed.")


def write_to_parquet(metrics_df):
    """Batch metriklerini Parquet formatında airline bazlı partition'layarak yazar."""
    print(f"[INFO] Writing to Parquet: {PARQUET_OUTPUT_PATH}")

    # Hesaplanan aynı metrikleri Parquet formatında diske yazar.
    (
        metrics_df.write.mode("overwrite")       # Her batch çalışmasında üzerine yaz
        .partitionBy("airline")                  # Airline bazlı partition sayesinde her airline için ayrı klasör oluşur. (Bu yapı büyük veri sorgularında çok hızlı filtreleme sağlar)
        .parquet(PARQUET_OUTPUT_PATH)
    )

    print("[INFO] Completed Parquet write.") 


# Tüm adımları sırayla çalıştıran orkestratör
def main():
    print("=" * 60)
    print("  Twitter HPA - PySpark Batch Layer")
    print("  MongoDB -> Calculate Metrics -> PostgreSQL + Parquet")
    print("=" * 60)

    # 1. SparkSession oluştur
    spark = create_spark_session()
    print(f"[INFO] SparkSession created: {spark.sparkContext.appName}")

    try:
        # 2. MongoDB'den oku (ham tweet verilerini)
        raw_df = read_from_mongodb(spark)

        # 3. Metrikleri hesapla (1 saatlik pencereler, airline bazlı)
        metrics_df = transform_and_compute_metrics(raw_df)

        # 4. Sonuçları PostgreSQL'e yaz (Serving Layer)
        write_to_postgresql(metrics_df)

        # 5. Sonuçları Parquet'e yaz (Data Lake / Arşiv)
        write_to_parquet(metrics_df)

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
