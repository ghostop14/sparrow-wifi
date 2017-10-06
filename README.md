# sparrow-wifi - Graphical WiFi Analyzer for Linux

## Overview
Like so many of us who have used tools like inSSIDer on Windows in the past, I've been looking for a good linux equivalent.  After not finding exactly what I was looking for I decided to create a new one.  Sparrow-wifi is written in python3 with the exception that behind the scenes it uses the linux 'iw' command for data acquisition.

As a wireless suite, sparrow-wifi also has a remotely deployable agent (sparrowwifiagent.py) that can be run on a separate system.  The GUI can then also be connected to the remote agent for remote monitoring.  The agent provides a basic HTTP service and provides JSON responses to requests from the UI.

## Installation
sparrow-wifi uses python3, qt5, and qtchart behind the scenes.  On a standard debian variant you will may already have python3 and qt5 installed.  The only addition to run is qtchart.  Therefore you may need to run the following command for setup:

pip3 install qscintilla PyQtChart

## Running sparrow-wifi
Because it needs to use iw to scan, you will need to run sparrow-wifi as root.  Simply run:

sudo sparrow-wifi.py

## Running sparrow-wifi remote agent
Because it needs to use iw to scan, you will need to run sparrowwifiagent as root.  Simply run:

sudo sparrowwifiagent.py

An alternate port can also be specified with:
sudo sparrowwifiagent.py --port=<myport>



