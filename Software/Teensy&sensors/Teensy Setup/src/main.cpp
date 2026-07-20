//  /* Blink
//  *
//  * Turns on an LED on for one second,
//  * then off for one second, repeatedly.
//  */
// /*#include "Arduino.h"

// // Set LED_BUILTIN if it is not defined by Arduino framework
// // #define LED_BUILTIN 13

// void setup()
// {
//   // initialize LED digital pin as an output.
//   pinMode(LED_BUILTIN, OUTPUT);
// }

// void loop()
// {
//   // turn the LED on (HIGH is the voltage level)
//   digitalWrite(LED_BUILTIN, HIGH);

//   // wait for a second
//   delay(4000);

//   // turn the LED off by making the voltage LOW
//   digitalWrite(LED_BUILTIN, LOW);

//    // wait for a second
//   delay(1000);
// }*/

// #include <Arduino.h>
// #include <Wire.h>
// #include "MS5837.h"

// MS5837 sensor;

// void setup() {
//   Serial.begin(115200);
//   delay(1000);

//   Wire.begin();

//   // Change to MS5837_02BA if you have the Bar02
//   sensor.setModel(MS5837::MS5837_30BA);

//   if (!sensor.init()) {
//     Serial.println("{\"status\":\"error\",\"message\":\"sensor init failed\"}");
//     while (true) {
//       delay(1000);
//     }
//   }

//   // 997 = freshwater, 1029 = seawater
//   sensor.setFluidDensity(997);

//   Serial.println("{\"status\":\"ok\",\"message\":\"sensor ready\"}");
// }

// void loop() {
//   sensor.read();
//     float pressure_mbar = sensor.pressure();
//     float temperature_c = sensor.temperature();
//     float depth_m = sensor.depth();

//     // Send one JSON line per reading
//     Serial.print("{\"pressure_mbar\":");
//     Serial.print(pressure_mbar, 3);
//     Serial.print(",\"temperature_c\":");
//     Serial.print(temperature_c, 3);
//     Serial.print(",\"depth_m\":");
//     Serial.print(depth_m, 3);
//     Serial.println("}");

//   if (sensor.pressure() == 0) {
//     Serial.println("{\"status\":\"warning\",\"message\":\"invalid reading\"}");
//     return;
//   }

//   delay(1000);
// }










//-----------------------------
// GPSS 
//-----------------------------

// #include <Arduino.h>
// #include <Wire.h>
// #include "MS5837.h"
// #include "Adafruit_GPS.h"

// // what's the name of the hardware serial port?
// #define GPSSerial Serial1

// // Connect to the GPS on the hardware port
// Adafruit_GPS GPS(&GPSSerial);

// // Set GPSECHO to 'false' to turn off echoing the GPS data to the Serial console
// // Set to 'true' if you want to debug and listen to the raw GPS sentences
// #define GPSECHO false

// uint32_t timer = millis();
// MS5837 sensor;

// void setup()
// {
//   //while (!Serial);  // uncomment to have the sketch wait until Serial is ready

//   // connect at 115200 so we can read the GPS fast enough and echo without dropping chars
//   // also spit it out
//   Serial.begin(115200);
//   Serial.println("Adafruit GPS library basic parsing test!");

//   // 9600 NMEA is the default baud rate for Adafruit MTK GPS's- some use 4800
//   GPS.begin(9600);
//   // uncomment this line to turn on RMC (recommended minimum) and GGA (fix data) including altitude
//   GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
//   // uncomment this line to turn on only the "minimum recommended" data
//   //GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCONLY);
//   // For parsing data, we don't suggest using anything but either RMC only or RMC+GGA since
//   // the parser doesn't care about other sentences at this time
//   // Set the update rate
//   GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ); // 1 Hz update rate
//   // For the parsing code to work nicely and have time to sort thru the data, and
//   // print it out we don't suggest using anything higher than 1 Hz

//   // Request updates on antenna status, comment out to keep quiet
//   GPS.sendCommand(PGCMD_ANTENNA);


//   Wire.begin();

