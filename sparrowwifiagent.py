#!/usr/bin/python3
# 
# Copyright 2017 ghostop14
# 
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# 
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this software; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
# 
import os
import datetime
import json
import re
import argparse

from socket import *
from time import sleep
from threading import Thread
from http import server as HTTPServer

from wirelessengine import WirelessEngine
from sparrowgps import GPSEngine, GPSStatus
from sparrowdrone import SparrowDroneMavlink

# ------   Global setup ------------
gpsEngine = GPSEngine()
curTime = datetime.datetime.now()

useMavlink = False
vehicle = None
mavlinkGPSThread = None

# ------------------  Announce thread  ------------------------------
class AnnounceThread(Thread):
    def __init__(self, port):
        super(AnnounceThread, self).__init__()
        self.signalStop = False
        self.sendDelay = 4.0  # seconds
        self.threadRunning = False
        
        self.broadcastSocket = socket(AF_INET, SOCK_DGRAM)
        self.broadcastSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        self.broadcastSocket.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)        
        
        self.broadcastPort = port
        self.broadcastAddr=('255.255.255.255', self.broadcastPort)

    def sendAnnounce(self):
        try:
            self.broadcastSocket.sendto(bytes('sparrowwifiagent', "utf-8"),self.broadcastAddr)
        except:
            pass
        
    def run(self):
        self.threadRunning = True
        
        while (not self.signalStop):
            self.sendAnnounce()
            sleep(self.sendDelay)
                    
        self.threadRunning = False

# ------------------  Local network scan thread  ------------------------------
class MavlinkGPSThread(Thread):
    def __init__(self, vehicle):
        super(MavlinkGPSThread, self).__init__()
        self.signalStop = False
        self.scanDelay = 0.5  # seconds
        self.threadRunning = False
        self.vehicle = vehicle
        self.synchronized = False
        self.latitude = 0.0
        self.longitude = 0.0
        self.altitude = 0.0
        
    def run(self):
        self.threadRunning = True
        
        while (not self.signalStop):
            self.synchronized, self.latitude, self.longitude, self.altitude = self.vehicle.getGlobalGPS()
            sleep(self.scanDelay)
                    
        self.threadRunning = False

# ---------------  HTTP Request Handler --------------------
# Sample handler: https://wiki.python.org/moin/BaseHttpServer
class SparrowWiFiAgentRequestHandler(HTTPServer.BaseHTTPRequestHandler):
    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_GET(s):
        global gpsEngine
        global useMavlink
        global mavlinkGPSThread
        
        if (s.path != '/wireless/interfaces') and (s.path != '/gps/status') and ('/wireless/networks/' not in s.path):
            s.send_response(404)
            s.send_header("Content-type", "text/html")
            s.end_headers()
            s.wfile.write("<html><body><p>Bad Request</p>".encode("utf-8"))
            s.wfile.write("</body></html>".encode("UTF-8"))
            return
            
        """Respond to a GET request."""
        s.send_response(200)
        #s.send_header("Content-type", "text/html")
        s.send_header("Content-type", "application/jsonrequest")
        s.end_headers()
        # NOTE: In python 3, string is a bit different.  Examples write strings directly for Python2,
        # In python3 you have to convert it to UTF-8 bytes
        # s.wfile.write("<html><head><title>Sparrow-wifi agent</title></head><body>".encode("utf-8"))

        if s.path == '/wireless/interfaces':
            wirelessInterfaces = WirelessEngine.getInterfaces()
            jsondict={}
            jsondict['interfaces']=wirelessInterfaces
            jsonstr = json.dumps(jsondict)
            s.wfile.write(jsonstr.encode("UTF-8"))
        elif s.path == '/gps/status':
            jsondict={}
            
            if not useMavlink:
                jsondict['gpsinstalled'] = str(GPSEngine.GPSDInstalled())
                jsondict['gpsrunning'] = str(GPSEngine.GPSDRunning())
                jsondict['gpssynch'] = str(gpsEngine.gpsValid())
                if gpsEngine.gpsValid():
                    gpsPos = {}
                    gpsPos['latitude'] = gpsEngine.lastCoord.latitude
                    gpsPos['longitude'] = gpsEngine.lastCoord.longitude
                    gpsPos['altitude'] = gpsEngine.lastCoord.altitude
                    gpsPos['speed'] = gpsEngine.lastCoord.speed
                    jsondict['gpspos'] = gpsPos
            else:
                jsondict['gpsinstalled'] = 'True'
                jsondict['gpsrunning'] = 'True'
                jsondict['gpssynch'] = str(mavlinkGPSThread.synchronized)
                gpsPos = {}
                gpsPos['latitude'] = mavlinkGPSThread.latitude
                gpsPos['longitude'] = mavlinkGPSThread.longitude
                gpsPos['altitude'] = mavlinkGPSThread.altitude
                gpsPos['speed'] = mavlinkGPSThread.vehicle.getAirSpeed()
                jsondict['gpspos'] = gpsPos
                
            jsonstr = json.dumps(jsondict)
            s.wfile.write(jsonstr.encode("UTF-8"))
        elif '/wireless/networks/' in s.path:
            curInterface = s.path.replace('/wireless/networks/', '')
            # Sanitize command-line input here:
            p = re.compile('^([0-9a-zA-Z]+)')
            try:
                fieldValue = p.search(curInterface).group(1)
            except:
                fieldValue = ""

            if len(fieldValue) == 0:
                return
                
            p = re.compile('.*Frequencies=(.*)', re.IGNORECASE)
            try:
                channelStr = p.search(curInterface).group(1)
            except:
                channelStr = ""

            huntChannelList = []
            
            tmpList = channelStr.split(',')
            
            if len(tmpList) > 0:
                for curItem in tmpList:
                    try:
                        if len(curItem) > 0:
                            huntChannelList.append(int(curItem))
                            # Get results for the specified interface
                            # Need to iterate through the channels and aggregate the results
                    except:
                        pass

            if useMavlink:
                gpsCoord = GPSStatus()
                gpsCoord.gpsInstalled = True
                gpsCoord.gpsRunning = True
                gpsCoord.isValid = mavlinkGPSThread.synchronized
                gpsCoord.latitude = mavlinkGPSThread.latitude
                gpsCoord.longitude = mavlinkGPSThread.longitude
                gpsCoord.altitude = mavlinkGPSThread.altitude
                gpsCoord.speed = mavlinkGPSThread.vehicle.getAirSpeed()
                retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(fieldValue, gpsCoord, huntChannelList)
            elif gpsEngine.gpsValid():
                retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(fieldValue, gpsEngine.lastCoord, huntChannelList)
            else:
                retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(fieldValue, None, huntChannelList)
                
            s.wfile.write(jsonstr.encode("UTF-8"))

