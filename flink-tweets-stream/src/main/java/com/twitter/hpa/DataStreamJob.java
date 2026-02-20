package com.twitter.hpa;

import org.apache.avro.Schema;
import org.apache.avro.generic.GenericRecord;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * Flink uygulamasının ana giriş noktası (main class).
 * Kafka'daki "tweets_topic" topic'inden AVRO formatındaki tweet verilerini okur ve işler.
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

		// Kafka Source: tweets_topic'ten AVRO mesajları okur (Schema Registry ile)
		KafkaSource<GenericRecord> kafkaSource = KafkaSource.<GenericRecord>builder()
				.setBootstrapServers("kafka:29092")
				.setTopics("tweets_topic")
				.setGroupId("flink-tweets-group")
				.setStartingOffsets(OffsetsInitializer.earliest())
				// Confluent Registry üzerinden AVRO deserialization
				.setValueOnlyDeserializer(
						ConfluentRegistryAvroDeserializationSchema.forGeneric(
								new Schema.Parser().parse("{\"type\":\"record\",\"name\":\"Tweet\",\"namespace\":\"com.twitter.hpa\",\"fields\":[{\"name\":\"tweet_id\",\"type\":\"string\"},{\"name\":\"airline\",\"type\":\"string\"},{\"name\":\"airline_sentiment\",\"type\":\"string\"},{\"name\":\"text\",\"type\":\"string\"},{\"name\":\"retweet_count\",\"type\":\"int\"},{\"name\":\"tweet_created\",\"type\":\"string\"}]}"),
								SCHEMA_REGISTRY_URL
						)
				)
				.build();

		// Kafka'dan AVRO veri akışı oluştur
		DataStream<GenericRecord> tweetStream = env.fromSource(
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
