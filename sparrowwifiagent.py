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
import configparser
import subprocess

from socket import *
from time import sleep
from threading import Thread, Lock
from http import server as HTTPServer

from wirelessengine import WirelessEngine
from sparrowgps import GPSEngine, GPSStatus
from sparrowdrone import SparrowDroneMavlink
from sparrowrpi import SparrowRPi

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

# Lock list is a dictionary of thread locks for scanning interfaces
lockList = {}

allowedIPs = []
useRPILeds = False

# runningcfg is created in main
runningcfg = None

recordThread = None
announceThread = None

# ------   Global functions ------------
def stringtobool(instr):
    if (instr == 'True' or instr == 'true'):
        return True
    else:
        return False

def TwoDigits(instr):
    # Fill in a leading zero for single-digit numbers
    while len(instr) < 2:
        instr = '0' + instr
        
    return instr

def restartAgent():
    if mavlinkGPSThread:
        mavlinkGPSThread.signalStop = True
        print('Waiting for mavlink GPS thread to terminate...')
        while (mavlinkGPSThread.threadRunning):
            sleep(0.2)

    stopRecord()
        
    stopAnnounceThread()
    
    if runningcfg.useRPiLEDs:
        SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
        
    if os.path.isfile('/usr/local/bin/python3.5') or os.path.isfile('/usr/bin/python3.5'):
        exefile = 'python3.5'
    else:
        exefile = 'python3'
        
    params = [exefile, __file__, '--delaystart=2']

    newCommand = exefile + ' ' + __file__ + ' --delaystart=2 &'
    os.system(newCommand)
    # subprocess.Popen(params, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # result = subprocess.run(params, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # restartResult = result.stdout.decode('ASCII')
    os.kill(os.getpid(), 9)
    
def updateRunningConfig(newCfg):
    global runningcfg
    
    if runningcfg.newCfg.ipAllowedList != newCfg.ipAllowedList:
        buildAllowedIPs(newCfg.ipAllowedList)
        
    # port we ignore since we're already running
    # useRPiLEDs will just update
    
    # Announce
    if runningcfg.announce != newCfg.announce:
        if not newCfg.announce:
            stopAnnounceThread()
        else:
            # start will check if it's already running
            startAnnounceThread()
    
    # mavlinkGPS
    # Need to restart to update mavlinkGPS
    # So just copy forward
    newCfg.mavlinkGPS = runningcfg.mavlinkGPS
    
    # recordInterface
    if runningcfg.recordInterface != newCfg.recordInterface:
        if len(newCfg.recordInterface) == 0:
            stopRecord(newCfg.recordInterface)
        else:
            # start will check if it's already running
            startRecord()
            
    # Finally swap out the config
    runningcfg = newCfg

def startRecord(interface):    
    global recordThread
    
    if recordThread:
        return
        
    if len(interface) > 0:
        recordThread = AutoAgentScanThread(interface)
        recordThread.start()
    else:
        recordThread = None
        
def stopRecord():
    global recordThread
    
    if recordThread:
        recordThread.signalStop = True
        print('Waiting for record thread to terminate...')
        while (recordThread.threadRunning):
            sleep(0.2)
            
def stopAnnounceThread():
    global announceThread
    
    if announceThread:
        announceThread.signalStop = True
        
        print('Waiting for announce thread to terminate...')
        while (announceThread.threadRunning):
            sleep(0.2)
            
        announceThread = None

def startAnnounceThread():
    global runningcfg
    global announceThread
    
    # Start announce if needed
    if announceThread:
        # It's already running
        return
        
    print('Sending agent announcements on port ' + str(runningcfg.port) + '.')
    announceThread = AnnounceThread(runningcfg.port)
    announceThread.start()

