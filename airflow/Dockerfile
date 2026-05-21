# =============================================================
# Airflow Custom Image
# =============================================================
# Resmi Airflow imajı üzerine amazon provider paketi eklenir.
# Bu paket, Airflow'un loglarını S3 (DO Spaces) üzerine
# yazabilmesi için gerekli olan RemoteLogHandler'ı sağlar.
# Docker CLI eklenir: DAG'lar "docker exec" ile diğer
# container'larda komut çalıştırabilsin diye gereklidir.
# =============================================================

FROM apache/airflow:2.7.1

# Docker CLI kurulumu (docker exec komutlarını çalıştırabilmek için)
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends docker.io && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
USER airflow

# S3 Remote Logging için gerekli provider paketi
# Bu paket: S3Hook, RemoteLogHandler ve AWS connection desteği sağlar.
RUN pip install --no-cache-dir apache-airflow-providers-amazon==8.13.0