class SparrowWiFiAgent(object):
    # See https://docs.python.org/3/library/http.server.html
    # For HTTP Server info
    def run(self, port):
        server_address = ('', port)
        httpd = HTTPServer.HTTPServer(server_address, SparrowWiFiAgentRequestHandler)
        
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Starting Sparrow-wifi agent on port " + str(port))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    
        httpd.server_close()
        
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Sparrow-wifi agent stopped.")
        
if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='Sparrow-wifi agent')
    argparser.add_argument('--port', help='Port for HTTP server to listen on', default=8020, required=False)
    argparser.add_argument('--mavlinkgps', help="Use Mavlink (drone) for GPS.  Options are: '3dr' for a Solo, 'sitl' for local simulator, or full connection string ('udp/tcp:<ip>:<port>' such as: 'udp:10.1.1.10:14550')", default='', required=False)
    argparser.add_argument('--sendannounce', help="Send a UDP broadcast packet on the specified port to announce presence", action='store_true', default=False, required=False)
    args = argparser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: You need to have root privileges to run this script.  Please try again, this time using 'sudo'. Exiting.\n")
        exit(2)

    # Set up parameters
    port = args.port
    mavlinksetting = args.mavlinkgps
    
    if len(mavlinksetting) > 0:
        vehicle = SparrowDroneMavlink()
        
        print('Connecting to ' + mavlinksetting)
        
        if mavlinksetting == '3dr':
            retVal = vehicle.connectToSolo()
        elif (mavlinksetting == 'sitl'):
            retVal = vehicle.connectToSimulator()
        else:
            retVal = vehicle.connect(mavlinksetting)

        if retVal:
            print('Mavlink connected.')
            print('Current GPS Info:')
            synchronized, latitude, longitude, altitude = vehicle.getGlobalGPS()
            print('Synchronized: ' + str(synchronized))
            print('Latitude: ' + str(latitude))
            print('Longitude: ' + str(longitude))
            print('Altitude (m): ' + str(altitude))
            print('Heading: ' + str(vehicle.getHeading()))
            
            useMavlink = True
            mavlinkGPSThread = MavlinkGPSThread(vehicle)
            mavlinkGPSThread.start()
        else:
            print("ERROR: Unable to connect to " + mavlinksetting)
            exit(1)
    else:
        # No mavlink specified.  Check the local GPS.
        if GPSEngine.GPSDRunning():
            gpsEngine.start()
            print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Local gpsd Found.  Providing GPS coordinates when synchronized.")
        else:
            print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] No local gpsd running.  No GPS data will be provided.")

    # Start announce if needed
    announceThread = None
    
    if args.sendannounce:
        print('Sending agent announcements on port ' + str(port) + '.')
        announceThread = AnnounceThread(port)
        announceThread.start()
        
    # Run HTTP Server
    server = SparrowWiFiAgent()
    server.run(port)
    
    if mavlinkGPSThread:
        mavlinkGPSThread.signalStop = True
        print('Waiting for mavlink GPS thread to terminate...')
        while (mavlinkGPSThread.threadRunning):
            sleep(0.2)

    if useMavlink and vehicle:
        vehicle.close()
        
    if announceThread:
        announceThread.signalStop = True
        
        print('Waiting for announce thread to terminate...')
        while (announceThread.threadRunning):
            sleep(0.2)