def buildAllowedIPs(allowedIPstr):
    global allowedIPs

    allowedIPs = []
    
    if len(allowedIPstr) > 0:
        ippattern = re.compile('([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})')
        if ',' in allowedIPstr:
            tmpList = allowedIPstr.split(',')
            for curItem in tmpList:
                ipStr = curItem.replace(' ', '')
                try:
                    ipValue = ippattern.search(ipStr).group(1)
                except:
                    ipValue = ""
                    print('ERROR: Unknown IP pattern: ' + ipStr)
                    exit(3)
                
                if len(ipValue) > 0:
                    allowedIPs.append(ipValue)
        else:
            ipStr = allowedIPstr.replace(' ', '')
            try:
                ipValue = ippattern.search(ipStr).group(1)
            except:
                ipValue = ""
                print('ERROR: Unknown IP pattern: ' + ipStr)
                return False
                
            if len(ipValue) > 0:
                allowedIPs.append(ipValue)
        
    return True
    
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

# ------------------  Config Settings  ------------------------------
class AgentConfigSettings(object):
    def __init__(self):
        self.cancelStart = False
        self.port = 8020
        self.announce = False
        self.useRPiLEDs = False
        self.recordInterface=""
        self.mavlinkGPS = ""
        self.ipAllowedList = ""
        
    def __str__(self):
        retVal = "Cancel Start: " + str(self.cancelStart) + "\n"
        retVal += "Port: " + str(self.port) + "\n"
        retVal += "Announce Agent: " + str(self.announce) + "\n"
        retVal += "Use RPi LEDs: " + str(self.useRPiLEDs) + "\n"
        retVal += "Record Interface: " + self.recordInterface + "\n"
        retVal += "Mavlink GPS: " + self.mavlinkGPS + "\n"
        retVal += "IP Allowed List: " + self.ipAllowedList + "\n"
        
        return retVal
        
    def __eq__(self, obj):
        # This is equivance....   ==
        if not isinstance(obj, AgentConfigSettings):
           return False
          
        if self.cancelStart != obj.cancelStart:
            return False
        if self.port != obj.port:
            return False

        if self.announce != obj.announce:
            return False
            
        if self.useRPiLEDs != obj.useRPiLEDs:
            return False
            
        if self.recordInterface != obj.recordInterface:
            return False
            
        if self.mavlinkGPS != obj.mavlinkGPS:
            return False
            
        if self.ipAllowedList != obj.ipAllowedList:
            return False
            
        return True

    def __ne__(self, other):
            return not self.__eq__(other)
        
    def toJsondict(self):
        dictjson = {}
        dictjson['cancelstart'] = str(self.cancelStart)
        dictjson['port'] = self.port
        dictjson['announce'] = str(self.announce)
        dictjson['userpileds'] = str(self.useRPiLEDs)
        dictjson['recordinterface'] = self.recordInterface
        dictjson['mavlinkgps'] = self.mavlinkGPS
        dictjson['allowedips'] = self.ipAllowedList
                
        return dictjson
        
    def toJson(self):
        dictjson = self.toJsondict()
        return json.dumps(dictjson)
    
    def fromJsondict(self, dictjson):
        try:
            self.cancelStart = stringtobool(dictjson['cancelstart'])
            self.port = int(dictjson['port'])
            self.announce = stringtobool(dictjson['announce'])
            self.useRPiLEDs = stringtobool(dictjson['userpileds'])
            self.recordInterface = dictjson['recordinterface']
            self.mavlinkGPS = dictjson['mavlinkgps']
            self.ipAllowedList = dictjson['allowedips']
        except:
            pass
            
    def fromJson(self, jsonstr):
        dictjson = json.loads(jsonstr)
        self.fromJsondict(dictjson)

    def toConfigFile(self, cfgFile):
        config = configparser.ConfigParser()
        
        config['agent'] = self.toJsondict()
        
        try:
            with open(cfgFile, 'w') as configfile:
                config.write(configfile)
                
            return True
        except:
            return False
            
    def fromConfigFile(self, cfgFile):
        if os.path.isfile(cfgFile):
            cfgParser = configparser.ConfigParser()
            
            try:
                cfgParser.read(cfgFile)
                
                section="agent"
                options = cfgParser.options(section)
                for option in options:
                    try:
                        if option =='cancelstart':
                            self.cancelStart = stringtobool(cfgParser.get(section, option))
                        elif option == 'sendannounce':
                            self.announce = stringtobool(cfgParser.get(section, option))
                        elif option == 'userpileds':
                            self.useRPiLEDs = stringtobool(cfgParser.get(section, option))
                        elif option == 'port':
                            self.port=int(cfgParser.get(section, option))
                        elif option == 'recordinterface':
                            self.recordInterface=cfgParser.get(section, option)
                        elif option == 'mavlinkgps':
                            self.mavlinkGPS=cfgParser.get(section, option)
                        elif option == 'allowedips':
                            self.ipAllowedList = cfgParser.get(section, option)
                    except:
                        print("exception on %s!" % option)
                        settings[option] = None
            except:
                print("ERROR: Unable to read config file: ", cfgFile)
                return False
        else:
            return False
            
        return True
    
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

        try:
            self.hostname = os.uname()[1]
        except:
            self.hostname = 'unknown'
            
        if len(self.hostname) == 0:
            self.hostname = 'unknown'

        self.ouiLookupEngine = getOUIDB()
        
        if interface not in lockList.keys():
            lockList[interface] = Lock()
            
        if  not os.path.exists('./recordings'):
            os.makedirs('./recordings')
            
        now = datetime.datetime.now()
        
        self.filename = './recordings/' + self.hostname + '_' + str(now.year) + "-" + TwoDigits(str(now.month)) + "-" + TwoDigits(str(now.day))
        self.filename += "_" + TwoDigits(str(now.hour)) + "_" + TwoDigits(str(now.minute)) + "_" + TwoDigits(str(now.second)) + ".csv"

        print('Capturing on ' + interface + ' and writing to ' + self.filename)
                
    def run(self):
        global lockList
        
        self.threadRunning = True
        
        if self.interface not in lockList.keys():
            lockList[self.interface] = Lock()
        
        curLock = lockList[self.interface]
        
        lastState = -1
        
        while (not self.signalStop):
            # Scan all / normal mode
            if (curLock):
                curLock.acquire()
            retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(self.interface)
            if (curLock):
                curLock.release()
                
            if (retCode == 0):
                if useMavlink:
                    gpsCoord = GPSStatus()
                    gpsCoord.gpsInstalled = True
                    gpsCoord.gpsRunning = True
                    gpsCoord.isValid = mavlinkGPSThread.synchronized
                    gpsCoord.latitude = mavlinkGPSThread.latitude
                    gpsCoord.longitude = mavlinkGPSThread.longitude
                    gpsCoord.altitude = mavlinkGPSThread.altitude
                    gpsCoord.speed = mavlinkGPSThread.vehicle.getAirSpeed()
                elif gpsEngine.gpsValid():
                    gpsCoord = gpsEngine.lastCoord
                    if useRPILeds  and (lastState !=SparrowRPi.LIGHT_STATE_ON):
                        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
                        lastState = SparrowRPi.LIGHT_STATE_ON
                else:
                    gpsCoord = GPSStatus()
                    if useRPILeds and (lastState !=SparrowRPi.LIGHT_STATE_HEARTBEAT) :
                        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
                        lastState = SparrowRPi.LIGHT_STATE_HEARTBEAT
                    
                # self.statusBar().showMessage('Scan complete.  Found ' + str(len(wirelessNetworks)) + ' networks')
                if wirelessNetworks and (len(wirelessNetworks) > 0) and (not self.signalStop):
                    for netKey in wirelessNetworks.keys():
                        curNet = wirelessNetworks[netKey]
                        curNet.gps = gpsCoord
                        curNet.strongestgps = gpsCoord
                        
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
                
            self.outputFile.write(curData.macAddr  + ',' + vendor + ',"' + curData.ssid + '",' + curData.security + ',' + curData.privacy)
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
        lastState = -1
        
        while (not self.signalStop):
            self.synchronized, self.latitude, self.longitude, self.altitude = self.vehicle.getGlobalGPS()
            
            if self.synchronized:
                # Solid on synchronized
                if useRPILeds and (lastState != SparrowRPi.LIGHT_STATE_ON):
                    SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
                    lastState = SparrowRPi.LIGHT_STATE_ON
            else:
                # heartbeat on unsynchronized
                if useRPILeds and (lastState != SparrowRPi.LIGHT_STATE_HEARTBEAT):
                    SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
                    lastState = SparrowRPi.LIGHT_STATE_HEARTBEAT

            sleep(self.scanDelay)
                    
        self.threadRunning = False

