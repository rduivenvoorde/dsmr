#!/usr/bin/python3
"""
Created: 11-02-2018
Author: daniel Schutterop <daniel@pure-knowledge.nl>

This module reads DSMR (Dutch Smart Meter Reader) data from the
P1 port (Serial) and outputs it as JSON

This script adheres to the 5.0.2 P1 Companion standard
(Dutch Smart Meter Requirements) of Netbeheer Nederland written
on 26-02-2016

"""

import sys
import signal
import os
import re
import serial
import paho.mqtt.client as paho

DEBUG = False

#[ MQTT Parameters ]
MQTT_ENABLED = True
MQTT_BROKER = 'duif.net'
MQTT_PORT = 1883
MQTT_CLIENT_UNIQ = 'smartmeter-1'
MQTT_TOPIC_PREFIX = 'dsmr'
MQTT_AUTH = False
MQTT_USER = 'dsmr' #username
MQTT_PASS = 'dsmr' # password
MQTT_QOS = 0
MQTT_RETAIN = False

#[ Serial parameters ]
SER = serial.Serial()
SER.port = "/dev/ttyUSB0"
SER.baudrate = 115200
SER.bytesize = serial.SEVENBITS
SER.parity = serial.PARITY_EVEN
SER.stopbits = serial.STOPBITS_ONE
SER.xonxoff = 0
SER.rtscts = 0
SER.timeout = 20

#[ InfluxDB parameters ]
INFLUXDB_ENABLED = False
INFLUXDB_HOST = 'influxdb.localdomain'
INFLUXDB_PORT = 8086
INFLUXDB_USER = 'dsmr' #username
INFLUXDB_PASS = 'dsmr' #password
INFLUXDB_DB = 'dsmr'

def signal_handler(signal, handler):
    """
    Signal handler to catch CRTL-C's
    """
    print('Exiting...')
    sys.exit(0)

def datastripper(line):
    """
    The datastripper grabs the incoming line from the serial interface
    and interprets it. Since the P1 telegrams are dumpes continuously,
    there are a few small rules:

    - The line starting with ! acts as a terminator (And honestly,
      I'm too lazy to check the hash.
    - The DSMR version is used to open a dsmr<version>.py file containing
      a dictionary with the OBIS codes, description, metric name and
      regex used to grab the data from the line itself.
      If the OBIS file isn't found, feel free to create one yourself
      using the dsmr42.py as a template, or drop me a line.
    """
    global dsmr_version
    line = line[2:]
    if DEBUG:
        print(line)
    if (not line.startswith("!")) and (not line.startswith("/")) and (not line.startswith("\\")):
        headers = re.match(r"\d{0,3}-\d{0,3}:\d{0,3}.\d{0,3}.\d{0,3}", line)
        if headers:
            #print(headers)
            header = re.match(r"\d{0,3}-\d{0,3}:\d{0,3}.\d{0,3}.\d{0,3}", line).group(0)
        else:
            print('NO HEADERS? :')
            print(headers)
            return 

        """
        The DSMR version is located in the 1-3:0.2.8 string.
        we're using the version string to look up the proper data
        for the smart meter you're using
        """
        if header == "1-3:0.2.8":
            dsmr_version = int(re.match(r"^.*\((.*)\)", line).group(1))
            if DEBUG:
                print("DSMR version detected is %s " % dsmr_version)

        if not 'dsmr_version' in globals():
            if DEBUG:
                print("[DSMR version Unknown] section %s from %s" % (header, line))
        else:
            if DEBUG:
                print("[DSMRv%s] section %s from %s" % (dsmr_version, header, line))

            if not os.path.isfile("./dsmr%s.py" % dsmr_version):
                print("No DSMR config for version %s (./dsmr%s.py)" % (dsmr_version, dsmr_version))
                exit(99)

            dsmr_value = open("dsmr%s.py" % dsmr_version, 'r').read()
            dsmr_value = eval(dsmr_value)

            if header in dsmr_value:
                if DEBUG:
                    print("Match for this entry: %s with regex %s, return as %s" \
                    % (dsmr_value[header][0], dsmr_value[header][2], dsmr_value[header][1]))
                dsmr_result = re.match(dsmr_value[header][2], line).group(1)

                if DEBUG:
                    print("returning %s -> %s" % (dsmr_value[header][1], dsmr_result))

                return [dsmr_value[header][1], dsmr_result, header]

            else:
                print(f'Unknow header: {header}')
    elif line.startswith("!"):
        print('END')
        return ['END']


