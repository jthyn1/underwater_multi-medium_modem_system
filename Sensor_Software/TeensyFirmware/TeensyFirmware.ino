#include <Wire.h>
#include "MS5837.h"
#include <Adafruit_GPS.h>
#define GPSSerial Serial1
#define GPSECHO false

MS5837 sensor;
Adafruit_GPS GPS(&GPSSerial);

const int WATER_PIN = 23;
const unsigned long sampleRate = 500;
unsigned long lastRead = 0;
int waterValue = 0;
float pressure = 0.00;
float temp = 0.00;
float alti = 0.00;
float depth = 0.00;
char lat;
char lon;
float latitude = 0.0;
float longitude = 0.0;
double speed = 0.0;

void setup() {
  // put your setup code here, to run once:
  pinMode(WATER_PIN, INPUT);
  Serial.begin(115200);
  delay(1000);
  Wire.begin();
  GPS.begin(9600);

  // BlueRobotics init error detection
  while (!sensor.init()) {
    Serial.println("Init failed!");
    Serial.println("Are SDA/SCL connected correctly?");
    Serial.println("Blue Robotics Bar30: White=SDA, Green=SCL");
    Serial.println("\n\n\n");
    delay(5000);
  }

  sensor.setFluidDensity(997); // kg/m^3 (freshwater, 1029 for seawater)
  GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA); // RMC (recommended minimum) and GGA (fix data) including altitude
  GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ); // 1 Hz update rate
  GPS.sendCommand(PGCMD_ANTENNA); // Request updates on antenna status, comment out to keep quiet
}

void paramRead() {

  sensor.read();

  waterValue = analogRead(WATER_PIN);

  alti = sensor.altitude();
  temp = sensor.temperature();
  pressure = sensor.pressure();
  depth = sensor.depth();
}

void GPSRead() {
  char c = GPS.read();
  //from code example
  // if you want to debug, this is a good time to do it!
  if (GPSECHO && c) Serial.print(c);
  // if a sentence is received, we can check the checksum, parse it...
  if (GPS.newNMEAreceived()) {
    // a tricky thing here is if we print the NMEA sentence, or data
    // we end up not listening and catching other sentences!
    // so be very wary if using OUTPUT_ALLDATA and trying to print out data
    // Serial.print(GPS.lastNMEA()); // this also sets the newNMEAreceived() flag to false
    if (!GPS.parse(GPS.lastNMEA())) // this also sets the newNMEAreceived() flag to false
      return; // we can fail to parse a sentence in which case we should just wait for another
  }

  if (GPS.fix) {
    latitude = GPS.latitudeDegrees;
    longitude = GPS.longitudeDegrees;
    lat = GPS.lat;
    lon = GPS.lon;
    speed = GPS.speed;
  }



}

void loop() {

 if (millis() - lastRead >= sampleRate) {
  lastRead = millis();

  paramRead();
  GPSRead();

  Serial.printf("water value: %d\n", waterValue);
  Serial.printf("Temperature: %.2f\n", temp);
  Serial.printf("Altitude: %.2f\n", alti);
  Serial.printf("Pressure: %.2f\n", pressure);
  Serial.printf("Depth: %.2f\n", depth);

  Serial.printf("Latitude: %.6f ", latitude); Serial.printf("'%c\n", lat);
  Serial.printf("Longitude: %.6f ", longitude); Serial.printf("'%c\n", lon);
  Serial.printf("Speed: %.2f\n", speed);
  }

}
