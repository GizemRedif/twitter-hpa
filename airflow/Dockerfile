# =============================================================
# Airflow Custom Image
# =============================================================
# Resmi Airflow imajı üzerine amazon provider paketi eklenir.
# Bu paket, Airflow'un loglarını S3 (DO Spaces) üzerine
# yazabilmesi için gerekli olan RemoteLogHandler'ı sağlar.
# =============================================================

FROM apache/airflow:2.7.1

# S3 Remote Logging için gerekli provider paketi
# Bu paket: S3Hook, RemoteLogHandler ve AWS connection desteği sağlar.
RUN pip install --no-cache-dir apache-airflow-providers-amazon==8.13.0
