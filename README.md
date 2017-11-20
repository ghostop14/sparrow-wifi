# sparrow-wifi - Graphical WiFi Analyzer for Linux

## Overview
Sparrow-wifi has been built from the ground up to be the next generation 2.4 GHz and 5 GHz Wifi spectral awareness tool.  At its most basic it provides a more comprehensive GUI-based replacement for tools like inSSIDer and linssid that runs specifically on linux.  In its most comprehensive use cases, sparrow-wifi integrates wifi, software-defined radio (hackrf), advanced bluetooth tools (traditional and Ubertooth), traditional gps, and drone/rover gps via mavlink in one solution.

Written entirely in Python3, Sparrow-wifi has been designed for the following scenarios:
- Basic wifi SSID identification
- Wifi source hunt - Switch from normal to hunt mode to get multiple samples per second and use the telemetry windows to track a wifi source
- 2.4 GHz and 5 GHz spectrum view - Overlay spectrums from Ubertooth (2.4 GHz) or HackRF (2.4 GHz and 5 GHz) in realtime on top of the wifi spectrum
- Bluetooth identification - LE advertisement listening with standard bluetooth, full promiscuous mode in LE and classic bluetooth with Ubertooth
- Bluetooth source hunt - Track LE advertisement sources or iBeacons with the telemetry window
- iBeacon advertisement - Advertise your own iBeacons
- Remote operations - An agent is included that provides all of the GUI functionality via a remote agent the GUI can talk to.  The agent can be run on systems such as a Raspberry Pi and flown on a drone (its made several flights on a Solo 3DR), or attached to a rover in either GUI-controlled or autonomous scan/record modes.
- The remote agent is JSON-based so it can be integrated with other applications
- Import/Export - Ability to import and export to/from CSV and JSON for easy integration and revisiualization.  You can also just run 'iw dev <interface> scan' and save it to a file and import that as well.

[NOTE: This project is under active development so check back regularly for updates, bugfixes, and new features.]

A few sample screenshots.  The first is the main window showing a basic wifi scan, the second shows the telemetry/tracking window used for both Wifi and bluetooth tracking.

<p align="center">
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/sparrow-screenshot.png" width="800"/>
</p>

<p align="center">
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/telemetry-screenshot.png" width="600"/>
</p>

## Installation
sparrow-wifi uses python3, qt5, and qtchart for the UI.  On a standard debian variant you will may already have python3 and qt5 installed.  The only addition to run it is qtchart.  The following commands should get you up and running with wifi on both Ubuntu and Kali linux:

sudo apt-get install python3-pip gpsd gpsd-clients
sudo pip3 install QScintilla PyQtChart gps3 dronekit manuf python-dateutil

NOTE: If you're trying to run on a Raspberry Pi, see the Raspberry Pi section below.  Only the remote agent has been run on a Pi, some of the GUI components wouldn't install / set up on the ARM platform.


## Running sparrow-wifi
Because it needs to use the standard command-line tool 'iw' for wifi scans, you will need to run sparrow-wifi as root.  Simply run this from the cloned directory:

sudo ./sparrow-wifi.py

## Bluetooth
Bluetooth support has been added.  However it's important to know that bluetooth works very differently than WiFi.  It uses frequency hopping spread spectrum in the same 2.4 GHz band as WiFi, but because of the hopping introduces challenges.  Also, Bluetooth Low Energy (BTLE) and Classic Bluetooth are different, which impacts tools to scan.

With that said, enabling bluetooth, especially with an Ubertooth introduces some great functionality.  In addition to scanning for Bluetooth devices, Ubertooth can be used as a 2.4 GHz spectrum analyzer, which gives you a great overlay on your WiFi 2.4 GHz signal space.

