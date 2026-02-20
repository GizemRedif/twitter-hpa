package com.twitter.hpa;

/**
 * Tweet verilerini temsil eden POJO sınıfı.
 *
 * NOT: Şu an DataStreamJob'da Avro GenericRecord kullanılıyor.
 * İleride SpecificRecord'a geçilirse, bu sınıf SpecificRecordBase'den
 * extend edilerek AVRO SpecificRecord olarak kullanılabilir.
 * Şimdilik referans amaçlı tutulur.
 */

public class Tweet {
    private String tweet_id;
    private String airline;
    private String airline_sentiment;
    private String text;
    private int retweet_count;
    private String tweet_created;

    // Boş constructor (deserialize için gerekli)
    public Tweet() {}

    // Getter & Setter'lar
    public String getTweet_id() { return tweet_id; }
    public void setTweet_id(String tweet_id) { this.tweet_id = tweet_id; }

    public String getAirline() { return airline; }
    public void setAirline(String airline) { this.airline = airline; }

    public String getAirline_sentiment() { return airline_sentiment; }
    public void setAirline_sentiment(String airline_sentiment) { this.airline_sentiment = airline_sentiment; }

    public String getText() { return text; }
    public void setText(String text) { this.text = text; }

    public int getRetweet_count() { return retweet_count; }
    public void setRetweet_count(int retweet_count) { this.retweet_count = retweet_count; }

    public String getTweet_created() { return tweet_created; }
    public void setTweet_created(String tweet_created) { this.tweet_created = tweet_created; }

    @Override
    public String toString() {
        return "Tweet{" +
                "tweet_id='" + tweet_id + '\'' +
                ", airline='" + airline + '\'' +
                ", sentiment='" + airline_sentiment + '\'' +
                ", retweet_count=" + retweet_count +
                '}';
    }
}
