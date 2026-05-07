#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>

#include "secrets.h"

// Pines de sensores y actuador.
const int VOLTAGE_SENSOR_PIN = 34;
const int CURRENT_SENSOR_PIN = 35;
const int RELAY_PIN = 23;

// Valores de calibracion.
const float VOLTAGE_CALIBRATION_FACTOR = 313.5;
const float CURRENT_SENSITIVITY = 0.033;
const float CURRENT_NOISE_THRESHOLD = 0.5;

// Logica del rele.
#define RELAY_ON LOW
#define RELAY_OFF HIGH

// Variables globales para offsets.
int voltageOffset = 2048;
int currentOffset = 2048;

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_OFF);

  // Realizar esta calibracion con los sensores sin carga.
  Serial.println("Calibrando offset de voltaje (sin carga)...");
  long v_sum = 0;
  for (int i = 0; i < 500; i++) {
    v_sum += analogRead(VOLTAGE_SENSOR_PIN);
    delay(1);
  }
  voltageOffset = v_sum / 500;
  Serial.print("Offset de voltaje medido: ");
  Serial.println(voltageOffset);

  Serial.println("Calibrando offset de corriente (sin carga)...");
  long c_sum = 0;
  for (int i = 0; i < 500; i++) {
    c_sum += analogRead(CURRENT_SENSOR_PIN);
    delay(1);
  }
  currentOffset = c_sum / 500;
  Serial.print("Offset de corriente medido: ");
  Serial.println(currentOffset);

  Serial.println("Calibracion completada.");
  delay(2000);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Conectando a WiFi...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConectado a la red WiFi.");
}

void loop() {
  float voltage = getAverageVoltage();
  float current = getAverageCurrent();

  if (current < CURRENT_NOISE_THRESHOLD) {
    current = 0.0;
  }

  if (voltage < 17.0) {
    voltage = 0.0;
  }

  Serial.printf("Enviando -> Voltaje: %.2f V, Corriente: %.3f A\n", voltage, current);

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SERVER_UPDATE_URL);
    http.setTimeout(5000);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<200> doc;
    doc["voltage"] = voltage;
    doc["current"] = current;
    String jsonPayload;
    serializeJson(doc, jsonPayload);

    int httpResponseCode = http.POST(jsonPayload);

    if (httpResponseCode == 200) {
      String response = http.getString();
      StaticJsonDocument<100> responseDoc;
      deserializeJson(responseDoc, response);
      const char* relayState = responseDoc["relay_state"];

      if (strcmp(relayState, "ON") == 0) {
        digitalWrite(RELAY_PIN, RELAY_ON);
      } else {
        digitalWrite(RELAY_PIN, RELAY_OFF);
      }
    } else {
      Serial.printf("Error en la peticion POST. Codigo: %d\n", httpResponseCode);
      digitalWrite(RELAY_PIN, RELAY_OFF);
    }
    http.end();
  }

  delay(2000);
}

float getAverageVoltage() {
  const int NUM_SAMPLES = 5;
  float sum = 0;
  for (int i = 0; i < NUM_SAMPLES; i++) {
    sum += getVoltageRMS();
    delay(10);
  }
  return sum / NUM_SAMPLES;
}

float getAverageCurrent() {
  const int NUM_SAMPLES = 10;
  float sum = 0;
  for (int i = 0; i < NUM_SAMPLES; i++) {
    sum += getCurrentRMS();
    delay(10);
  }
  return sum / NUM_SAMPLES;
}

float getVoltageRMS() {
  long sumOfSquares = 0;
  int sampleCount = 0;
  unsigned long startTime = millis();

  while (millis() - startTime < 100) {
    int sensorValue = analogRead(VOLTAGE_SENSOR_PIN);
    long diff = sensorValue - voltageOffset;
    sumOfSquares += diff * diff;
    sampleCount++;
  }

  if (sampleCount == 0) return 0;

  double meanSquare = (double)sumOfSquares / sampleCount;
  double rmsValueADC = sqrt(meanSquare);
  float vRmsCalculated = rmsValueADC * (3.3 / 4095.0);
  float vRmsReal = vRmsCalculated * VOLTAGE_CALIBRATION_FACTOR;
  return vRmsReal;
}

float getCurrentRMS() {
  long sumOfSquares = 0;
  int sampleCount = 0;
  unsigned long startTime = millis();

  while (millis() - startTime < 100) {
    int sensorValue = analogRead(CURRENT_SENSOR_PIN);
    long diff = sensorValue - currentOffset;
    sumOfSquares += diff * diff;
    sampleCount++;
  }

  if (sampleCount == 0) return 0;

  double meanSquare = (double)sumOfSquares / sampleCount;
  double rmsValueADC = sqrt(meanSquare);
  float vRms = rmsValueADC * (3.3 / 4095.0);
  float iRms = vRms / CURRENT_SENSITIVITY;
  return iRms;
}
