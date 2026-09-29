/*
 * Bai thuc hanh so 2 - Firmware ESP32 (Wokwi)
 * DHT22 (nhiet do, do am) + HC-SR04 (khoang cach) -> JSON -> MQTT
 *
 * Luong du lieu:
 *   ESP32 (Wokwi) --MQTT--> broker.emqx.io  (topic: <NAMESPACE>/iot/sensor/room1)
 *   Mosquitto local --bridge--> nhan ve thanh topic iot/sensor/room1
 *   collector.py subscribe Mosquitto local -> MongoDB
 *
 * Wokwi khong truy cap duoc "localhost" cua may ban, vi vay ESP32 gui len
 * public broker, con Mosquitto tren may ban keo du lieu ve qua bridge.
 */
#define ARDUINOJSON_USE_LONG_LONG 1

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <ArduinoJson.h>
#include <time.h>
#include <sys/time.h>

// ================= CAU HINH =================
const char *WIFI_SSID = "Wokwi-GUEST";
const char *WIFI_PASSWORD = "";

const char *MQTT_SERVER = "broker.emqx.io"; // public broker
const int MQTT_PORT = 1883;

// Namespace rieng tren public broker de khong trung topic voi nguoi khac.
// PHAI trung voi remote-prefix trong server/mosquitto/config/mosquitto.conf
const char *TOPIC_NAMESPACE = "dungdinh60240505";

const char *DEVICE_ID = "esp32-room1";
const char *ROOM = "room1";

const char *NTP_SERVER_1 = "pool.ntp.org";
const char *NTP_SERVER_2 = "time.google.com";

const int DHT_PIN = 15;      // Data pin cua DHT22
const int TRIG_PIN = 5;      // Trig cua HC-SR04
const int ECHO_PIN = 18;     // Echo cua HC-SR04
const int LED_PIN = 2;       // LED bao trang thai gui du lieu
const int ALARM_LED_PIN = 4; // LED canh bao vat can qua gan

const unsigned long SEND_INTERVAL_MS = 5000;           // Chu ky gui du lieu
const unsigned long NTP_RESYNC_MS = 10UL * 60UL * 1000; // Dong bo lai NTP moi 10 phut
const float DISTANCE_ALARM_CM = 15.0;                  // Nguong canh bao khoang cach

// ================= BIEN TOAN CUC =================
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
DHTesp dht;

String topicTelemetry; // <NAMESPACE>/iot/sensor/<ROOM>
String topicStatus;    // <NAMESPACE>/iot/status/<ROOM>

unsigned long lastSend = 0;
unsigned long lastNtpSync = 0;
unsigned long sequenceNo = 0;

// Tiem loi qua Serial Monitor de demo tien xu ly:
//   n = lan gui tiep theo mat gia tri nhiet do (null)
//   s = lan gui tiep theo co gai nhieu (spike) nhiet do +30 do C
//   d = lan gui tiep theo gui lap lai goi tin truoc (duplicate)
bool injectNull = false;
bool injectSpike = false;
bool injectDuplicate = false;
char lastPayload[512] = {0};
size_t lastPayloadLen = 0;

// ---------------- THOI GIAN (NTP) ----------------
bool timeSynced()
{
  return time(nullptr) > 1700000000; // > nam 2023 => da dong bo
}

uint64_t epochMs()
{
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)(tv.tv_usec / 1000);
}

void syncTime(bool waitForSync)
{
  configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2); // UTC
  lastNtpSync = millis();
  if (!waitForSync)
    return;
  Serial.print("Sync NTP");
  unsigned long start = millis();
  while (!timeSynced() && millis() - start < 15000)
  {
    delay(300);
    Serial.print(".");
  }
  Serial.println(timeSynced() ? " OK" : " TIMEOUT (se gui khong kem ts)");
}

// ---------------- KET NOI WIFI ----------------
void connectWiFi()
{
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting WiFi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
    if (millis() - start > 20000)
    {
      Serial.println("\nWiFi timeout, retry...");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      start = millis();
    }
  }
  Serial.println(" connected");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

