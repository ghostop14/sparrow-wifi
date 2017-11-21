#!/bin/bash

cd /opt/sdr/sparrow/sparrow-wifi/

GPSDRUNNING=`service gpsd status | grep "Active.*dead" | wc -l`

# If the service isn't dead stop it
if [ $GPSRUNNING -eq 0 ]; then
	service gpsd stop
fi

GPSDRUNNING=`pgrep gpsd | wc -l`

# See if it was already started with the command below
if [ $GPSRUNNING -eq 0 ]; then
	# We only care if the ttyUSB0 port is present indicating the GPS is actually plugged in
	if [ -e /dev/ttyUSB0 ]; then
		gpsd -G /dev/ttyUSB0
	fi
fi

/usr/local/bin/python3.5 ./sparrowwifiagent.py &


