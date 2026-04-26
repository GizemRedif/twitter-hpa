// MongoDB init script: tweet_alerts koleksiyonunu ve index'leri oluşturur.
// Docker container ilk kez başlatıldığında otomatik çalışır.
// Özetle bu dosya; veritabanını oluşturuyor, içine bir koleksiyon ekliyor ve verilerin hızlıca bulunabilmesi için indexler tanımlıyor.

// twitter_hpa adında bir veritabanı oluştur
db = db.getSiblingDB("twitter_hpa");


// -------- tweet_alerts koleksiyonu: tüm tweet uyarılarının saklanacağı koleksiyon --------

// tweet_alerts adında bir koleksiyon oluştur. Gelen tweet uyarıları bu klasörün içinde saklanacaktır.
db.createCollection("tweet_alerts");

// tweet_id üzerinden duplicate kontrolü için unique index. Aynı tweet'in tekrar eklenmesini engeller.
db.tweet_alerts.createIndex({ "tweet_id": 1 }, { unique: true });


// -------- Compound Indexler --------
// Tek alanlı indexler yerine, birlikte sorgulanan alanları kapsayan compound indexler oluşturuyoruz.
// Örnek: { airline: 1, created_at: -1 } indexi şu sorguları hızlandırır:
//   - db.tweet_alerts.find({ airline: "United" }).sort({ created_at: -1 })
//   - db.tweet_alerts.find({ airline: "United" })
// Bu sayede ayrı ayrı { airline: 1 } ve { created_at: -1 } indexlerine gerek kalmaz → daha az disk kullanımı, daha hızlı yazma.

// Compound Index 1: Airline bazlı filtreleme + zamana göre sıralama
// Kullanım: "United Airlines'ın son 24 saatteki alertleri" gibi sorgular
db.tweet_alerts.createIndex({ "airline": 1, "created_at": -1 });

// Compound Index 2: Airline + Sentiment bazlı analiz sorguları
// Kullanım: "Delta Airlines'ın negative alertleri" gibi sorgular
db.tweet_alerts.createIndex({ "airline": 1, "airline_sentiment": 1 });

// Standalone Index: Tüm alertleri zamana göre sıralama (airline filtresi olmadan)
// Kullanım: "Son gelen 100 alert" gibi global sorgular
db.tweet_alerts.createIndex({ "created_at": -1 });

print("tweet_alerts collection and indexes created successfully!");
