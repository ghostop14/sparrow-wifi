# sparrow-wifi - Graphical WiFi Analyzer for Linux

## Overview
Like so many of us who have used tools like inSSIDer on Windows in the past, I've been looking for something that runs natively on linux with at least the same or better capabilities.  After not finding exactly what I was looking for I decided to create a new one.  Sparrow-wifi is written in python3 with the exception that behind the scenes it uses the linux 'iw' command for data acquisition.

Sparrow-wifi provides a nice graphical interface with tables of discovered networks and signal plots along with a few other nice features:

- Enhanced per-network telemetry display ('tracker' style signal meter, time plots, GPS log which can be exported)
- Signal "hunt" mode.  Normal scans running across all 2.4 GHz and 5 GHz channels can take 5-10 seconds per sweep as the radio needs to retune to each frequency and listen.  If you're trying to locate a particular SSID, this can be too slow.  Hunt mode allows you to specify the channel number or center frequency and only scan that one channel for much faster hunt performance (generally less than 0.2 seconds/channel).
- Ability to export results to CSV and import them back in to revisualize a scan
- Plot SSID GPS coordinates on Google maps
- Sparrow-wifi has built-in GPS support via gpsd for network location tagging
- Sparrow-wifi has a remotely deployable agent (sparrowwifiagent.py) that can be run on a separate system.  The GUI can then be connected to the remote agent for remote monitoring, including remote GPS.  Agent supports a --sendannounce startup parameter to allow for auto-discovery via broadcast packets.  It also supports a headless record local on start mode (see --help)
- MAVLINK / DRONE SUPPORT!  The remote agent can be configured to pull GPS via the Mavlink protocol from a mavlink-enabled vehicle such as a drone or rover
- The agent provides a basic HTTP service and provides JSON responses to requests from the UI, so requests for wireless interfaces, networks, and GPS status can even be used in other applications

Sample screenshots:

<p align="center">
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/sparrow-screenshot.png" width="800"/>
</p>

<p align="center">
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/telemetry-screenshot.png" width="600"/>
</p>

NOTE: This project is under active development so check back regularly for updates, bugfixes, and new features.

## Installation
sparrow-wifi uses python3, qt5, and qtchart behind the scenes.  On a standard debian variant you will may already have python3 and qt5 installed.  The only addition to run is qtchart.  Therefore you may need to run the following command for setup:

(if you don't already have pip3 installed, use 'apt-get install python3-pip')

pip3 install QScintilla PyQtChart gps3 dronekit manuf

If you're going to use the gps capabilities, you'll also need to make sure gpsd is installed and configured:

sudo apt-get install gpsd


## Running sparrow-wifi
Because it needs to use iw to scan, you will need to run sparrow-wifi as root.  Simply run:

sudo ./sparrow-wifi.py

## Running sparrow-wifi remote agent
Because it needs to use iw to scan, you will need to run sparrowwifiagent as root.  Simply run:

sudo ./sparrowwifiagent.py

An alternate port can also be specified with:
sudo ./sparrowwifiagent.py --port=&lt;myport&gt;

There are a number of options including IP connection restrictions and record-local-on-start.  See ./sparrowwifiagent.py --help for a full list of options.

To use mavlink to pull GPS from a drone use the --mavlinkgps parameter:

                        --mavlinkgps MAVLINKGPS

			Use Mavlink (drone) for GPS. Options are: '3dr' for a

                        Solo, 'sitl' for local simulator, or full connection

                        string ('udp/tcp:<ip>:<port>' such as:

                        'udp:10.1.1.10:14550')


## Raspberry Pi Notes

You can run the remote agent on a Raspberry pi, however the installation requirements are a bit different.  For the pip installation, you won't be able to run the GUI since there doesn't appear to be a PyQtChart suitable for the Pi.  So for the agent, just install the python-dateutil, gps, dronekit, and manuf modules:

You will also need to upgrade to Python 3.5.1 or higher with a process similar to this:

cd /tmp
wget https://www.python.org/ftp/python/3.5.1/Python-3.5.1.tgz
tar -zxvf Python-3.5.1.tgz
cd Python-3.5.1
./configure && make && sudo make install

Once that is done, install the necessary modules into the 3.5 build:
sudo pip3.5 install gps3 dronekit manuf python-dateutil

Then you can run the agent directly with commands like this:

/usr/local/bin/python3.5 ./sparrowwifiagent.py

/usr/local/bin/python3.5 ./sparrowwifiagent.py --mavlinkgps=3dr --recordinterface=wlan1



