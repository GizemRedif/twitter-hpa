package com.twitter.hpa;

import org.apache.avro.Schema;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericRecord;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.AggregateFunction;
import org.apache.flink.api.java.tuple.Tuple8;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroDeserializationSchema;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.TumblingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;

import java.time.Duration;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Flink uygulamasının ana giriş noktası (main class).
 * Kafka'daki "tweets.raw" topic'inden AVRO formatındaki tweet verilerini okur ve işler.
 * Negatif sentiment'e sahip tweet'leri "tweets.alert" topic'ine yazar.
 * Her 1 dakikada bir airline'lara göre tweet metriklerini hesaplar ve "tweets.metrics" topic'ine yazar.
 * Confluent Schema Registry üzerinden şema çözümlemesi yapar.
 */

public class DataStreamJob {

	// Schema Registry URL: Docker ağı içerisinden Schema Registry'ye erişim adresi.
	private static final String SCHEMA_REGISTRY_URL = "http://schema-registry:8081";

	public static void main(String[] args) throws Exception {

		// Flink execution environment
		final StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

		// Checkpoint: Kafka offset yönetimi için her 5 saniyede bir checkpoint alınır.
		env.enableCheckpointing(5000);

		// Tweet AVRO schema (source ve alert sink'te ortak kullanılır)
		Schema tweetSchema = new Schema.Parser().parse(
				"{\"type\":\"record\",\"name\":\"Tweet\",\"namespace\":\"com.twitter.hpa\"," +
				"\"fields\":[{\"name\":\"tweet_id\",\"type\":\"string\"}," +
				"{\"name\":\"airline\",\"type\":\"string\"}," +
				"{\"name\":\"airline_sentiment\",\"type\":\"string\"}," +
				"{\"name\":\"text\",\"type\":\"string\"}," +
				"{\"name\":\"retweet_count\",\"type\":\"int\"}," +
				"{\"name\":\"tweet_created\",\"type\":\"string\"}]}"
		);

		// Kafka Source: tweets_topic'ten AVRO mesajları okur (Schema Registry ile)
		KafkaSource<GenericRecord> kafkaSource = KafkaSource.<GenericRecord>builder()
				.setBootstrapServers("kafka:29092")
				.setTopics("tweets.raw")
				.setGroupId("flink-tweets-group")
				.setStartingOffsets(OffsetsInitializer.earliest())
				// Confluent Registry üzerinden AVRO deserialization
				.setValueOnlyDeserializer(
						ConfluentRegistryAvroDeserializationSchema.forGeneric(
								tweetSchema,
								SCHEMA_REGISTRY_URL
						)
				)
				.build();




		// Event Time: tweet_created alanını event time olarak kullan
		// Watermark: 5 saniyelik tolerans 
		// Format: "2015-02-24 11:35:52 -0800"
		DateTimeFormatter tweetDateFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss Z");

		WatermarkStrategy<GenericRecord> watermarkStrategy = WatermarkStrategy
				.<GenericRecord>forBoundedOutOfOrderness(Duration.ofSeconds(5))
				.withTimestampAssigner((record, kafkaTimestamp) -> {
					String tweetCreated = record.get("tweet_created").toString(); //"tweet_created" string alanını al
					return ZonedDateTime.parse(tweetCreated, tweetDateFormat) // String'i parse edip epoch millisecond'a çevir
							.toInstant()
							.toEpochMilli(); 
				});
		
		// Kafka'dan AVRO veri akışı oluştur (event time ile)
		//fromSource(...) = "Bu kaynaktan veri oku ve bir DataStream oluştur"
		DataStream<GenericRecord> tweetStream = env.fromSource(
				kafkaSource,          // Source
				watermarkStrategy,    // Event Time + Watermark
				"Kafka Source - tweets.raw"  // Source Name
		);

		// Gelen tweet'leri konsola yazdır
		tweetStream.print();

		// Negatif sentiment'li tweet'leri filtrele (GenericRecord olarak kalır)
		DataStream<GenericRecord> negativeTweets = tweetStream
				.filter(record -> "negative".equals(record.get("airline_sentiment").toString()));

		// Kafka Sink: Negatif tweet'leri tweets.alert topic'ine AVRO olarak yaz
		KafkaSink<GenericRecord> alertSink = KafkaSink.<GenericRecord>builder()
				.setBootstrapServers("kafka:29092")
				.setRecordSerializer(KafkaRecordSerializationSchema.builder()
						.setTopic("tweets.alert")
						.setValueSerializationSchema(
								ConfluentRegistryAvroSerializationSchema.forGeneric(
										"tweets.alert-value",
										tweetSchema,
										SCHEMA_REGISTRY_URL
								)
						).build()
				).build();

		// Negatif tweet'leri tweets.alert topic'ine gönder
		negativeTweets.sinkTo(alertSink);








		// ----------------- Stream Aggregation: Her airline için 1 dakikalık Tumbling Window ----------------------
		// TweetMetrics AVRO schema
		Schema metricsSchema = new Schema.Parser().parse(
				"{\"type\":\"record\",\"name\":\"TweetMetrics\",\"namespace\":\"com.twitter.hpa\"," +
				"\"fields\":[{\"name\":\"airline\",\"type\":\"string\"}," +
				"{\"name\":\"tweet_count\",\"type\":\"long\"}," +
				"{\"name\":\"positive_count\",\"type\":\"long\"}," +
				"{\"name\":\"negative_count\",\"type\":\"long\"}," +
				"{\"name\":\"neutral_count\",\"type\":\"long\"}," +
				"{\"name\":\"positive_ratio\",\"type\":\"double\"}," +
				"{\"name\":\"negative_ratio\",\"type\":\"double\"}," +
				"{\"name\":\"avg_retweet\",\"type\":\"double\"}," +
				"{\"name\":\"max_retweet\",\"type\":\"long\"}," +
				"{\"name\":\"tweet_rate\",\"type\":\"double\"}," +
				"{\"name\":\"window_start\",\"type\":\"string\"}," +
				"{\"name\":\"window_end\",\"type\":\"string\"}]}"
		);

		// TweetMetrics mesajlarını hesapla
		DataStream<GenericRecord> metrics = tweetStream
				.keyBy(record -> record.get("airline").toString())  // airline alanına göre key'le (Her metric mesajı, airline'a göre gruplandırılır)
				.window(TumblingEventTimeWindows.of(Time.minutes(1)))  // 1 dakikalık tumbling window (event time tabanlı)
				
				// Her pencerede airline başına tweet ve sentiment say, AVRO GenericRecord olarak döndür
				// AggregateFunction<Girdi, Accumulator, Çıktı>    
				//   Girdi:       GenericRecord (her tweet) 
				//   Accumulator: Tuple8<airline, totalCount, positiveCount, negativeCount, neutralCount, minTimestamp, totalRetweetSum, maxRetweet>
				//   Çıktı:       GenericRecord (AVRO metrik kaydı)
				// (Accumulator = Ara toplam tutucu - Window boyunca verileri biriktiren geçici depo, sayaçlar burada tutulur)
				.aggregate(new AggregateFunction<GenericRecord, Tuple8<String, Long, Long, Long, Long, Long, Long, Long>, GenericRecord>() {

					// Window açıldığında çağrılır — boş bir sayaç oluşturur
					@Override
					public Tuple8<String, Long, Long, Long, Long, Long, Long, Long> createAccumulator() {
						return Tuple8.of("", 0L, 0L, 0L, 0L, Long.MAX_VALUE, 0L, 0L);
					}
					// f0: airline, f1: totalCount, f2: positiveCount, f3: negativeCount, f4: neutralCount, f5: minTimestamp, f6: totalRetweetSum, f7: maxRetweet


					// Her tweet geldiğinde çağrılır - sayaçları günceller
					@Override
					public Tuple8<String, Long, Long, Long, Long, Long, Long, Long> add(GenericRecord record, Tuple8<String, Long, Long, Long, Long, Long, Long, Long> accumulator) {
						String airline = record.get("airline").toString();
						String sentiment = record.get("airline_sentiment").toString();
						long timestamp = ZonedDateTime.parse(record.get("tweet_created").toString(), tweetDateFormat) // String'i parse edip epoch millisecond'a çevir
								.toInstant().toEpochMilli();
						int retweetCount = (int) record.get("retweet_count");

						// Sentiment'e göre ilgili sayaç +1 artırılır
						long pos = accumulator.f2 + ("positive".equals(sentiment) ? 1 : 0);
						long neg = accumulator.f3 + ("negative".equals(sentiment) ? 1 : 0);
						long neu = accumulator.f4 + ("neutral".equals(sentiment) ? 1 : 0);

						// Yeni accumulator döndür
						return Tuple8.of(airline, accumulator.f1 + 1, pos, neg, neu,
								Math.min(accumulator.f5, timestamp), // window için en erken timestamp
								accumulator.f6 + retweetCount,        // toplam retweet
								Math.max(accumulator.f7, retweetCount) // max retweet
						);
					}

					// Window kapandığında çağrılır — accumulator'dan AVRO sonuç üretir
					@Override
					public GenericRecord getResult(Tuple8<String, Long, Long, Long, Long, Long, Long, Long> accumulator) {
						GenericRecord metric = new GenericData.Record(metricsSchema);
						metric.put("airline", accumulator.f0);
						metric.put("tweet_count", accumulator.f1);
						metric.put("positive_count", accumulator.f2);
						metric.put("negative_count", accumulator.f3);
						metric.put("neutral_count", accumulator.f4);
						// Sentiment oranları (0'a bölme koruması ile)
						metric.put("positive_ratio", accumulator.f1 > 0 ? (double) accumulator.f2 / accumulator.f1 : 0.0);
						metric.put("negative_ratio", accumulator.f1 > 0 ? (double) accumulator.f3 / accumulator.f1 : 0.0);
						// Retweet metrikleri
						metric.put("avg_retweet", accumulator.f1 > 0 ? (double) accumulator.f6 / accumulator.f1 : 0.0);
						metric.put("max_retweet", accumulator.f7);
						// Tweet rate: saniyede kaç tweet
						metric.put("tweet_rate", accumulator.f1 / 60.0);
						// Window başlangıç ve bitiş zamanları
						long windowStart = accumulator.f5 - (accumulator.f5 % 60000); // 1 dk'ya yuvarla
						metric.put("window_start", java.time.Instant.ofEpochMilli(windowStart).toString());
						metric.put("window_end", java.time.Instant.ofEpochMilli(windowStart + 60000).toString());
						return metric;
					}

					// Paralel çalışmada iki ara sonucu birleştirir
					@Override
					public Tuple8<String, Long, Long, Long, Long, Long, Long, Long> merge(Tuple8<String, Long, Long, Long, Long, Long, Long, Long> a, Tuple8<String, Long, Long, Long, Long, Long, Long, Long> b) {
						return Tuple8.of(a.f0, a.f1 + b.f1, a.f2 + b.f2, a.f3 + b.f3, a.f4 + b.f4, Math.min(a.f5, b.f5), a.f6 + b.f6, Math.max(a.f7, b.f7));
					}
				});





		// Kafka Sink: Metrikleri tweets.metrics topic'ine AVRO olarak yaz
		KafkaSink<GenericRecord> metricsSink = KafkaSink.<GenericRecord>builder()
				.setBootstrapServers("kafka:29092")
				.setRecordSerializer(KafkaRecordSerializationSchema.builder()
							.setTopic("tweets.metrics")
							.setValueSerializationSchema(ConfluentRegistryAvroSerializationSchema.forGeneric(
											"tweets.metrics-value",
											metricsSchema,
											SCHEMA_REGISTRY_URL
										)
									).build()
							).build();

		metrics.sinkTo(metricsSink);


















		// Flink job'ini baslat
		env.execute("Twitter Tweets Stream Processing");
	}
}