def main():
    """
    Main function
    """
    signal.signal(signal.SIGINT, signal_handler)

    print("Starting...")

    try:
        SER.open()
    except ValueError:
        sys.exit("Error opening serial port (%s). exiting" % SER.name)

    if INFLUXDB_ENABLED:
        if DEBUG:
            print("INFLUXDB enabled")

        from influxdb import client as influxdb
        db = influxdb.InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASS, INFLUXDB_DB)

    if MQTT_ENABLED:
        if DEBUG:
            print("MQTT is enabled")
            print("MQTT loop starting")

        mqttc = paho.Client(MQTT_CLIENT_UNIQ, False)

        if MQTT_AUTH:
            mqttc.username_pw_set(MQTT_USER, MQTT_PASS)

    mqttc.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqttc.loop_start()

    power_l1 = 0
    power_l2 = 0
    power_l3 = 0
    solar_l2 = 0
    netto_consumed = 0
    netto_produced = 0
    d_1_0_1_8_2 = None
    d_1_0_1_8_1 = None
    d_1_0_2_8_2 = None
    d_1_0_2_8_1 = None

    while True:
        try:
            raw_line = SER.readline()
        except ValueError:
            sys.exit("Unable to get data from serial port (%s). Exiting." % SER.name)

        data = datastripper(str(raw_line))

        # do some actual stuff with the returned (and valid) data
        if data:
            if data[0] == 'END':
                total = float(power_l1) + float(power_l2) + float(power_l3)
                print(f'TOTAL consumed: {int(total*1000)}')
                power_l1 = 0
                power_l2 = 0
                power_l3 = 0
                mqtt_topic = 'riouw/kwh'
                mqttc.publish(mqtt_topic, int(total*1000), MQTT_QOS, MQTT_RETAIN)
                #print("[MQTT  ] Publish (%s) %s to %s..." % ('total', total, mqtt_topic))
                
                mqtt_topic = 'riouw/solar_l2'
                print(f'SOLAR_L2: {int(float(solar_l2)*1000)}')
                mqttc.publish(mqtt_topic, int(float(solar_l2)*1000), MQTT_QOS, MQTT_RETAIN)
#                print("[MQTT  ] Publish (%s) %s to %s..." % ('total', solar_l2, mqtt_topic))
                solar_l1 = 0
                solar_l2 = 0
                solar_l3 = 0

                print(f'netto consumed: {netto_consumed}')
                print(f'netto_produced {netto_produced}')
                netto = float(netto_consumed) - float(netto_produced)
                netto_consumed = 0
                netto_produced = 0
                print(f'netto: {netto}')
                mqtt_topic = 'riouw/kwh_netto'
                mqttc.publish(mqtt_topic, int(netto*1000), MQTT_QOS, MQTT_RETAIN)

                if d_1_0_1_8_2 and d_1_0_1_8_1 and d_1_0_2_8_2 and d_1_0_2_8_1:
                    print(f"levering normaal/hoog tarief = {d_1_0_1_8_2} kWh")
                    print(f"levering dal/laag tarief     = {d_1_0_1_8_1} kWh")
                    print(f"terug normaal/hoog tarief    = {d_1_0_2_8_2} kWh")
                    print(f"terug dal/laag tarief        = {d_1_0_2_8_1} kWh")

