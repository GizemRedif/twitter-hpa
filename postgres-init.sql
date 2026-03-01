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