If you would like to scan for bluetooth, you'll need a few things:
1. A bluetooth adapter (test with 'hcitool dev' to make sure it shows up).  With an adapter you can do basic BTLE advertisement and iBeacon scans.
2. [Optional ] An Ubertooth for promiscuous discovery scans (BTLE and Classic Bluetooth)
	- Ubertooth tools installed and functioning (you can test it with ubertooth-specan-ui)
	- Blue Hydra installed into /opt/bluetooth/blue_hydra (mkdir /opt/bluetooth && cd /opt/bluetooth && git clone https://github.com/pwnieexpress/blue_hydra.git).  Then make sure you've followed the blue_hydra installation instructions.  You can test it with bin/blue_hydra.  This msut be in /opt/bluetooth/blue_hydra or the app won't find it.

Some troubleshooting tips:
- If you don't see any devices with a basic LE advertisement scan, try "hcitool lescan" from the command-line and see if you get any errors.  If so address them there.  Sometimes a quick "hciconfig hci0 down && hciconfig hci0 up" can fix it.
- If you have an Ubertooth and don't see any spectrum try running ubertooth-specan or ubertooth-specan-ui from the command line.  If you get any errors address them there.

## Spectrum / HackRF
HackRF support has been added to take advantage of the hackrf_sweep capabilities added to the firmware.  With a HackRF you can sweep the entire range for a view of the spectrum.  While hackrf_sweep can sweep from 2.4 GHz through 5 GHz, the frame rate was too slow (like 1 frame every 2 seconds), so you can use it for only one band at a time.  With that said, if you have both an Ubertooth and a HackRF, you could use the Ubertooth to display the 2.4 GHz band and the HackRF to display the 5 GHz band simultaneously.

With that said, standard RF and and antenna rules apply.  If you want to monitor either band, make sure you have an antenna capable of receiving in that band.  And if you do want to grab an external dual-band antenna used on wireless cards, just note that the connector polarity is typically reversed (rp-sma) so you'll need to grab an adapter to connect it to the HackRF.  An RP-SMA will screw on to the SMA connector but the center pin isn't there so you won't actually receive anything.  Just a word of caution.

Notes: The 5 GHz spectrum, even with a dual-band antenna can be difficult to see signals in the same way as in 2.4 GHz.  Sometimes the band shows better in a waterfall plot, but if that's what you need try qspectrumanalyzer.  One thing that does seem to help is on the Spectrum menu, increase the gain to about 1.4 for 5 GHz.

Troubleshooting tips:
- If you don't see any spectrum at all try running hackrf_sweep from the command-line.  If you get any errors, address them there.

## Running sparrow-wifi remote agent
Because the agent has the same requirements as the GUI in terms of system access, you will need to run the agent as root as well.  Simply run:

sudo ./sparrowwifiagent.py

By default it will listen on port 8020.  There are a number of options that can be seen with --help, and a local configuration file can also be used.

An alternate port can also be specified with:
sudo ./sparrowwifiagent.py --port=&lt;myport&gt;

There are a number of options including IP connection restrictions and record-local-on-start.  Here's the --help parameter list at this time:

usage: sparrowwifiagent.py [-h] [--port PORT] [--allowedips ALLOWEDIPS]
                           [--mavlinkgps MAVLINKGPS] [--sendannounce]
                           [--userpileds] [--recordinterface RECORDINTERFACE]
                           [--ignorecfg] [--cfgfile CFGFILE]
                           [--delaystart DELAYSTART]

Sparrow-wifi agent

optional arguments:
  -h, --help            show this help message and exit
  --port PORT           Port for HTTP server to listen on
  --allowedips ALLOWEDIPS
                        IP addresses allowed to connect to this agent. Default
                        is any. This can be a comma-separated list for
                        multiple IP addresses
  --mavlinkgps MAVLINKGPS
                        Use Mavlink (drone) for GPS. Options are: '3dr' for a
                        Solo, 'sitl' for local simulator, or full connection
                        string ('udp/tcp:<ip>:<port>' such as:
                        'udp:10.1.1.10:14550')
  --sendannounce        Send a UDP broadcast packet on the specified port to
                        announce presence
  --userpileds          Use RPi LEDs to signal state. Red=GPS
                        [off=None,blinking=Unsynchronized,solid=synchronized],
                        Green=Agent Running [On=Running, blinking=servicing
                        HTTP request]
  --recordinterface RECORDINTERFACE
                        Automatically start recording locally with the given
                        wireless interface (headless mode) in a recordings
                        directory
  --ignorecfg           Don't load any config files (useful for overriding
                        and/or testing)
  --cfgfile CFGFILE     Use the specified config file rather than the default
                        sparrowwifiagent.cfg file
  --delaystart DELAYSTART
                        Wait <delaystart> seconds before initializing

