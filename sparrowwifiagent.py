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
from threading import Thread, Lock
from http import server as HTTPServer

from wirelessengine import WirelessEngine
from sparrowgps import GPSEngine, GPSStatus
from sparrowdrone import SparrowDroneMavlink

try:
    from manuf import manuf
    hasOUILookup = True
except:
    hasOUILookup = False

# ------   Global setup ------------
gpsEngine = GPSEngine()
curTime = datetime.datetime.now()

useMavlink = False
vehicle = None
mavlinkGPSThread = None

lockList = {}

# ------   OUI lookup functions ------------
def getOUIDB():
    ouidb = None
    
    if hasOUILookup:
        if  os.path.isfile('manuf'):
            # We have the file but let's not update it every time we run the app.
            # every 90 days should be plenty
            last_modified_date = datetime.datetime.fromtimestamp(os.path.getmtime('manuf'))
            now = datetime.datetime.now()
            age = now - last_modified_date
            
            if age.days > 90:
                updateflag = True
            else:
                updateflag = False
        else:
            # We don't have the file, let's get it
            updateflag = True
            
        try:
            ouidb = manuf.MacParser(update=updateflag)
        except:
            ouidb = None
    else:
        ouidb = None
        
    return ouidb
    
# ------------------  Agent auto scan thread  ------------------------------
class AutoAgentScanThread(Thread):
    def __init__(self, interface):
        global lockList
        
        super(AutoAgentScanThread, self).__init__()
        self.interface = interface
        self.signalStop = False
        self.scanDelay = 0.5  # seconds
        self.threadRunning = False
        self.discoveredNetworks = {}

        self.ouiLookupEngine = getOUIDB()
        
        if interface not in lockList.keys():
            lockList[interface] = Lock()
            
        if  not os.path.exists('./recordings'):
            os.makedirs('./recordings')
            
        now = datetime.datetime.now()
        
        self.filename = './recordings/' + str(now.year) + "_" + str(now.month) + "_" + str(now.day) + "_" + str(now.hour) + "_" + str(now.minute) + "_" + str(now.second) + ".csv"

        print('Capturing on ' + interface + ' and writing to ' + self.filename)
                
    def run(self):
        global lockList
        
        self.threadRunning = True
        
        curIterator = 0
        
        if self.interface not in lockList.keys():
            lockList[self.interface] = Lock()
        
        curLock = lockList[self.interface]
        
        while (not self.signalStop):
            # Scan all / normal mode
            if (curLock):
                curLock.acquire()
            retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(self.interface)
            if (curLock):
                curLock.release()
                
            if (retCode == 0):
                # self.statusBar().showMessage('Scan complete.  Found ' + str(len(wirelessNetworks)) + ' networks')
                if wirelessNetworks and (len(wirelessNetworks) > 0) and (not self.signalStop):
                    for netKey in wirelessNetworks.keys():
                        curNet = wirelessNetworks[netKey]
                        curKey = curNet.getKey()
                        if curKey not in self.discoveredNetworks.keys():
                            self.discoveredNetworks[curKey] = curNet
                        else:
                            # Network exists, need to update it.
                            pastNet = self.discoveredNetworks[curKey]
                            # Need to save strongest gps and first seen.  Everything else can be updated.
                            # Carry forward firstSeen
                            curNet.firstSeen = pastNet.firstSeen # This is one field to carry forward
                            
                            # Check strongest signal
                            if pastNet.strongestsignal > curNet.signal:
                                curNet.strongestsignal = pastNet.strongestsignal
                                curNet.strongestgps.latitude = pastNet.strongestgps.latitude
                                curNet.strongestgps.longitude = pastNet.strongestgps.longitude
                                curNet.strongestgps.altitude = pastNet.strongestgps.altitude
                                curNet.strongestgps.speed = pastNet.strongestgps.speed
                                curNet.strongestgps.isValid = pastNet.strongestgps.isValid
                                
                            self.discoveredNetworks[curKey] = curNet
                            
                    if not self.signalStop:
                        self.exportNetworks()
        
            sleep(self.scanDelay)
              
        self.threadRunning = False
        
    def ouiLookup(self, macAddr):
        clientVendor = ""
        
        if hasOUILookup:
            try:
                if self.ouiLookupEngine:
                    clientVendor = self.ouiLookupEngine.get_manuf(macAddr)
            except:
                clientVendor = ""
            
        return clientVendor

    def exportNetworks(self):
        try:
            self.outputFile = open(self.filename, 'w')
        except:
            print('ERROR: Unable to write to file ' + self.filename)
            exit(1)

        self.outputFile.write('macAddr,vendor,SSID,Security,Privacy,Channel,Frequency,Signal Strength,Strongest Signal Strength,Bandwidth,Last Seen,First Seen,GPS Valid,Latitude,Longitude,Altitude,Speed,Strongest GPS Valid,Strongest Latitude,Strongest Longitude,Strongest Altitude,Strongest Speed\n')
        
        for netKey in self.discoveredNetworks.keys():
            curData = self.discoveredNetworks[netKey]
            vendor = self.ouiLookup(curData.macAddr)
            
            if vendor is None:
                vendor = ''
            
            channelstr = str(curData.channel)
            if curData.secondaryChannel > 0:
                channelstr = channelstr + '+' + str(curData.secondaryChannel)
                
            self.outputFile.write(curData.macAddr  + ',' + vendor + ',' + curData.ssid + ',' + curData.security + ',' + curData.privacy)
            self.outputFile.write(',' + channelstr + ',' + str(curData.frequency) + ',' + str(curData.signal) + ',' + str(curData.strongestsignal) + ',' + str(curData.bandwidth) + ',' +
                                    curData.lastSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + curData.firstSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + 
                                    str(curData.gps.isValid) + ',' + str(curData.gps.latitude) + ',' + str(curData.gps.longitude) + ',' + str(curData.gps.altitude) + ',' + str(curData.gps.speed) + ',' + 
                                    str(curData.strongestgps.isValid) + ',' + str(curData.strongestgps.latitude) + ',' + str(curData.strongestgps.longitude) + ',' + str(curData.strongestgps.altitude) + ',' + str(curData.strongestgps.speed) + '\n')

        self.outputFile.close()
        
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
        global lockList
        
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
            inputstr = s.path.replace('/wireless/networks/', '')
            # Sanitize command-line input here:
            p = re.compile('^([0-9a-zA-Z]+)')
            try:
                fieldValue = p.search(inputstr).group(1)
            except:
                fieldValue = ""

            if len(fieldValue) == 0:
                return
            
            if '?' in inputstr:
                splitlist = inputstr.split('?')
                curInterface = splitlist[0]
            else:
                curInterface = inputstr
                
            p = re.compile('.*Frequencies=(.*)', re.IGNORECASE)
            try:
                channelStr = p.search(inputstr).group(1)
            except:
                channelStr = ""

            huntChannelList = []
            
            if ',' in channelStr:
                tmpList = channelStr.split(',')
            else:
                tmpList = []
            
            if len(tmpList) > 0:
                for curItem in tmpList:
                    try:
                        if len(curItem) > 0:
                            huntChannelList.append(int(curItem))
                            # Get results for the specified interface
                            # Need to iterate through the channels and aggregate the results
                    except:
                        pass

            if curInterface not in lockList.keys():
                lockList[curInterface] = Lock()
            
            curLock = lockList[curInterface]
            
            if (curLock):
                curLock.acquire()
                
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
                
            if (curLock):
                curLock.release()
                
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
    argparser.add_argument('--recordinterface', help="Automatically start recording locally with the given wireless interface (headless mode) in a recordings directory", default='', required=False)
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
    else:
        announceThread = None

    if len(args.recordinterface) > 0:
        recordThread = AutoAgentScanThread(args.recordinterface)
        recordThread.start()
    else:
        recordThread = None
        
    # Run HTTP Server
    server = SparrowWiFiAgent()
    server.run(port)
    
    if mavlinkGPSThread:
        mavlinkGPSThread.signalStop = True
        print('Waiting for mavlink GPS thread to terminate...')
        while (mavlinkGPSThread.threadRunning):
            sleep(0.2)

    if recordThread:
        recordThread.signalStop = True
        print('Waiting for record thread to terminate...')
        while (recordThread.threadRunning):
            sleep(0.2)
        
    if useMavlink and vehicle:
        vehicle.close()
        
    if announceThread:
        announceThread.signalStop = True
        
        print('Waiting for announce thread to terminate...')
        while (announceThread.threadRunning):
            sleep(0.2)
