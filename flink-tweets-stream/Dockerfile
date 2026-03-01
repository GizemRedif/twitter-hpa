# =============================================================
# Flink Job - Multi-Stage Docker Build
# =============================================================
# Bu Dockerfile, Flink uygulamasını iki aşamada hazırlar:
#   1. BUILD aşaması: Maven ile Java kodunu derleyip fat JAR üretir 
#       Bu aşama, Java kodlarının bilgisayarın anlayacağı bir dile çevrildiği yerdir. 
#       Maven aracısı kullanılarak kütüphaneler indirilir ve yazdığımız kodlar birleştirilip tek paket haline gelir. 
#       Sonuç, Fat JAR dediğimiz, içinde her şeyin olduğu dev bir uygulama dosyasıdır.
#   2. RUNTIME aşaması: Üretilen JAR'ı Flink image'ına kopyalar
#       Bu aşama, derlenmiş Java kodunu Flink'in çalışacağı ortama yerleştirir.
#       Fat JAR, Flink'in ihtiyaç duyduğu tüm kütüphaneleri içerdiği için, bu aşamada sadece JAR dosyasını Flink imajına kopyalamak yeterlidir.
#
# Avantajı: "docker compose up --build" ile her şey otomatik olur.
# Maven cache Docker layer'ında tutulur → tekrar build hızlıdır.
# =============================================================


# -------------------- AŞAMA 1: BUILD --------------------
# Maven + JDK içeren imajı kullanarak projeyi derliyoruz.
FROM maven:3.9-eclipse-temurin-8 AS builder

# Maven build'in yapılacağı dizin
WORKDIR /build

# Önce sadece pom.xml kopyalanır ve bağımlılıklar indirilir.
# Neden ayrı adım? Docker cache optimizasyonu:
#   → pom.xml değişmedikçe bağımlılıklar tekrar indirilmez.
#   → Sadece kaynak kod değişirse yeniden derleme yapılır.
COPY pom.xml .
RUN mvn dependency:go-offline -B

# Kaynak kodları kopyala ve fat JAR oluştur
# -DskipTests: testleri atla (CI/CD'de ayrıca çalıştırılır)
# -B: batch mode (interaktif olmayan çıktı, log temizliği için)
COPY src ./src
RUN mvn clean package -DskipTests -B


# -------------------- AŞAMA 2: RUNTIME --------------------
# Resmi Flink 1.18 imajını kullanıyoruz (docker-compose.yml ile aynı versiyon)
FROM flink:1.18

# Build aşamasında üretilen fat JAR'ı Flink'in usrlib dizinine kopyala.
# /opt/flink/usrlib/ dizinindeki JAR'lar Flink tarafından otomatik olarak classpath'e eklenir, ancak job submit işlemi ayrıca yapılmalıdır.
COPY --from=builder /build/target/flink-tweets-stream-1.0-SNAPSHOT.jar /opt/flink/usrlib/flink-tweets-stream.jar

# Özel başlatma scriptini kopyala ve çalıştırılabilir yap.
# Bu script: JobManager başlat → REST API hazır olana kadar bekle → job submit et
COPY docker-entrypoint-override.sh /opt/flink/usrlib/docker-entrypoint-override.sh
RUN chmod +x /opt/flink/usrlib/docker-entrypoint-override.sh