## Drone Notes
Being able to "war fly" (the drone equivilent of "wardriving" popular in the wifi world) was another goal of the project.  As a result, being able to have a lightweight agent that could be run on a small platform such as a Raspberry Pi that could be mounted on a drone was incorporated into the design requirements.  The agent has been flown successfully on a Solo 3DR drone (keeping the overall weight under the 350 g payload weight).

The Solo was a perfect choice for the project because the controller acts as a wifi access point and communicates with the drone over a traditional IP network using the mavlink protocol.  This allows other devices such as laptops, tablets, and the Raspberry Pi to simply join the controller wifi network and have IP connectivity.  This was important for field operations as it kept the operational complexity down.

Because these drones have onboard GPS as part of their basic functionality, it's possible over mavlink (with the help of dronekit) to pull GPS coordinates directly from the drone's GPS.  This helps keep the overall payload weight down as an additional GPS receiver does not need to be flown as part of the payload.  Also, in order to keep the number of tasks required by the drone operator to a minimum during flight, the agent can be started, wait for the drone GPS to be synchronized, use the Raspberry Pi lights to signal operational readiness, and automatically start recording wifi networks to a local file.  The GUI then provides an interface to retrieve those remotely saved files and pull back for visualization.

This scenario has been tested with a Cisco AE1000 dual-band adapter connected to the Pi.  Note though that I ran into an issue scanning 5 GHz from the Pi that I finally found the solution for.  With a dual-band adapter, if you don't disable the internal Pi wireless adapter you won't get any 5 GHz results (this is a known issue).  What you'll need to do is disable the onboard wifi by editing /boot/config.txt and adding the following line then reboot 'dtoverlay=pi3-disable-wifi'.  Now you'll be able to scan both bands from the Pi.

The quickest way to start the agent on a Raspberry Pi (IMPORTANT: see the Raspbery Pi section first, you'll need to build Python 3.5 to run the agent since the subprocess commands used were initially removed from python3 then put back in 3.5) and pull GPS from a Solo drone is to start it with the following command on the Pi:

sudo python3.5 ./sparrowwifiagent.py --userpileds --sendannounce --mavlinkgps 3dr

The Raspberry Pi red and green LED's will then be used as visual indicators transitioning through the following states:
1. Both lights off - Initializing
2. Red LED Heartbeat - Connected to the drone (dronekit vehicle connect was successful)
3. Red LED Solid - Connected and GPS synchronized and operational (the drone can take a couple of minutes for the GPS to settle as part of its basic flight initialization)
4. Green LED Solid - Agent HTTP server is up and the agent is operational and ready to serve requests

Note: Without the mavlink setting, if using a local GPS module, the red LED will transition through the same heartbeat=GPS present but unsynchronized, solid = GPS synchronized states.

## Raspberry Pi Notes

You can run the remote agent on a Raspberry pi, however the installation requirements are a bit different.  For the pip installation, you won't be able to run the GUI since there doesn't appear to be a PyQtChart suitable for the Pi.  So for the agent, just install the python-dateutil, gps, dronekit, and manuf modules:

You will also need to upgrade to Python 3.5.1 or higher with the following commands:

sudo apt-get install libsqlite3-dev

cd /tmp
wget https://www.python.org/ftp/python/3.5.1/Python-3.5.1.tgz
tar -zxvf Python-3.5.1.tgz
cd Python-3.5.1
./configure && make -j3 && sudo make install

Once that is done, install the necessary modules into the 3.5 build:
sudo pip3.5 install gps3 dronekit manuf python-dateutil

Then you can run the agent directly with commands like this:

/usr/local/bin/python3.5 ./sparrowwifiagent.py

/usr/local/bin/python3.5 ./sparrowwifiagent.py --mavlinkgps=3dr --recordinterface=wlan1

Another important note about using dual band USB wireless dongles on the Raspberry Pi (tested on a Pi 3), is that as along as the internal wireless is enabled, Raspbian won't see the 5 GHz band.

Add this line in your /boot/config.txt to disable the internal wireless, then your dual-band USB wireless will be able to see the 5 GHz band:

dtoverlay=pi3-disable-wifi