//   // Change to MS5837_02BA if you have the Bar02
//   sensor.setModel(MS5837::MS5837_30BA);

//   if (!sensor.init()) {
//     Serial.println("{\"status\":\"error\",\"message\":\"sensor init failed\"}");
//     while (true) {
//       delay(1000);
//     }

  
//   }
//   // Ask for firmware version
//   GPSSerial.println(PMTK_Q_RELEASE);

//   sensor.setFluidDensity(997);

//   Serial.println("{\"status\":\"ok\",\"message\":\"sensor ready\"}");
// }

// void loop() // run over and over again
// {
//   // read data from the GPS in the 'main loop'
//   char c = GPS.read();
//   // if you want to debug, this is a good time to do it!
//   if (GPSECHO)
//     if (c) Serial.print(c);
//   // if a sentence is received, we can check the checksum, parse it...
//   if (GPS.newNMEAreceived()) {
//     // a tricky thing here is if we print the NMEA sentence, or data
//     // we end up not listening and catching other sentences!
//     // so be very wary if using OUTPUT_ALLDATA and trying to print out data
//     Serial.print(GPS.lastNMEA()); // this also sets the newNMEAreceived() flag to false
//     if (!GPS.parse(GPS.lastNMEA())) // this also sets the newNMEAreceived() flag to false
//       return; // we can fail to parse a sentence in which case we should just wait for another
//   }

//   // approximately every 2 seconds or so, print out the current stats
//   if (millis() - timer > 2000) {
//     timer = millis(); // reset the timer
//     Serial.print("\nTime: ");
//     if (GPS.hour < 10) { Serial.print('0'); }
//     Serial.print(GPS.hour, DEC); Serial.print(':');
//     if (GPS.minute < 10) { Serial.print('0'); }
//     Serial.print(GPS.minute, DEC); Serial.print(':');
//     if (GPS.seconds < 10) { Serial.print('0'); }
//     Serial.print(GPS.seconds, DEC); Serial.print('.');
//     if (GPS.milliseconds < 10) {
//       Serial.print("00");
//     } else if (GPS.milliseconds > 9 && GPS.milliseconds < 100) {
//       Serial.print("0");
//     }
//     Serial.println(GPS.milliseconds);
//     Serial.print("Date: ");
//     Serial.print(GPS.day, DEC); Serial.print('/');
//     Serial.print(GPS.month, DEC); Serial.print("/20");
//     Serial.println(GPS.year, DEC);
//     Serial.print("Fix: "); Serial.print((int)GPS.fix);
//     Serial.print(" quality: "); Serial.println((int)GPS.fixquality);
//     if (GPS.fix) {
//       Serial.print("Location: ");
//       Serial.print(GPS.latitude, 4); Serial.print(GPS.lat);
//       Serial.print(", ");
//       Serial.print(GPS.longitude, 4); Serial.println(GPS.lon);
//       Serial.print("Speed (knots): "); Serial.println(GPS.speed);
//       Serial.print("Angle: "); Serial.println(GPS.angle);
//       Serial.print("Altitude: "); Serial.println(GPS.altitude);
//       Serial.print("Satellites: "); Serial.println((int)GPS.satellites);
//       Serial.print("Antenna status: "); Serial.println((int)GPS.antenna);
//     }
//   }
//   sensor.read();
//     float pressure_mbar = sensor.pressure();
//     float temperature_c = sensor.temperature();
//     float depth_m = sensor.depth();

//     // Send one JSON line per reading
//     Serial.print("{\"pressure_mbar\":");
//     Serial.print(pressure_mbar, 3);
//     Serial.print(",\"temperature_c\":");
//     Serial.print(temperature_c, 3);
//     Serial.print(",\"depth_m\":");
//     Serial.print(depth_m, 3);
//     Serial.println("}");

//   if (sensor.pressure() == 0) {
//     Serial.println("{\"status\":\"warning\",\"message\":\"invalid reading\"}");
//     return;
//   }

//   delay(1000);



// }



#include <Arduino.h>#include <Arduino.h>
// #include <Wire.h>
// #include "MS5837.h"
// #include "Adafruit_GPS.h"

