"""
PySpark Batch Layer — Twitter HPA Lambda Architecture 

Data Lake'teki (Parquet formatındaki) ham tweet verilerini okur,
1 saatlik pencereler halinde airline bazlı batch metrikleri hesaplar
ve sonuçları hem PostgreSQL'e hem Parquet dosyalarına yazar.

Kullanım:
    spark-submit --packages org.postgresql:postgresql:42.7.4 batch_job.py
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, TimestampType
import shutil, os


# -------- Ayarlar --------
# Ham tweet verilerinin Parquet formatında saklandığı dizin (Data Lake)
RAW_PARQUET_PATH = "/opt/spark-data/raw_tweets"

PG_URL = "jdbc:postgresql://postgres:5432/" + os.environ.get("POSTGRES_DB", "twitter_metrics")
PG_TABLE = "batch_tweet_metrics"
PG_USER = os.environ.get("POSTGRES_USER", "admin")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "admin")

PARQUET_OUTPUT_PATH = "/opt/spark-data/batch_output"


# Spark uygulamasını başlatır. 
def create_spark_session():
    """MongoDB ve PostgreSQL bağlantıları için yapılandırılmış SparkSession oluşturur."""
    return (
        SparkSession.builder
        .appName("Twitter HPA - Batch Layer")
        .config("spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version", "2")
        .config("spark.sql.files.ignoreMissingFiles", "true")
        .config("spark.sql.files.ignoreCorruptFiles", "true")
        .config("spark.sql.parquet.cacheMetadata", "false")
        .getOrCreate()   # Zaten bir session varsa onu kullanır, yoksa yenisini oluşturur.
    )


def get_raw_tweet_schema():
    """Ham Parquet dosyaları için beklenen şema."""
    return StructType([
        StructField("tweet_id", StringType(), True),
        StructField("airline", StringType(), True),
        StructField("airline_sentiment", StringType(), True),
        StructField("text", StringType(), True),
        StructField("retweet_count", IntegerType(), True),
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
    # JDBC (Java Database Connectivity): Java uygulamalarının veritabanlarına bağlanmasını sağlayan standart API’dir.
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
    # Parquet: Sütun bazlı (columnar) bir dosya formatıdır. CSV'ye kıyasla:
    # - Çok daha hızlı okuma (sadece ihtiyaç duyulan sütunlar okunur)
    # - Çok daha küçük dosya boyutu (Snappy sıkıştırma ile)
    # - Büyük veri araçları (Spark, Hive, Presto) ile doğrudan uyumlu
    print(f"[INFO] Writing to Parquet: {PARQUET_OUTPUT_PATH}")

    # --- Eski Parquet çıktılarını temizle ---
    # write.mode("overwrite") yerine write.mode("append") kullanıp manuel temizleme yapıyoruz. Nedenleri:
    # Spark'ın overwrite modu çıktı dizininin TAMAMINI silmeye çalışır.
    # Ancak /opt/spark-data/batch_output dizini Docker volume mount noktası olduğu için silinemez ("Device or resource busy" hatası verir).
    # Çözüm: Kök dizini olduğu gibi bırakıp sadece içindeki alt dosya/dizinleri temizle.
    if os.path.exists(PARQUET_OUTPUT_PATH):
        for item in os.listdir(PARQUET_OUTPUT_PATH):
            item_path = os.path.join(PARQUET_OUTPUT_PATH, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)    # airline=American/ gibi partition dizinlerini sil
            else:
                os.remove(item_path)        # _SUCCESS gibi dosyaları sil

    # --- Parquet olarak diske yaz ---
    # mode("append"): Mevcut dizine ekleme yapar, dizini silmeye çalışmaz.
    # (Yukarıda manuel temizleme yaptığımız için sonuç overwrite ile aynı. Sanki eski içeriğin üzerine yazılıyor gibi yani)
    # partitionBy("airline"): Her airline değeri için ayrı alt dizin oluşturur:
    #   batch_output/
    #   ├── airline=American/part-00000.snappy.parquet
    #   ├── airline=United/part-00000.snappy.parquet
    #   ├── airline=Delta/part-00000.snappy.parquet
    #   └── ...
    # Bu yapı sayesinde "sadece Delta'nın verisini getir" gibi sorgularda Spark yalnızca airline=Delta/ dizinini okur ve işlem hızlı olur (partition pruning).
    (
        metrics_df.write.mode("append")
        .partitionBy("airline")
        .parquet(PARQUET_OUTPUT_PATH)
    )

    print("[INFO] Completed Parquet write.") 


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
