#!/bin/bash

MAVLINKENABLED=`cat /opt/sdr/sparrow/sparrow-wifi/sparrowwifiagent.cfg | grep "^mavlink" | wc -l`

if [ $MAVLINKENABLED -eq 0 ]; then
	# echo "Mavlink not enabled. Exiting."
	exit 0
fi

IFACE="wlan0"

IPADDR=`ifconfig $IFACE | grep -Eo 'inet addr\:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}' | grep -Eo "[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"`

ISTENNET=`echo "$IPADDR" | grep "^10\.1\.1" | wc -l`

AGENTRUNNING=`ps aux | grep "sparrowwifiagent.py" | grep -v grep | wc -l`
if [ $ISTENNET -eq 1 ]; then
	# We're connected
	if [ $AGENTRUNNING -eq 0 ]; then
		# Check if it should be running
		if [ -e /opt/sdr/sparrow/sparrow-wifi/sparrowwifiagent.cfg ]; then
			CANCELSTART=`cat /opt/sdr/sparrow/sparrow-wifi/sparrowwifiagent.cfg | grep -Ei "^cancelstart.*?true" | wc -l`
		else
			CANCELSTART=0
		fi

		if [ $CANCELSTART -eq 0 ]; then
			echo "[`date`] Starting agent"
			echo "[`date`] Starting Sparrow Wifi agent" >> /var/log/rpisparrowagent.log
			cd /opt/sdr/sparrow/sparrow-wifi/

			/usr/local/bin/python3.5 ./sparrowwifiagent.py &
		fi
	fi
else
	# Not in ten net
	if [ $AGENTRUNNING -gt 0 ]; then
		echo "[`date`] Stopping agent"
		echo "[`date`] Stopping Sparrow Wifi agent" >> /var/log/rpisparrowagent.log
		# Send keyboard interrupt
		pkill -2 -f "python3.5.*sparrowwifiagent.py.*"
		# wait for HTTP server to stop
		sleep 1
		# force kill agent if it didn't stop on its own
		pkill -9 -f "python3.5.*sparrowwifiagent.py.*"
	fi
fi