// // what's the name of the hardware serial port?
// #define GPSSerial Serial1

// // Connect to the GPS on the hardware port
// Adafruit_GPS GPS(&GPSSerial);

// // Set GPSECHO to 'false' to turn off echoing the GPS data to the Serial console
// // Set to 'true' if you want to debug and listen to the raw GPS sentences
// #define GPSECHO false

// uint32_t timer = millis();
// MS5837 sensor;

// void setup()
// {
//   //while (!Serial);  // uncomment to have the sketch wait until Serial is ready

//   // connect at 115200 so we can read the GPS fast enough and echo without dropping chars
//   // also spit it out
//   Serial.begin(115200);
//   Serial.println("Adafruit GPS library basic parsing test!");

//   // 9600 NMEA is the default baud rate for Adafruit MTK GPS's- some use 4800
//   GPS.begin(9600);
//   // uncomment this line to turn on RMC (recommended minimum) and GGA (fix data) including altitude
//   GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
//   // uncomment this line to turn on only the "minimum recommended" data
//   //GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCONLY);
//   // For parsing data, we don't suggest using anything but either RMC only or RMC+GGA since
//   // the parser doesn't care about other sentences at this time
//   // Set the update rate
//   GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ); // 1 Hz update rate
//   // For the parsing code to work nicely and have time to sort thru the data, and
//   // print it out we don't suggest using anything higher than 1 Hz

//   // Request updates on antenna status, comment out to keep quiet
//   GPS.sendCommand(PGCMD_ANTENNA);


//   Wire.begin();

//   // Change to MS5837_02BA if you have the Bar02
//   sensor.setModel(MS5837::MS5837_30BA);

//   if (!sensor.init()) {
//     Serial.println("{\"status\":\"error\",\"message\":\"sensor init failed\"}");
//     while (true) {
//       delay(1000);
//     }

  
//   }
//   // Ask for firmware version
//   GPSSerial.println(PMTK_Q_RELEASE);

//   sensor.setFluidDensity(997);

//   Serial.println("{\"status\":\"ok\",\"message\":\"sensor ready\"}");
// }

// void loop() // run over and over again
// {
//   // read data from the GPS in the 'main loop'
//   char c = GPS.read();
//   // if you want to debug, this is a good time to do it!
//   if (GPSECHO)
//     if (c) Serial.print(c);
//   // if a sentence is received, we can check the checksum, parse it...
//   if (GPS.newNMEAreceived()) {
//     // a tricky thing here is if we print the NMEA sentence, or data
//     // we end up not listening and catching other sentences!
//     // so be very wary if using OUTPUT_ALLDATA and trying to print out data
//     Serial.print(GPS.lastNMEA()); // this also sets the newNMEAreceived() flag to false
//     if (!GPS.parse(GPS.lastNMEA())) // this also sets the newNMEAreceived() flag to false
//       return; // we can fail to parse a sentence in which case we should just wait for another
//   }

//   // approximately every 2 seconds or so, print out the current stats
//   if (millis() - timer > 2000) {
//     timer = millis(); // reset the timer
//     Serial.print("\nTime: ");
//     if (GPS.hour < 10) { Serial.print('0'); }
//     Serial.print(GPS.hour, DEC); Serial.print(':');
//     if (GPS.minute < 10) { Serial.print('0'); }
//     Serial.print(GPS.minute, DEC); Serial.print(':');
//     if (GPS.seconds < 10) { Serial.print('0'); }
//     Serial.print(GPS.seconds, DEC); Serial.print('.');
//     if (GPS.milliseconds < 10) {
//       Serial.print("00");
//     } else if (GPS.milliseconds > 9 && GPS.milliseconds < 100) {
//       Serial.print("0");
//     }
//     Serial.println(GPS.milliseconds);
//     Serial.print("Date: ");
//     Serial.print(GPS.day, DEC); Serial.print('/');
//     Serial.print(GPS.month, DEC); Serial.print("/20");
//     Serial.println(GPS.year, DEC);
//     Serial.print("Fix: "); Serial.print((int)GPS.fix);
//     Serial.print(" quality: "); Serial.println((int)GPS.fixquality);
//     if (GPS.fix) {
//       Serial.print("Location: ");
//       Serial.print(GPS.latitude, 4); Serial.print(GPS.lat);
//       Serial.print(", ");
//       Serial.print(GPS.longitude, 4); Serial.println(GPS.lon);
//       Serial.print("Speed (knots): "); Serial.println(GPS.speed);
//       Serial.print("Angle: "); Serial.println(GPS.angle);
//       Serial.print("Altitude: "); Serial.println(GPS.altitude);
//       Serial.print("Satellites: "); Serial.println((int)GPS.satellites);
//       Serial.print("Antenna status: "); Serial.println((int)GPS.antenna);
//     }
//   }
//   sensor.read();
//     float pressure_mbar = sensor.pressure();
//     float temperature_c = sensor.temperature();
//     float depth_m = sensor.depth();

