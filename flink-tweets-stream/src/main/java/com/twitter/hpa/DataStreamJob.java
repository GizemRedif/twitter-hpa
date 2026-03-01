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
import org.apache.flink.api.common.serialization.SerializationSchema;
import org.apache.flink.api.common.typeinfo.TypeInformation;
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

 /**
  * Tweet Metrics için iş akışı:
  * Kafkadan gelen binary veri AvroSerDe ile Java nesnesine (GenericRecord) çevrilir
  * Flink işleme yapar. PojoSerializer, window işlemi bitene kadar veriyi saklar (TweetMetrics POJO'su ile)
  * AvroSerDe işlem bitince veriyi yine kafkaya binary (Avro) olarak gönderir
  */

public class DataStreamJob {

	// Schema Registry URL: Docker ağı içerisinden Schema Registry'ye erişim adresi.
	private static final String SCHEMA_REGISTRY_URL = "http://schema-registry:8081";
	private static final String TWEET_DATE_PATTERN = "yyyy-MM-dd HH:mm:ss Z";

	// tweet metrics için POJO sınıfı (Avro şeması ile birebir eşleşmelidir)
	// Bu sınıf, Flink'in AvroSerDe'si tarafından kullanılmak üzere tasarlanmıştır.
	// AvroSerDe: Avro Serialization ve Deserialization
	// Flink artık dahili pencere durumu için Kryo yerine yüksek performanslı PojoSerializer'ını kullanıyor ve bu da çökmeyi ortadan kaldırıyor.
	public static class TweetMetrics {
		public String airline;
		public long tweet_count;
		public long positive_count;
		public long negative_count;
		public long neutral_count;
		public double positive_ratio;
		public double negative_ratio;
		public double avg_retweet;
		public long max_retweet;
		public double tweet_rate;
		public String window_start;
		public String window_end;

		public TweetMetrics() {} // Flink için gerekli boş constructor
	}

