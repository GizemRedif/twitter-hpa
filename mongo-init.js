// MongoDB init script: tweet_alerts koleksiyonunu ve index'leri oluşturur.
// Docker container ilk kez başlatıldığında otomatik çalışır.
// Özetle bu dosya; veritabanını oluşturuyor, içine bir koleksiyon ekliyor ve verilerin hızlıca bulunabilmesi için indexler tanımlıyor.

// twitter_hpa adında bir veritabanı oluştur
db = db.getSiblingDB("twitter_hpa");


// ── tweet_alerts koleksiyonu: tüm tweet uyarılarının saklanacağı koleksiyon ──────────

// tweet_alerts adında bir koleksiyon oluştur. Gelen tweet uyarıları bu klasörün içinde saklanacaktır.
db.createCollection("tweet_alerts");

// tweet_id üzerinden duplicate kontrolü için unique index. Aynı tweet'in tekrar eklenmesini engeller.
db.tweet_alerts.createIndex({ "tweet_id": 1 }, { unique: true });

// airline bazlı sorgular için index. Hangi havayolunun kaç tweet'i olduğunu hızlıca bulmak için.
db.tweet_alerts.createIndex({ "airline": 1 });

// zaman bazlı sorgular için index (Tweetleri en yeniden en eskiye doğru sıralamayı sağlar)
db.tweet_alerts.createIndex({ "created_at": -1 });

print("tweet_alerts collection and indexes created successfully!");



// ── tweets_raw koleksiyonu: tüm tweet'lerin saklanacağı koleksiyon ──────────

db.createCollection("tweets_raw");

db.tweets_raw.createIndex({ "tweet_id": 1 }, { unique: true });

db.tweets_raw.createIndex({ "airline": 1 });

db.tweets_raw.createIndex({ "airline_sentiment": 1 });

db.tweets_raw.createIndex({ "created_at": -1 });

print("tweets_raw collection and indexes created successfully!");
