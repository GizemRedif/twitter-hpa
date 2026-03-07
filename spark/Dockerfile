# PySpark Batch Layer - Docker Image Build Tarifi (Dockerfile)
# Apache Spark resmi image üzerine MongoDB Spark Connector ve PostgreSQL JDBC driver ekler.
# spark-master, spark-worker ve spark-submit servislerinde ortak kullanılır.

FROM apache/spark:3.5.3

USER root

# MongoDB Spark Connector JAR ve PostgreSQL JDBC Driver'ı indir
# Bu JAR'lar Spark'ın MongoDB'den okuma ve PostgreSQL'e yazma yapabilmesi için gereklidir.
RUN curl -L -o /opt/spark/jars/mongo-spark-connector_2.12-10.4.0.jar \
    https://repo1.maven.org/maven2/org/mongodb/spark/mongo-spark-connector_2.12/10.4.0/mongo-spark-connector_2.12-10.4.0.jar && \
    curl -L -o /opt/spark/jars/mongodb-driver-sync-5.2.1.jar \
    https://repo1.maven.org/maven2/org/mongodb/mongodb-driver-sync/5.2.1/mongodb-driver-sync-5.2.1.jar && \
    curl -L -o /opt/spark/jars/mongodb-driver-core-5.2.1.jar \
    https://repo1.maven.org/maven2/org/mongodb/mongodb-driver-core/5.2.1/mongodb-driver-core-5.2.1.jar && \
    curl -L -o /opt/spark/jars/bson-5.2.1.jar \
    https://repo1.maven.org/maven2/org/mongodb/bson/5.2.1/bson-5.2.1.jar && \
    curl -L -o /opt/spark/jars/postgresql-42.7.4.jar \
    https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar

# Batch output dizinini oluştur (Parquet çıktısı için)
RUN mkdir -p /opt/spark-data/batch_output && chmod 777 /opt/spark-data/batch_output

# Batch job script'ini container'a kopyala
COPY batch_job.py /opt/spark-jobs/batch_job.py