class SparrowWiFiAgent(object):
    # See https://docs.python.org/3/library/http.server.html
    # For HTTP Server info
    def run(self, port):
        global useRPILeds
        
        server_address = ('', port)
        httpd = HTTPServer.HTTPServer(server_address, SparrowWiFiAgentRequestHandler)
        
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Starting Sparrow-wifi agent on port " + str(port))

        if useRPILeds:
            SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)
            
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    
        httpd.server_close()
        
        if useRPILeds:
            SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
            
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Sparrow-wifi agent stopped.")

# ---------------  HTTP Request Handler --------------------
# Sample handler: https://wiki.python.org/moin/BaseHttpServer
class SparrowWiFiAgentRequestHandler(HTTPServer.BaseHTTPRequestHandler):
    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_POST(s):
        global runningcfg
        
        if len(s.client_address) == 0:
            # This should have the connecting client IP.  If this isn't at least 1, something is wrong
            return
        
        if len(allowedIPs) > 0:
            if s.client_address[0] not in allowedIPs:
                s.send_response(403)
                s.send_header("Content-type", "text/html")
                s.end_headers()
                s.wfile.write("<html><body><p>Connections not authorized from your IP address</p>".encode("utf-8"))
                s.wfile.write("</body></html>".encode("UTF-8"))
                return
                
        if (s.path != '/system/config'):
            s.send_response(404)
            s.send_header("Content-type", "text/html")
            s.end_headers()
            s.wfile.write("<html><body><p>Page not found.</p>".encode("utf-8"))
            s.wfile.write("</body></html>".encode("UTF-8"))
            return
            
        try:
            length = int(s.headers['Content-Length'])
        except:
            length = 0
            
        if length <= 0:
            responsedict = {}
            responsedict['errcode'] = 1
            responsedict['errmsg'] = 'Agent received a zero-length request.'

            s.send_response(400)
            s.send_header("Content-type", "application/jsonrequest")
            s.end_headers()
            jsonstr = json.dumps(responsedict)
            s.wfile.write(jsonstr.encode("UTF-8"))
            return
            
        jsonstr_data = s.rfile.read(length).decode('utf-8')
        
        try:
            jsondata = json.loads(jsonstr_data)
        except:
            return
            
        # -------------  Update startup config ------------------
        try:
            scfg = jsondata['startup']
            startupCfg = AgentConfigSettings()
            startupCfg.fromJsondict(scfg)
            
            dirname, filename = os.path.split(os.path.abspath(__file__))
            cfgFile = dirname + '/sparrowwifiagent.cfg'
            retVal = startupCfg.toConfigFile(cfgFile)
            
            if not retVal:
                # HTML 400 = Bad request
                s.send_response(400)
                responsedict = {}
                responsedict['errcode'] = 2
                responsedict['errmsg'] = 'An error occurred saving the startup config.'

                s.send_response(400)
                s.send_header("Content-type", "application/jsonrequest")
                s.end_headers()
                jsonstr = json.dumps(responsedict)
                s.wfile.write(jsonstr.encode("UTF-8"))
        except:
            responsedict = {}
            responsedict['errcode'] = 3
            responsedict['errmsg'] = 'Bad startup config.'

            s.send_response(400)
            s.send_header("Content-type", "application/jsonrequest")
            s.end_headers()
            jsonstr = json.dumps(responsedict)
            s.wfile.write(jsonstr.encode("UTF-8"))

        # -------------  Check if we should reboot ------------------
        if 'rebootagent' in jsondata:
            rebootFlag = jsondata['rebootagent']
            if rebootFlag:
                responsedict = {}
                responsedict['errcode'] = 0
                responsedict['errmsg'] = 'Restarting agent.'

                s.send_response(200)
                s.send_header("Content-type", "application/jsonrequest")
                s.end_headers()
                jsonstr = json.dumps(responsedict)
                s.wfile.write(jsonstr.encode("UTF-8"))
                
                restartAgent()
            
        # If we're restarting, we'll never get to running config.
        
        # -------------  Update Running config ------------------
        
        try:
            rcfg = jsondata['running']
            tmpcfg = AgentConfigSettings()
            tmpcfg.fromJsondict(rcfg)
            
            updateRunningConfig(tmpcfg)
        except:
            responsedict = {}
            responsedict['errcode'] = 4
            responsedict['errmsg'] = 'Bad running config.'

            s.send_response(400)
            s.send_header("Content-type", "application/jsonrequest")
            s.end_headers()
            jsonstr = json.dumps(responsedict)
            s.wfile.write(jsonstr.encode("UTF-8"))

        # -------------  Done updating config ------------------
        
    def do_GET(s):
        global gpsEngine
        global useMavlink
        global mavlinkGPSThread
        global lockList
        global allowedIPs
        global runningcfg

        # For RPi LED's, using it during each get request wasn't completely working.  Short transactions like
        # status and interface list were so quick the light would get "confused" and stay off.  So
        # the LED is only used for long calls like scan
        
        if len(s.client_address) == 0:
            # This should have the connecting client IP.  If this isn't at least 1, something is wrong
            return
        
        if len(allowedIPs) > 0:
            if s.client_address[0] not in allowedIPs:
                s.send_response(403)
                s.send_header("Content-type", "text/html")
                s.end_headers()
                s.wfile.write("<html><body><p>Connections not authorized from your IP address</p>".encode("utf-8"))
                s.wfile.write("</body></html>".encode("UTF-8"))
                if useRPILeds:
                # Green will heartbeat when servicing requests. Turn back solid here
                    SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)
                return
                
        if ((s.path != '/wireless/interfaces') and (s.path != '/gps/status') and 
            ('/wireless/networks/' not in s.path) and ('/system/config' not in s.path)):
            s.send_response(404)
            s.send_header("Content-type", "text/html")
            s.end_headers()
            s.wfile.write("<html><body><p>Bad Request</p>".encode("utf-8"))
            s.wfile.write("</body></html>".encode("UTF-8"))
            if useRPILeds:
                # Green will heartbeat when servicing requests. Turn back solid here
                SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)
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
                    if useRPILeds:
                        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
                else:
                    if useRPILeds:
                        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
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
        elif s.path == '/system/config':
            cfgSettings = AgentConfigSettings()
            cfgSettings.fromConfigFile('sparrowwifiagent.cfg')
            responsedict = {}
            responsedict['startup'] = cfgSettings.toJsondict()
            
            responsedict['running'] = runningcfg.toJsondict()
            
            jsonstr = json.dumps(responsedict)
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
                if useRPILeds:
                    # Green will heartbeat when servicing requests. Turn back solid here
                    SparrowRPi.greenLED(LIGHT_STATE_ON)
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

            if useRPILeds:
                # Green will heartbeat when servicing requests
                SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
                sleep(0.1)
                
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
                if useRPILeds:
                    SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
            else:
                retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(fieldValue, None, huntChannelList)
                if useRPILeds:
                    SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
                
            if (curLock):
                curLock.release()
                
            s.wfile.write(jsonstr.encode("UTF-8"))
            
        if useRPILeds:
            # Green will heartbeat when servicing requests. Turn back solid here
            SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)