	public static void main(String[] args) throws Exception {

		// Flink execution environment
		final StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

		// Paralellik ayarı 
		env.setParallelism(2);

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

		// Kafka Source: tweets.raw topic'ten AVRO mesajları okur (Schema Registry ile) Bu veriler aşağıda işlenip metrics olacak.
		KafkaSource<GenericRecord> kafkaSource = KafkaSource.<GenericRecord>builder()
				.setBootstrapServers("kafka:29092")
				.setTopics("tweets.raw")
				.setGroupId("flink-tweets-group-v2")
				.setStartingOffsets(OffsetsInitializer.earliest())
				// Confluent Registry üzerinden AVRO deserialization
				.setValueOnlyDeserializer(ConfluentRegistryAvroDeserializationSchema.forGeneric(tweetSchema,SCHEMA_REGISTRY_URL))
				.build();

		// Event Time: csv'deki tweet_created alanı
		// Format: "2015-02-24 11:35:52 -0800"  Z: Offset değeri (örneğin -0800, bu zamanın UTC'den 8 saat geri olduğunu gösterir)
		final String tweetDatePattern = "yyyy-MM-dd HH:mm:ss Z";

		// Bu kodun temel amacı, Flink'e verinin içindeki saati nasıl okuyacağını ve geciken verilerle nasıl başa çıkacağını öğretmektir.
		WatermarkStrategy<GenericRecord> watermarkStrategy = WatermarkStrategy
				.<GenericRecord>forBoundedOutOfOrderness(Duration.ofSeconds(5))     //5 saniyelik tolerans 
				.withIdleness(Duration.ofSeconds(30))                               //Eğer bir kanaldan (partitiondan) 30 saniye boyunca hiç veri gelmezse, kanalı idle olarak işaretle ve diğer kanallardan gelenlerle windowing işlemine devam et 
																					//(olmazsa bir kanal veri göndermeyi durdurduğunda tüm sistemin watermark'ı (saati) durabilir ve windowlar asla kapanmayabilir
				.withTimestampAssigner((record, kafkaTimestamp) -> {
					String tweetCreated = record.get("tweet_created").toString();   //"tweet_created" string alanını al
					return ZonedDateTime.parse(tweetCreated, DateTimeFormatter.ofPattern(tweetDatePattern)).toInstant().toEpochMilli();  // String'i parse edip epoch millisecond'a çevir (flink bunu anlar)						
				});

		// Kafka'dan AVRO veri akışı oluştur (event time ile)
		//fromSource(...) = "Bu kaynaktan veri oku ve bir DataStream oluştur"
		DataStream<GenericRecord> tweetStream = env.fromSource(
				kafkaSource,          // Source: tweets.raw topic'i
				watermarkStrategy,    // Event Time + Watermark (Yukarıda tanımlandı)
				"Kafka Source - tweets.raw"  // Source Name 
		);

		// Gelen tweet'leri konsola yazdır (Test için)
		tweetStream.print();




		//------------------------------ Negatif Sentiment Filtresi (tweets.alert topic'e yazma) ---------------------------
		// Negatif sentiment'li tweet'leri filtrele (GenericRecord olarak kalır)
		DataStream<GenericRecord> negativeTweets = tweetStream.filter(record -> "negative".equals(record.get("airline_sentiment").toString()));

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
								)).build()).build();

		// Negatif tweet'leri tweets.alert topic'ine gönder
		negativeTweets.sinkTo(alertSink);






		// ----------------- Stream Aggregation: Her airline için 1 dakikalık Tumbling Window (tweets.metrics topic'e yazma) ----------------------
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

		//Ham tweet verileri (tweetStream), airline'a göre anlamlı istatistiklere dönüştürülüyor
		DataStream<TweetMetrics> metricsPojo = tweetStream 
				.keyBy(record -> record.get("airline").toString())  		// airline alanına göre key'le (Her metric mesajı, airline'a göre gruplandırılır)
				.window(TumblingEventTimeWindows.of(Time.minutes(1)))  		// 1 dakikalık tumbling window (event time tabanlı)
				
				// Her pencerede airline başına tweet ve sentiment say
				// AggregateFunction<Girdi, Accumulator, Çıktı>    
				//   Girdi:       GenericRecord (her tweet) 
				//   Accumulator: Tuple8<airline, totalCount, positiveCount, negativeCount, neutralCount, minTimestamp, totalRetweetSum, maxRetweet>
				//   Çıktı:       TweetMetrics (AVRO metrik kaydı)
				// (Accumulator = Ara toplam tutucu - Window boyunca verileri biriktiren geçici depo, sayaçlar burada tutulur)
				.aggregate(new AggregateFunction<GenericRecord, Tuple8<String, Long, Long, Long, Long, Long, Long, Long>, TweetMetrics>() {
					// Window açıldığında çağrılır — boş bir sayaç oluşturur
					@Override
					public Tuple8<String, Long, Long, Long, Long, Long, Long, Long> createAccumulator() {
						return Tuple8.of("", 0L, 0L, 0L, 0L, Long.MAX_VALUE, 0L, 0L);
					}
					// f0: airline, f1: totalCount, f2: positiveCount, f3: negativeCount, f4: neutral_count, f5: minTimestamp, f6: totalRetweetSum, f7: maxRetweet
					
					// Her tweet geldiğinde çağrılır - sayaçları günceller
					@Override
					public Tuple8<String, Long, Long, Long, Long, Long, Long, Long> add(GenericRecord record, Tuple8<String, Long, Long, Long, Long, Long, Long, Long> accumulator) {
						String airline = record.get("airline").toString();
						String sentiment = record.get("airline_sentiment").toString();
						long timestamp = ZonedDateTime.parse(record.get("tweet_created").toString(), DateTimeFormatter.ofPattern(tweetDatePattern)).toInstant().toEpochMilli(); // String'i parse edip epoch millisecond'a çevir
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

					// Window kapandığında çağrılır — accumulator'dan POJO sonuç üretir
					@Override
					public TweetMetrics getResult(Tuple8<String, Long, Long, Long, Long, Long, Long, Long> accumulator) {
						TweetMetrics m = new TweetMetrics();
						m.airline = accumulator.f0;
						m.tweet_count = accumulator.f1;
						m.positive_count = accumulator.f2;
						m.negative_count = accumulator.f3;
						m.neutral_count = accumulator.f4;
						m.positive_ratio = accumulator.f1 > 0 ? (double) accumulator.f2 / accumulator.f1 : 0.0;
						m.negative_ratio = accumulator.f1 > 0 ? (double) accumulator.f3 / accumulator.f1 : 0.0;
						m.avg_retweet = accumulator.f1 > 0 ? (double) accumulator.f6 / accumulator.f1 : 0.0;
						m.max_retweet = accumulator.f7;
						m.tweet_rate = accumulator.f1 / 60.0;
						long windowStart = accumulator.f5 - (accumulator.f5 % 60000);
						m.window_start = java.time.Instant.ofEpochMilli(windowStart).toString();
						m.window_end = java.time.Instant.ofEpochMilli(windowStart + 60000).toString();
						return m;
					}

					// Paralel çalışmada iki ara sonucu birleştirir
					@Override
					public Tuple8<String, Long, Long, Long, Long, Long, Long, Long> merge(Tuple8<String, Long, Long, Long, Long, Long, Long, Long> a, Tuple8<String, Long, Long, Long, Long, Long, Long, Long> b) {
						return Tuple8.of(a.f0, a.f1 + b.f1, a.f2 + b.f2, a.f3 + b.f3, a.f4 + b.f4, Math.min(a.f5, b.f5), a.f6 + b.f6, Math.max(a.f7, b.f7));
					}
				});

		// Kafka Sink: Metrikleri "tweets.metrics" topic'ine AVRO formatında gönderen sink yapılandırması
		KafkaSink<TweetMetrics> metricsSink = KafkaSink.<TweetMetrics>builder()
				.setBootstrapServers("kafka:29092") // Kafka broker adresi
				.setRecordSerializer(KafkaRecordSerializationSchema.builder()
						.setTopic("tweets.metrics")
						.setValueSerializationSchema(new SerializationSchema<TweetMetrics>() {
							// Serileştirme sırasında Schema Registry ile konuşacak olan iç serileştirici
							private transient ConfluentRegistryAvroSerializationSchema<GenericRecord> inner;
							// Çıkış şeması (AVRO formatında)
							private transient Schema schema;

							@Override
							public void open(InitializationContext context) throws Exception {
								// 1. TweetMetrics için AVRO şemasını tanımla (Schema Registry'ye kaydedilecek şema)
								schema = new Schema.Parser().parse(
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
										"{\"name\":\"window_end\",\"type\":\"string\"}]}");
								
								// 2. Confluent Registry destekli GenericRecord serileştiricisini başlat
								inner = ConfluentRegistryAvroSerializationSchema.forGeneric("tweets.metrics-value", schema, SCHEMA_REGISTRY_URL);
								inner.open(context);
							}

							@Override
							public byte[] serialize(TweetMetrics m) {
								// 3. POJO'yu (Java nesnesi) AVRO GenericRecord nesnesine dönüştür
								GenericRecord record = new GenericData.Record(schema);
								record.put("airline", m.airline);
								record.put("tweet_count", m.tweet_count);
								record.put("positive_count", m.positive_count);
								record.put("negative_count", m.negative_count);
								record.put("neutral_count", m.neutral_count);
								record.put("positive_ratio", m.positive_ratio);
								record.put("negative_ratio", m.negative_ratio);
								record.put("avg_retweet", m.avg_retweet);
								record.put("max_retweet", m.max_retweet);
								record.put("tweet_rate", m.tweet_rate);
								record.put("window_start", m.window_start);
								record.put("window_end", m.window_end);
								
								// 4. GenericRecord'u binary AVRO formatına serileştir ve döndür
								return inner.serialize(record);
							}
						}).build()
				).build();

		metricsPojo.sinkTo(metricsSink);




		// ----------------- Flink job'ını başlat -----------------
		env.execute("Twitter Tweets Stream Processing");
	}
}
