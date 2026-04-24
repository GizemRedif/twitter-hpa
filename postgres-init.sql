-- =============================================
-- tweets.metrics verilerini saklamak için
-- twitter_metrics DB ve tweet_metrics tablosu
-- =============================================

-- 1) twitter_metrics veritabanını oluştur
CREATE DATABASE twitter_metrics;

-- 2) twitter_metrics'e bağlan ve tabloyu oluştur
\connect twitter_metrics

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

-- =============================================
-- Batch Layer (PySpark) sonuçları için  
-- Speed Layer ile aynı yapı, farklı window (1 saat)
-- =============================================
CREATE TABLE batch_tweet_metrics (
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

-- =============================================
-- Staging Table (Atomic Swap için)
-- Spark batch job'u verileri önce bu tabloya yazar.
-- Yazma bitince tek transaction içinde ana tabloyla
-- yer değiştirilir (RENAME). Böylece unified_metrics
-- view'ı hiçbir zaman boş batch verisi görmez.
-- Özellikle dashboard işlemleri için şarttır. 
-- =============================================
CREATE TABLE batch_tweet_metrics_staging (
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
