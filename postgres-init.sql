-- tweets.metrics verilerini saklamak için:
-- 1) twitter_metrics veritabanını oluştur
CREATE DATABASE twitter_metrics;
-- 2) twitter_metrics'e bağlan ve tabloyu oluştur
\connect twitter_metrics  


-- ---------------------------------------------------------------------
-- Speed Layer Tablosu - Flink'in gerçek zamanlı hesapladığı metrikler buraya yazılır.
-- Batch job her çalıştığında eski satırlar prune edildiği için bu tablo küçük kalır. Partitioning yerine index yeterlidir.
 
-- Prune nedeni: Spark S3'teki ham veriden 1 saatlik kesin sonuçları hesaplayıp batch tablosuna yazar. 
-- Artık elimizde o 1 saatin toplu ve kesin verisi olduğu için, Flink'in aynı saate ait 1'er dakikalık parçalarına ihtiyacımız kalmaz. 
-- Overlap'i önlemek ve Speed Layer'ı sadece en taze veriyle tutmak için buradaki eski kısımlar silinir.
CREATE TABLE tweet_metrics (
    id              SERIAL PRIMARY KEY,
    airline         VARCHAR(100) NOT NULL,
    tweet_count     BIGINT,
    positive_count  BIGINT,
    negative_count  BIGINT,
    neutral_count   BIGINT,
    positive_ratio  DOUBLE PRECISION,
    negative_ratio  DOUBLE PRECISION,
    avg_retweet     DOUBLE PRECISION,
    max_retweet     BIGINT,
    tweet_rate      DOUBLE PRECISION,
    window_start    TIMESTAMP,
    window_end      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Speed Layer Indexleri
-- unified_metrics view'ı window_start >= MAX(batch.window_end) filtresi kullanır.
-- PostgreSQL direkt o zamandan sonraki satırlara zıplar, eski veriye hiç bakmaz
CREATE INDEX idx_tweet_metrics_window_start ON tweet_metrics (window_start);

-- Airline bazlı sorgular için (dashboard filtreleme)
-- Örneğin Delta için sadece Delta olan satırlara gider, diğer airline'lara bakmaz.
CREATE INDEX idx_tweet_metrics_airline ON tweet_metrics (airline);

-- Prune işlemi DELETE FROM tweet_metrics WHERE window_end <= ? kullanır.
-- silinecek eski satırları direkt bulur, tüm tabloyu taramaz
CREATE INDEX idx_tweet_metrics_window_end ON tweet_metrics (window_end);


-- ---------------------------------------------------------------------
-- Batch Layer Tablosu
-- Range Partitioning: window_start alanına göre aylık bölümleme. Neden partitioning?
--   - Veri sürekli büyür
--   - Sorguların çoğu zaman aralığı bazlı (WHERE window_start BETWEEN ...)
--   - PostgreSQL sadece ilgili partition'ı tarar (Partition Pruning)
--   - Eski verileri silmek kolaylaşır (DROP PARTITION vs DELETE)
CREATE TABLE batch_tweet_metrics (
    id              SERIAL,
    airline         VARCHAR(100) NOT NULL,
    tweet_count     BIGINT,
    positive_count  BIGINT,
    negative_count  BIGINT,
    neutral_count   BIGINT,
    positive_ratio  DOUBLE PRECISION,
    negative_ratio  DOUBLE PRECISION,
    avg_retweet     DOUBLE PRECISION,
    max_retweet     BIGINT,
    tweet_rate      DOUBLE PRECISION,
    window_start    TIMESTAMP NOT NULL,
    window_end      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
) PARTITION BY RANGE (window_start);

-- Batch Layer Indexleri (partition tablolarına otomatik uygulanır)
CREATE INDEX idx_batch_airline ON batch_tweet_metrics (airline);
CREATE INDEX idx_batch_window_start ON batch_tweet_metrics (window_start);

-- Aylık Partition'lar
-- Verimiz Şubat 2015 civarı olduğu için o dönemi kapsıyoruz.
-- Yeni aylar için partition eklemek gerekir (veya default partition yakalar). 
CREATE TABLE batch_tweet_metrics_2015_01 PARTITION OF batch_tweet_metrics
    FOR VALUES FROM ('2015-01-01') TO ('2015-02-01');

CREATE TABLE batch_tweet_metrics_2015_02 PARTITION OF batch_tweet_metrics
    FOR VALUES FROM ('2015-02-01') TO ('2015-03-01');

CREATE TABLE batch_tweet_metrics_2015_03 PARTITION OF batch_tweet_metrics
    FOR VALUES FROM ('2015-03-01') TO ('2015-04-01');

-- Default partition: Yukarıdaki aralıklara uymayan tüm veriler buraya düşer.
-- Böylece beklenmeyen tarihli veriler kaybolmaz, sistem hata vermez.
CREATE TABLE batch_tweet_metrics_default PARTITION OF batch_tweet_metrics DEFAULT;


-- ---------------------------------------------------------------------
-- Staging Table (Atomic Swap için)
-- Spark batch job'u verileri önce bu tabloya yazar.
-- Yazma bitince tek transaction içinde ana tabloyla yer değiştirilir (RENAME). 
-- Böylece unified_metrics view'ı hiçbir zaman boş batch verisi görmez.
-- Özellikle dashboard işlemleri için şarttır.
-- NOT: Staging tablosu da partitioned olmalı çünkü swap sonrası bu tablo ana tablo rolünü üstlenecek.
CREATE TABLE batch_tweet_metrics_staging (
    id              SERIAL,
    airline         VARCHAR(100) NOT NULL,
    tweet_count     BIGINT,
    positive_count  BIGINT,
    negative_count  BIGINT,
    neutral_count   BIGINT,
    positive_ratio  DOUBLE PRECISION,
    negative_ratio  DOUBLE PRECISION,
    avg_retweet     DOUBLE PRECISION,
    max_retweet     BIGINT,
    tweet_rate      DOUBLE PRECISION,
    window_start    TIMESTAMP NOT NULL,
    window_end      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
) PARTITION BY RANGE (window_start);

-- Staging partition'ları (ana tablo ile aynı yapı)
CREATE TABLE batch_staging_2015_01 PARTITION OF batch_tweet_metrics_staging
    FOR VALUES FROM ('2015-01-01') TO ('2015-02-01');

CREATE TABLE batch_staging_2015_02 PARTITION OF batch_tweet_metrics_staging
    FOR VALUES FROM ('2015-02-01') TO ('2015-03-01');

CREATE TABLE batch_staging_2015_03 PARTITION OF batch_tweet_metrics_staging
    FOR VALUES FROM ('2015-03-01') TO ('2015-04-01');

CREATE TABLE batch_staging_default PARTITION OF batch_tweet_metrics_staging DEFAULT;



















-- =============================================
-- Unified Serving Layer View
-- Speed Layer (tweet_metrics) + Batch Layer (batch_tweet_metrics)
-- Batch layer has the "truth", Speed layer provides the most recent "delta"
-- =============================================
CREATE OR REPLACE VIEW unified_metrics AS
SELECT 
    airline, tweet_count, positive_count, negative_count, neutral_count,
    positive_ratio, negative_ratio, avg_retweet, max_retweet,
    tweet_rate, window_start, window_end, 'BATCH' as source
FROM batch_tweet_metrics
UNION ALL
SELECT 
    airline, tweet_count, positive_count, negative_count, neutral_count,
    positive_ratio, negative_ratio, avg_retweet, max_retweet,
    tweet_rate, window_start, window_end, 'REAL-TIME' as source
FROM tweet_metrics
WHERE window_start >= COALESCE((SELECT MAX(window_end) FROM batch_tweet_metrics), '1970-01-01');
