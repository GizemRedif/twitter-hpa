
# Airflow DAG: Görevi, spark/batch_job.py job'unu her saat başı otomatik olarak tetiklemektir. 
# Yani batch layer'ın scheduler rolünü üstleniyor.

# Bu DAG, Spark batch job'unu schedule etmek içindir.
# - PySpark batch job'ını periyodik olarak tetikler. 
# - Spark-submit container'ını docker compose run ile çalıştırır.

# Schedule: @hourly (her saat başı çalışır)


from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator  

# DAG varsayılan ayarları
default_args = {
    "owner": "twitter-hpa",     # DAG'ın sahibi, Airflow UI'da görünür
    "depends_on_past": False,   # Önceki çalışma başarısız olsa bile yeni çalışma başlar
    "email_on_failure": False,  # Hata durumunda e-posta göndermez
    "email_on_retry": False,    # Görev tekrarlandığında e-posta göndermez
    "retries": 1,               # Görev başarısız olursa 1 kez daha dener
    "retry_delay": timedelta(minutes=5),  # Görev başarısız olursa 5 dakika bekler
}

# DAG tanımı
with DAG(
    dag_id="spark_batch_metrics",
    default_args=default_args,
    description="PySpark batch job: MongoDB → Calculate Metrics → PostgreSQL + Parquet",
    schedule_interval="@hourly",      #Her saat başı çalışır
    start_date=datetime(2025, 1, 1),  #DAG'ın başlangıç tarihi
    catchup=False,                    #Geçmiş görevleri çalıştırmaz (önemli çünkü eğer True olsaydı, start_date'ten bugüne kadar olan her saat için ayrı ayrı job tetiklerdi (binlerce çalışma))
    tags=["batch", "spark", "metrics"], 
) as dag:

    # Spark batch job'ını çalıştır
    # BashOperator: Shell komutlarını Airflow task'ı olarak çalıştırır. Burada docker exec komutu çalıştırmak için kullanılıyor.
    run_spark_batch = BashOperator(
        task_id="run_spark_batch_job",
        bash_command=(
            "docker exec spark-submit "                 # Zaten çalışan spark-submit container'ına bağlanır
            "/opt/spark/bin/spark-submit "              # Spark'ın submit komutunu çalıştırır
            "--master spark://spark-master:7077 "       # Job'u Spark master'a gönderir
            "--deploy-mode client "                     # Driver, submit eden makinede çalışır (cluster modu değil)
            "/opt/spark-jobs/batch_job.py"              # Çalıştırılacak PySpark scripti (batch_job.py)
        ),
    )



 # ----------------------- Data Quality Check ----------------------
    # Batch job sonrası çalışacak task 
    # Veri Kalite Kontrol: Batch job tamamlandıktan sonra tüm veri katmanlarını doğrular.
    # Parquet (Data Lake), PostgreSQL (Speed + Batch), MongoDB (Alerts) kontrol edilir.
    # Kontrol başarısız olursa task FAIL olur ve Airflow UI'da görünür.
    run_data_quality_check = BashOperator(
        task_id="run_data_quality_check", 
        bash_command=(
            "docker exec data-quality-check "   # Zaten çalışan data-quality-check container'ına bağlanır
            "python dq_check.py"                 # /data_quality/dq_check.py script'ini çalıştırır
        ),
    )

    # Akış: Spark Batch Job → Veri Kalite Kontrol
    run_spark_batch >> run_data_quality_check


# AKIŞ ÖZETİ
# Her saat başı Airflow tetikler
# 1. BashOperator → docker exec spark-submit → batch_job.py çalışır
# 2. BashOperator → docker compose run data-quality-check → dq_check.py çalışır
# batch_job.py: Data Lake (Parquet) → Metrik Hesaplama → PostgreSQL + Parquet
# dq_check.py: Parquet + PostgreSQL + MongoDB veri kalite kontrolü


