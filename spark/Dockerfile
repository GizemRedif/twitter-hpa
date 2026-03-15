# PySpark Batch Layer - Docker Image Build Tarifi (Dockerfile)
# Apache Spark resmi image üzerine PostgreSQL JDBC driver ekler.
# spark-master, spark-worker ve spark-submit servislerinde ortak kullanılır.

FROM apache/spark:3.5.3

USER root

# PostgreSQL JDBC Driver'ı indir
# Bu JAR, Spark'ın PostgreSQL'e yazma yapabilmesi için gereklidir.
RUN curl -L -o /opt/spark/jars/postgresql-42.7.4.jar \
    https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar

# Deletion/Pruning işlemleri için psycopg2-binary kur
RUN pip install --no-cache-dir psycopg2-binary

# Spark veri dizinlerini oluştur
# raw_tweets: Data Lake'ten okunan ham Parquet dosyaları (girdi)
# batch_output: Hesaplanan metriklerin Parquet çıktısı
RUN mkdir -p /opt/spark-data/raw_tweets && chmod 777 /opt/spark-data/raw_tweets && \
    mkdir -p /opt/spark-data/batch_output && chmod 777 /opt/spark-data/batch_output

# Batch job script'ini container'a kopyala
COPY batch_job.py /opt/spark-jobs/batch_job.py