// ---------------- KET NOI MQTT ----------------
void connectMQTT()
{
  while (!mqttClient.connected())
  {
    String clientId = String(DEVICE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.print("Connecting MQTT...");
    // Last Will: neu ESP32 mat ket noi dot ngot, broker tu publish "offline"
    if (mqttClient.connect(clientId.c_str(), topicStatus.c_str(), 1, true, "offline"))
    {
      Serial.println(" connected");
      mqttClient.publish(topicStatus.c_str(), "online", true);
    }
    else
    {
      Serial.printf(" failed, rc=%d. Retry in 2 s\n", mqttClient.state());
      delay(2000);
    }
  }
}

// ---------------- DOC HC-SR04 ----------------
float readDistanceCm()
{
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000); // timeout 30ms (~5m)
  if (duration == 0)
    return NAN;
  return duration * 0.0343f / 2.0f;
}

float round2(float v)
{
  return roundf(v * 100.0f) / 100.0f;
}

// ---------------- LENH TIEM LOI TU SERIAL ----------------
void handleSerialCommands()
{
  while (Serial.available())
  {
    char c = Serial.read();
    if (c == 'n')
    {
      injectNull = true;
      Serial.println(">> Lan gui toi: temperature = null");
    }
    else if (c == 's')
    {
      injectSpike = true;
      Serial.println(">> Lan gui toi: spike nhiet do +30");
    }
    else if (c == 'd')
    {
      injectDuplicate = true;
      Serial.println(">> Gui lai goi tin truoc (duplicate)");
    }
  }
}

void setup()
{
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(ALARM_LED_PIN, OUTPUT);

  dht.setup(DHT_PIN, DHTesp::DHT22);

  topicTelemetry = String(TOPIC_NAMESPACE) + "/iot/sensor/" + ROOM;
  topicStatus = String(TOPIC_NAMESPACE) + "/iot/status/" + ROOM;

  connectWiFi();
  syncTime(true);

  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(512);
  mqttClient.setKeepAlive(30);

  Serial.print("Telemetry topic: ");
  Serial.println(topicTelemetry);
  Serial.println("Lenh Serial: n = null, s = spike, d = duplicate");
}

void loop()
{
  if (WiFi.status() != WL_CONNECTED)
    connectWiFi();
  if (!mqttClient.connected())
    connectMQTT();
  mqttClient.loop();
  handleSerialCommands();

  unsigned long now = millis();

  // Dong bo lai NTP dinh ky (quan trong tren Wokwi vi dong ho mo phong bi troi)
  if (now - lastNtpSync > NTP_RESYNC_MS)
    syncTime(false);

  if (injectDuplicate && lastPayloadLen > 0)
  {
    injectDuplicate = false;
    mqttClient.publish(topicTelemetry.c_str(), (const uint8_t *)lastPayload, lastPayloadLen, false);
    Serial.println("DUPLICATE sent");
  }

  if (now - lastSend < SEND_INTERVAL_MS)
    return;
  lastSend = now;

  TempAndHumidity data = dht.getTempAndHumidity();
  float distance = readDistanceCm();

  bool alarm = isfinite(distance) && distance < DISTANCE_ALARM_CM;
  digitalWrite(ALARM_LED_PIN, alarm ? HIGH : LOW);

  sequenceNo++;

  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["room"] = ROOM;
  doc["seq"] = sequenceNo;
  if (timeSynced())
    doc["ts"] = epochMs(); // epoch milliseconds (UTC) luc do xong cam bien

  // Cam bien loi (NaN) -> gui null thay vi bo ca goi tin,
  // de phia server xu ly missing value.
  float temperature = data.temperature;
  if (injectSpike && isfinite(temperature))
  {
    temperature += 30.0f;
    injectSpike = false;
  }
  if (injectNull)
  {
    temperature = NAN;
    injectNull = false;
  }

  if (isfinite(temperature))
    doc["temperature"] = round2(temperature);
  else
    doc["temperature"] = nullptr;

  if (isfinite(data.humidity))
    doc["humidity"] = round2(data.humidity);
  else
    doc["humidity"] = nullptr;

  if (isfinite(distance))
    doc["distance_cm"] = round2(distance);
  else
    doc["distance_cm"] = nullptr;

  doc["alarm"] = alarm;
  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;

  char payload[512];
  size_t len = serializeJson(doc, payload, sizeof(payload));

  bool ok = mqttClient.publish(topicTelemetry.c_str(), (const uint8_t *)payload, len, false);
  Serial.printf("%s | publish=%s\n", payload, ok ? "OK" : "FAILED");

  memcpy(lastPayload, payload, len);
  lastPayloadLen = len;

  digitalWrite(LED_PIN, HIGH);
  delay(80);
  digitalWrite(LED_PIN, LOW);
}
