# sparrow-wifi - Graphical WiFi Analyzer for Linux

## Overview
Like so many of us who have used tools like inSSIDer on Windows in the past, I've been looking for something that runs natively on linux with at least the same or better capabilities.  After not finding exactly what I was looking for I decided to create a new one.  Sparrow-wifi is written in python3 with the exception that behind the scenes it uses the linux 'iw' command for data acquisition.

Sparrow-wifi provides a nice graphical interface with tables of discovered networks and signal plots along with a few other nice features:

- Sparrow-wifi has built-in GPS support for network location tagging
- Sparrow-wifi has a remotely deployable agent (sparrowwifiagent.py) that can be run on a separate system.  The GUI can then be connected to the remote agent for remote monitoring.
- The agent provides a basic HTTP service and provides JSON responses to requests from the UI, so requests for wireless interfaces, networks, and gpsstatus can even be used in other applications

Sample screenshot:
![alt text](https://github.com/ghostop14/sparrow-wifi/sample-screenshot.png)

## Installation
sparrow-wifi uses python3, qt5, and qtchart behind the scenes.  On a standard debian variant you will may already have python3 and qt5 installed.  The only addition to run is qtchart.  Therefore you may need to run the following command for setup:

pip3 install qscintilla PyQtChart gps3

If you're going to use the gps capabilities, you'll also need to make sure gpsd is installed and configured:

sudo apt-get install gpsd


## Running sparrow-wifi
Because it needs to use iw to scan, you will need to run sparrow-wifi as root.  Simply run:

sudo sparrow-wifi.py

## Running sparrow-wifi remote agent
Because it needs to use iw to scan, you will need to run sparrowwifiagent as root.  Simply run:

sudo sparrowwifiagent.py

An alternate port can also be specified with:
sudo sparrowwifiagent.py --port=<myport>



