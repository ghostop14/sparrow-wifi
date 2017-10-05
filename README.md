# sparrow-wifi - Graphical WiFi Analyzer for Linux

## Overview
Like so many of us who have used tools like inSSIDer on Windows in the past, I've been looking for a good linux equivalent.  After not finding exactly what I was looking for I decided to create a new one.  Sparrow-wifi is written in python3 with the exception that behind the scenes it uses the linux 'iw' command for data acquisition.

## Installation
sparrow-wifi uses python3, qt5, and qtchart behind the scenes.  On a standard debian variant you will may already have python3 and qt5 installed.  The only addition to run is qtchart.  Therefore you may need to run the following command for setup:

pip3 install qscintilla PyQtChart

## Running sparrow-wifi
Because it needs to use iw to scan, you will need to run sparrow-wifi as root.  Simply run:

sudo sparrow-wifi.py



