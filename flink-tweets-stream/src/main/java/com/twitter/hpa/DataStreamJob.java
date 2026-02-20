package com.twitter.hpa;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * Flink uygulamasının ana giriş noktası (main class).
 * Kafka'daki "tweets_topic" topic'inden tweet verilerini okur ve işler.
 * Bu sınıf, Flink job'ının tanımlandığı ve çalıştırıldığı yerdir.
 */
public class DataStreamJob {

	public static void main(String[] args) throws Exception {

		// Flink execution environment
		final StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

		// Kafka Source: tweets_topic'ten JSON mesajları okur
		KafkaSource<String> kafkaSource = KafkaSource.<String>builder()
				.setBootstrapServers("kafka:29092")
				.setTopics("tweets_topic")
				.setGroupId("flink-tweets-group")
				.setStartingOffsets(OffsetsInitializer.earliest())
				.setValueOnlyDeserializer(new SimpleStringSchema())
				.build();

		// Kafka'dan veri akışı oluştur
		DataStream<String> tweetStream = env.fromSource(
				kafkaSource,
				WatermarkStrategy.noWatermarks(),
				"Kafka Source - tweets_topic"
		);

		// Gelen tweet'leri konsola yazdır
		tweetStream.print();

		// Flink job'ını başlat
		env.execute("Twitter Tweets Stream Processing");
	}
}