//     // Send one JSON line per reading
//     Serial.print("{\"pressure_mbar\":");
//     Serial.print(pressure_mbar, 3);
//     Serial.print(",\"temperature_c\":");
//     Serial.print(temperature_c, 3);
//     Serial.print(",\"depth_m\":");
//     Serial.print(depth_m, 3);
//     Serial.println("}");

//   if (sensor.pressure() == 0) {
//     Serial.println("{\"status\":\"warning\",\"message\":\"invalid reading\"}");
//     return;
//   }

//   delay(1000);



// }
#include <Wire.h>
#include "MS5837.h"
#include "Adafruit_GPS.h"

#define GPSSerial Serial1
Adafruit_GPS GPS(&GPSSerial);

MS5837 sensor;

uint32_t timer = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);

  // GPS setup
  GPS.begin(9600);
  GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
  GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ);
  GPS.sendCommand(PGCMD_ANTENNA);
  delay(1000);
  GPSSerial.println(PMTK_Q_RELEASE);

  // Pressure sensor setup
  Wire.begin();
  sensor.setModel(MS5837::MS5837_30BA);

  if (!sensor.init()) {
    Serial.println("{\"status\":\"error\",\"message\":\"sensor init failed\"}");
    while (true) {
      delay(1000);
    }
  }

  sensor.setFluidDensity(997);

  Serial.println("{\"status\":\"ok\",\"message\":\"system ready\"}");
}

void loop() {
  // Keep GPS parser updated
  char c = GPS.read();

  if (GPS.newNMEAreceived()) {
    if (!GPS.parse(GPS.lastNMEA())) {
      return;
    }
  }

  // Send one JSON packet every 2 seconds
  if (millis() - timer > 2000) {
    timer = millis();

    sensor.read();

    float pressure_mbar = sensor.pressure();
    float temperature_c = sensor.temperature();
    float depth_m = sensor.depth();

    if (pressure_mbar == 0) {
      Serial.println("{\"status\":\"warning\",\"message\":\"invalid pressure reading\"}");
      return;
    }

    Serial.print("{");
    Serial.print("\"pressure_mbar\":");
    Serial.print(pressure_mbar, 3);
    Serial.print(",\"temperature_c\":");
    Serial.print(temperature_c, 3);
    Serial.print(",\"depth_m\":");
    Serial.print(depth_m, 3);

    Serial.print(",\"gps_fix\":");
    Serial.print((int)GPS.fix);

    Serial.print(",\"gps_fixquality\":");
    Serial.print((int)GPS.fixquality);

    if (GPS.fix) {
      Serial.print(",\"latitude\":");
      Serial.print(GPS.latitudeDegrees, 6);

      Serial.print(",\"longitude\":");
      Serial.print(GPS.longitudeDegrees, 6);

      Serial.print(",\"speed_knots\":");
      Serial.print(GPS.speed);

      Serial.print(",\"altitude\":");
      Serial.print(GPS.altitude);

      Serial.print(",\"satellites\":");
      Serial.print((int)GPS.satellites);
    }

    Serial.println("}");
  }
}