#                print("[MQTT  ] Publish (%s) %s to %s..." % ('netto', netto, mqtt_topic))
            elif data[2] == "1-0:21.7.0":
                power_l1 = data[1]
                print(f'power_l1 {power_l1}')
                mqttc.publish('riouw/power_l1', int(float(power_l1)*1000), MQTT_QOS, MQTT_RETAIN)
            elif data[2] == "1-0:41.7.0":
                power_l2 = data[1]
                mqttc.publish('riouw/power_l2', int(float(power_l2)*1000), MQTT_QOS, MQTT_RETAIN)
                print(f'power_l2 {power_l2}')
            elif data[2] == "1-0:61.7.0":
                power_l3 = data[1]
                mqttc.publish('riouw/power_l3', int(float(power_l3)*1000), MQTT_QOS, MQTT_RETAIN)
                print(f'power_l3 {power_l3}')
            elif data[2] == "1-0:1.7.0":
                netto_consumed = data[1]
                #print(f'XXXXX netto_consumed {netto_consumed}')
            elif data[2] == "1-0:2.7.0":
                netto_produced = data[1]
                #print(f'XXXXX netto_produces {netto_produced}')
            elif data[2] == "1-0:22.7.0":
                solar_l1 = data[1]
                print(f'solar_l1 {solar_l1}')
            elif data[2] == "1-0:42.7.0":
                solar_l2 = data[1]
                print(f'solar_l2 {solar_l2}')
            elif data[2] == "1-0:62.7.0":
                solar_l3 = data[1]
                print(f'solar_l3 {solar_l3}')
            elif data[2] == "0-1:24.2.1":
                gas = data[1]
                print(f'gas {gas} m3')

            # https://github.com/energietransitie/dsmr-info/blob/main/dsmr-p1-specs.csv
            # https://www.netbeheernederland.nl/publicatie/dsmr-502-p1-companion-standard
            elif data[2] == "1-0:1.8.1":
                # P1 electricity meter reading deliverd to client normal tarriff OBIS code
                # Meter Reading electricity delivered to client (Tariff 1)
                d_1_0_1_8_1 = data[1]
                #print(f"levering dal/laag tarief     = {d_1_0_1_8_1} kWh")
            elif data[2] == "1-0:1.8.2":
                # P1 electricity meter reading deliverd to client low tarriff OBIS code
                # Meter Reading electricity delivered to client (Tariff 2) 
                d_1_0_1_8_2 = data[1]
                #print(f"levering normaal/hoog tarief = {d_1_0_1_8_2} kWh")
            elif data[2] == "1-0:2.8.1":
                # P1 electricity meter reading deliverd by client normal tarriff OBIS code
                # Meter Reading electricity delivered by client (Tariff 1) 
                d_1_0_2_8_1 = data[1]
                #print(f"terug dal/laag tarief        = {d_1_0_2_8_1} kWh")
            elif data[2] == "1-0:2.8.2":
                # P1 electricity meter reading deliverd by client low tarriff OBIS code
                # Meter Reading electricity delivered by client (Tariff 2) 
                d_1_0_2_8_2 = data[1]
                #print(f"terug normaal/hoog tarief    = {d_1_0_2_8_2} kWh")
                

            if MQTT_ENABLED:
                #mqtt_topic = ("%s/%s" % (MQTT_TOPIC_PREFIX, data[0]))
                #mqttc.publish(mqtt_topic, data[1], MQTT_QOS, MQTT_RETAIN)
                #print("[MQTT  ] Publish (%s) %s to %s..." % (data[0], data[1], mqtt_topic))
                pass

            if INFLUXDB_ENABLED:
                print("[INFLUX] Posting (%s) %s to %s..." % (data[0], data[1], INFLUXDB_DB))
                if data[1]:
                    json_body = [
                        {
                            "measurement": data[0],
                            "fields": {
                                "value": data[1]
                            }
                        }]
                    db.write_points(json_body)


    if MQTT_ENABLED:
        if DEBUG:
            print("MQTT loop stopping")
        mqttc.loop_stop()
        mqttc.disconnect()

    try:
        SER.close()
    except ValueError:
        sys.exit("Unable to close serial port (%s). Exiting." % SER.name)

if __name__ == '__main__':
    main()