# ----------------- Main -----------------------------
if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='Sparrow-wifi agent')
    argparser.add_argument('--port', help='Port for HTTP server to listen on', default=8020, required=False)
    argparser.add_argument('--allowedips', help="IP addresses allowed to connect to this agent.  Default is any.  This can be a comma-separated list for multiple IP addresses", default='', required=False)
    argparser.add_argument('--mavlinkgps', help="Use Mavlink (drone) for GPS.  Options are: '3dr' for a Solo, 'sitl' for local simulator, or full connection string ('udp/tcp:<ip>:<port>' such as: 'udp:10.1.1.10:14550')", default='', required=False)
    argparser.add_argument('--sendannounce', help="Send a UDP broadcast packet on the specified port to announce presence", action='store_true', default=False, required=False)
    argparser.add_argument('--userpileds', help="Use RPi LEDs to signal state.  Red=GPS [off=None,blinking=Unsynchronized,solid=synchronized], Green=Agent Running [On=Running, blinking=servicing HTTP request]", action='store_true', default=False, required=False)
    argparser.add_argument('--recordinterface', help="Automatically start recording locally with the given wireless interface (headless mode) in a recordings directory", default='', required=False)
    argparser.add_argument('--ignorecfg', help="Don't load any config files (useful for overriding and/or testing)", action='store_true', default=False, required=False)
    argparser.add_argument('--cfgfile', help="Use the specified config file rather than the default sparrowwifiagent.cfg file", default='', required=False)
    argparser.add_argument('--delaystart', help="Wait <delaystart> seconds before initializing", default=0, required=False)
    args = argparser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: You need to have root privileges to run this script.  Please try again, this time using 'sudo'. Exiting.\n")
        exit(2)

    # See if we have a config file:
    dirname, filename = os.path.split(os.path.abspath(__file__))
    
    settings = {}
    runningcfg=AgentConfigSettings()

    if len(args.cfgfile) == 0:
        cfgFile = dirname + '/sparrowwifiagent.cfg'
    else:
        cfgFile = args.cfgfile
        # Since it's user-specified, let's see if it exists.
        if not os.path.isfile(cfgFile):
            print("ERROR: Unable to find the specified config file.")
            exit(3)
            
    if os.path.isfile(cfgFile) and (not args.ignorecfg):
        cfgParser = configparser.ConfigParser()
        
        try:
            cfgParser.read(cfgFile)
            
            section="agent"
            options = cfgParser.options(section)
            for option in options:
                try:
                    if option == 'sendannounce' or option == 'userpileds' or option == 'cancelstart':
                        settings[option] = stringtobool(cfgParser.get(section, option))
                    else:
                        settings[option] = cfgParser.get(section, option)
                except:
                    print("exception on %s!" % option)
                    settings[option] = None
        except:
            print("ERROR: Unable to read config file: ", cfgFile)
            exit(1)

    # Set up parameters
    
    if 'cancelstart' in settings.keys():
        if settings['cancelstart']:
            exit(0)

    delayStart = int(args.delaystart)
    if delayStart > 0:
        sleep(delayStart)
        
    runningcfg.cancelStart = False
    
    if 'port' not in settings.keys():
        port = args.port
    else:
        port = int(settings['port'])
    
    runningcfg.port = port
    
    if 'sendannounce' not in settings.keys():
        sendannounce = args.sendannounce
    else:
        sendannounce = settings['sendannounce']
    
    runningcfg.announce = sendannounce
    
    if 'userpileds' not in settings.keys():
        useRPILeds = args.userpileds
    else:
        useRPILeds = settings['userpileds']
    
    runningcfg.useRPiLEDs = useRPILeds
    
    if 'allowedips' not in settings.keys():
        allowedIPstr = args.allowedips
    else:
        allowedIPstr = settings['allowedips']
    
    runningcfg.ipAllowedList = allowedIPstr
    
    if 'mavlinkgps' not in settings.keys():
        mavlinksetting = args.mavlinkgps
    else:
        mavlinksetting = settings['mavlinkgps']

    runningcfg.mavlinkGPS = mavlinksetting
    
    if 'recordinterface' not in settings.keys():
        recordinterface = args.recordinterface
    else:
        recordinterface = settings['recordinterface']
    
    runningcfg.recordInterface = recordinterface
    
    # Now start logic
    
    if runningcfg.useRPiLEDs:
        # One extra check that the LED's are really present
        runningcfg.useRPiLEDs = SparrowRPi.hasLights()
        
        if not runningcfg.useRPiLEDs:
            # we changed state.  Print warning
            print('WARNING: RPi LEDs were requested but were not found on this platform.')
        
    # Now check again:
    if runningcfg.useRPiLEDs:
        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_OFF)
        SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)

    buildAllowedIPs(allowedIPstr)
    
    if len(runningcfg.mavlinkGPS) > 0:
        vehicle = SparrowDroneMavlink()
        
        print('Connecting to ' + runningcfg.mavlinkGPS)

        connected = False
        synchronized = False

        if runningcfg.useRPiLEDs:
            SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_OFF)
            
        # If we're in drone gps mode, wait for the drone to be up and gps synchronized before starting.
        while (not connected) or (not synchronized):
            if not connected:
                if runningcfg.mavlinkGPS == '3dr':
                    retVal = vehicle.connectToSolo()
                elif (runningcfg.mavlinkGPS == 'sitl'):
                    retVal = vehicle.connectToSimulator()
                else:
                    retVal = vehicle.connect(runningcfg.mavlinkGPS)
                    
                connected = retVal

            if connected:
                if runningcfg.useRPiLEDs:
                    SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
                    
                print('Mavlink connected.')
                print('Current GPS Info:')
                
                # get synchronized flag and position
                synchronized, latitude, longitude, altitude = vehicle.getGlobalGPS()
                
                print('Synchronized: ' + str(synchronized))
                print('Latitude: ' + str(latitude))
                print('Longitude: ' + str(longitude))
                print('Altitude (m): ' + str(altitude))
                print('Heading: ' + str(vehicle.getHeading()))
                
                if synchronized:
                    useMavlink = True
                    mavlinkGPSThread = MavlinkGPSThread(vehicle)
                    mavlinkGPSThread.start()
                    print('Mavlink GPS synchronized.  Continuing.')
                else:
                    print('Mavlink GPS not synchronized yet.  Waiting...')
                    sleep(2)
            else:
                print("ERROR: Unable to connect to " + mavlinksetting + '.  Retrying...')
                sleep(2)

            if runningcfg.useRPiLEDs:
                SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
    else:
        # No mavlink specified.  Check the local GPS.
        if GPSEngine.GPSDRunning():
            if runningcfg.useRPiLEDs:
                SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
                
            gpsEngine.start()
            print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Local gpsd Found.  Providing GPS coordinates when synchronized.")
            
            if useRPILeds:
                sleep(1)
                if gpsEngine.gpsValid():
                    SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
        else:
            print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] No local gpsd running.  No GPS data will be provided.")

    if runningcfg.announce:
        startAnnounceThread()

    if len(runningcfg.recordInterface) > 0:
        startRecord(runningcfg.recordInterface)

    # -------------- Run HTTP Server / Main Loop-------------- 
    server = SparrowWiFiAgent()
    server.run(runningcfg.port)

    # -------------- This is the shutdown process -------------- 
    if mavlinkGPSThread:
        mavlinkGPSThread.signalStop = True
        print('Waiting for mavlink GPS thread to terminate...')
        while (mavlinkGPSThread.threadRunning):
            sleep(0.2)

    stopRecord()
        
    if useMavlink and vehicle:
        vehicle.close()

    stopAnnounceThread()
    
    if runningcfg.useRPiLEDs:
        SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
