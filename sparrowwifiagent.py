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
import sys
import datetime
import json
import re
import argparse
import configparser
# import subprocess

from socket import *
from time import sleep
from threading import Thread, Lock
from dateutil import parser
from http import server as HTTPServer
from socketserver import ThreadingMixIn

from wirelessengine import WirelessEngine
from sparrowgps import GPSEngine, GPSStatus
try:
    from sparrowdrone import SparrowDroneMavlink
    hasDroneKit = True
except:
    hasDroneKit = False
    
from sparrowrpi import SparrowRPi
from sparrowbluetooth import SparrowBluetooth, BluetoothDevice
from sparrowhackrf import SparrowHackrf
from sparrowcommon import gzipCompress

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
hasFalcon = False
hasBluetooth = False
hasUbertooth = False
falconWiFiRemoteAgent = None

bluetooth = None
hackrf = SparrowHackrf()

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

def deleteRecordingFiles(filelist):
    dirname, filename = os.path.split(os.path.abspath(__file__))
    recordingsDir = dirname + '/recordings'
    retVal = ''
    for curFilename in filelist:
        # This split is simply a safety check to prevent path traversal attacks
        dirname, filename = os.path.split(curFilename)
        if len(filename) > 0:
            fullpath = recordingsDir + '/' + filename
            try:
                os.remove(fullpath)
            except:
                if len(retVal) == 0:
                    retVal = filename
                else:
                    retVal += ',' + filename
                    
    return retVal

def getRecordingFiles():
    dirname, filename = os.path.split(os.path.abspath(__file__))
    recordingsDir = dirname + '/recordings'
    if  not os.path.exists(recordingsDir):
        os.makedirs(recordingsDir)
    
    retVal = []
    
    try:
        for filename in os.listdir(recordingsDir):
            fullPath = recordingsDir + '/' + filename
            
            if not os.path.isdir(fullPath):
                curFile = FileSystemFile()
                curFile.filename = filename
                curFile.size = os.path.getsize(fullPath)
                try:
                    curFile.timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(fullPath))
                except:
                    curFile.timestamp = None
                    
                retVal.append(curFile.toJsondict())
    except:
        pass
        
    return retVal

def restartAgent():
    global bluetooth
    
    if mavlinkGPSThread:
        mavlinkGPSThread.signalStop = True
        print('Waiting for mavlink GPS thread to terminate...')
        while (mavlinkGPSThread.threadRunning):
            sleep(0.2)

    stopRecord()
        
    stopAnnounceThread()
    
    if bluetooth:
        bluetooth.stopScanning()

    if runningcfg.useRPiLEDs:
        SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
        
    if hasFalcon:
        falconWiFiRemoteAgent.cleanup()
    
    if os.path.isfile('/usr/local/bin/python3.5') or os.path.isfile('/usr/bin/python3.5'):
        exefile = 'python3.5'
    else:
        exefile = 'python3'
        
    # params = [exefile, __file__, '--delaystart=2']

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
            stopRecord()
        else:
            # start will check if it's already running
            startRecord(newCfg.recordInterface)
            
    # Finally swap out the config
    runningcfg = newCfg

def startRecord(interface):    
    global recordThread
    
    if recordThread:
        return
        
    if len(interface) > 0:
        interfaces = WirelessEngine.getInterfaces()
        
        if interface in interfaces:
            recordThread = AutoAgentScanThread(interface)
            recordThread.start()
        else:
            print('ERROR: Record was requested on ' + interface + ' but that interface was not found.')
    else:
        recordThread = None
        
def stopRecord():
    global recordThread
    
    if recordThread:
        recordThread.signalStop = True
        print('Waiting for record thread to terminate...')

        i=0
        maxCycles = 2 /0.2
        while (recordThread.threadRunning) and (i<maxCycles):
            sleep(0.2)
            i += 1
            
def stopAnnounceThread():
    global announceThread
    
    if announceThread:
        announceThread.signalStop = True
        
        print('Waiting for announce thread to terminate...')
        
        sleep(0.2)
        
        # i=0
        # maxCycles = 5 # int(2.0 /0.2)
        # while (announceThread.threadRunning) and (i<maxCycles):
        #    sleep(0.2)
        #    i += 1
            
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

# ------------------  File  ------------------------------
class FileSystemFile(object):
    def __init__(self):
        self.filename = ""
        self.size = 0
        self.timestamp = None
        
    def __str__(self):
        retVal = self.filename
        
        return retVal
        
    def toJsondict(self):
        jsondict = {}
        jsondict['filename'] = self.filename
        jsondict['size'] = self.size
        jsondict['timestamp'] = str(self.timestamp)
        
        return jsondict
        
    def fromJsondict(self, jsondict):
        self.filename = jsondict['filename']
        self.size = jsondict['size']
        
        if jsondict['timestamp'] == 'None':
            self.timestamp = None
        else:
            self.timestamp = parser.parse(jsondict['timestamp'])
        

# ------------------  Config Settings  ------------------------------
class AgentConfigSettings(object):
    def __init__(self):
        self.cancelStart = False
        self.port = 8020
        self.announce = False
        self.useRPiLEDs = False
        self.recordInterface=""
        self.recordRunning = False
        self.mavlinkGPS = ""
        self.ipAllowedList = ""
        
    def __str__(self):
        retVal = "Cancel Start: " + str(self.cancelStart) + "\n"
        retVal += "Port: " + str(self.port) + "\n"
        retVal += "Announce Agent: " + str(self.announce) + "\n"
        retVal += "Use RPi LEDs: " + str(self.useRPiLEDs) + "\n"
        retVal += "Record Interface: " + self.recordInterface + "\n"
        retVal += "Record Running (for running configs): " + str(self.recordRunning) + "\n"
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
        dictjson['recordrunning'] = str(self.recordRunning)
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
            self.recordRunning = stringtobool(dictjson['recordrunning'])
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
        global hasBluetooth
        
        super(AutoAgentScanThread, self).__init__()
        self.interface = interface
        self.signalStop = False
        self.scanDelay = 0.5  # seconds
        self.threadRunning = False
        self.discoveredNetworks = {}
        self.discoveredBluetoothDevices = {}
        self.daemon = True
        
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
        
        self.filename = './recordings/' + self.hostname  + '_wifi_' + str(now.year) + "-" + TwoDigits(str(now.month)) + "-" + TwoDigits(str(now.day))
        self.filename += "_" + TwoDigits(str(now.hour)) + "_" + TwoDigits(str(now.minute)) + "_" + TwoDigits(str(now.second)) + ".csv"

        self.btfilename = './recordings/' + self.hostname  + '_bt_' + str(now.year) + "-" + TwoDigits(str(now.month)) + "-" + TwoDigits(str(now.day))
        self.btfilename += "_" + TwoDigits(str(now.hour)) + "_" + TwoDigits(str(now.minute)) + "_" + TwoDigits(str(now.second)) + ".csv"

        if hasBluetooth:
            print('Capturing on ' + interface + ' and writing wifi to ' + self.filename)
            print('and writing bluetooth to ' + self.btfilename)
        else:
            print('Capturing on ' + interface + ' and writing wifi to ' + self.filename)
                
    def run(self):
        global lockList
        global hasBluetooth
        
        self.threadRunning = True
        
        if self.interface not in lockList.keys():
            lockList[self.interface] = Lock()
        
        curLock = lockList[self.interface]
        
        if hasBluetooth:
            # Start normal discovery
            bluetooth.startDiscovery(False)
            
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
                        curNet.gps.copy(gpsCoord)
                        curNet.strongestgps.copy(gpsCoord)
                        
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
        
                    # Now if we have bluetooth running export these:
                    if hasBluetooth and bluetooth.discoveryRunning():
                        bluetooth.deviceLock.acquire()
                        
                        # Update GPS
                        now = datetime.datetime.now()
                        
                        for curKey in bluetooth.devices.keys():
                            curDevice = bluetooth.devices[curKey]
                            elapsedTime =  now - curDevice.lastSeen
                            
                            # This is a little bit of a hack for the BlueHydra side since it can take a while to see devices or have
                            # them show up in the db.  For LE discovery scans this will always be pretty quick.
                            if elapsedTime.total_seconds() < 120:
                                curDevice.gps.copy(gpsCoord)
                                if curDevice.rssi >= curDevice.strongestRssi:
                                    curDevice.strongestRssi = curDevice.rssi
                                    curDevice.strongestgps.copy(gpsCoord)
                        # export
                        self.exportBluetoothDevices(bluetooth.devices)
                        bluetooth.deviceLock.release()
                    
            sleep(self.scanDelay)
              
        if hasBluetooth:
            # Start normal discovery
            bluetooth.stopDiscovery()
            
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

    def exportBluetoothDevices(self, devices):
        try:
            btOutputFile = open(self.btfilename, 'w')
        except:
            print('ERROR: Unable to write to bluetooth file ' + self.filename)
            return

        btOutputFile.write('uuid,Address,Name,Company,Manufacturer,Type,RSSI,TX Power,Strongest RSSI,Est Range (m),Last Seen,GPS Valid,Latitude,Longitude,Altitude,Speed,Strongest GPS Valid,Strongest Latitude,Strongest Longitude,Strongest Altitude,Strongest Speed\n')

        for curKey in devices.keys():
            curData = devices[curKey]
            
            btType = ""
            if curData.btType == BluetoothDevice.BT_LE:
                btType = "BTLE"
            else:
                btType = "Classic"
                
            if curData.txPowerValid:
                txPower = str(curData.txPower)
            else:
                txPower = 'Unknown'
                
            btOutputFile.write(curData.uuid  + ',' + curData.macAddress + ',"' + curData.name + '","' + curData.company + '","' + curData.manufacturer)
            btOutputFile.write('","' + btType + '",' + str(curData.rssi) + ',' + str(curData.strongestRssi) + ',' + txPower + ',' + str(curData.iBeaconRange) + ',' +
                                    curData.lastSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + 
                                    str(curData.gps.isValid) + ',' + str(curData.gps.latitude) + ',' + str(curData.gps.longitude) + ',' + str(curData.gps.altitude) + ',' + str(curData.gps.speed) + ',' + 
                                    str(curData.strongestgps.isValid) + ',' + str(curData.strongestgps.latitude) + ',' + str(curData.strongestgps.longitude) + ',' + str(curData.strongestgps.altitude) + ',' + str(curData.strongestgps.speed) + '\n')

        btOutputFile.close()
        
    def exportNetworks(self):
        try:
            self.outputFile = open(self.filename, 'w')
        except:
            print('ERROR: Unable to write to wifi file ' + self.filename)
            return

        self.outputFile.write('macAddr,vendor,SSID,Security,Privacy,Channel,Frequency,Signal Strength,Strongest Signal Strength,Bandwidth,Last Seen,First Seen,GPS Valid,Latitude,Longitude,Altitude,Speed,Strongest GPS Valid,Strongest Latitude,Strongest Longitude,Strongest Altitude,Strongest Speed\n')
        
        for netKey in self.discoveredNetworks.keys():
            curData = self.discoveredNetworks[netKey]
            vendor = self.ouiLookup(curData.macAddr)
            
            if vendor is None:
                vendor = ''
            
            self.outputFile.write(curData.macAddr  + ',' + vendor + ',"' + curData.ssid + '",' + curData.security + ',' + curData.privacy)
            self.outputFile.write(',' + curData.getChannelString() + ',' + str(curData.frequency) + ',' + str(curData.signal) + ',' + str(curData.strongestsignal) + ',' + str(curData.bandwidth) + ',' +
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
        self.daemon = True
        
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
            
            # 4 second delay, but check every second for termination signal
            i=0
            while i<4 and not self.signalStop:
                sleep(1.0)
                i += 1
                    
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
        self.daemon = True
        
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
        global hackrf
        global bluetooth
        global falconWiFiRemoteAgent
        
        server_address = ('', port)
        try:           # httpd = HTTPServer.HTTPServer(server_address, SparrowWiFiAgentRequestHandler)
            httpd = MultithreadHTTPServer(server_address, SparrowWiFiAgentRequestHandler)
        except OSError as e:
            curTime = datetime.datetime.now()
            print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Unable to bind to port " + str(port) +  ". " + e.strerror)
            if runningcfg.useRPiLEDs:
                SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
                SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
            exit(1)
            
    
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
            
        if hasFalcon:
            falconWiFiRemoteAgent.cleanup()
            
        if bluetooth:
            bluetooth.stopScanning()
            
        if hackrf.scanRunning():
            hackrf.stopScanning()
        
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Sparrow-wifi agent stopped.")

# --------------- Multithreaded HTTP Server ------------------------------------
class MultithreadHTTPServer(ThreadingMixIn, HTTPServer.HTTPServer):
    pass

# ---------------  HTTP Request Handler --------------------
# Sample handler: https://wiki.python.org/moin/BaseHttpServer
class SparrowWiFiAgentRequestHandler(HTTPServer.BaseHTTPRequestHandler):
    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_POST(s):
        global runningcfg
        global falconWiFiRemoteAgent
        
        if len(s.client_address) == 0:
            # This should have the connecting client IP.  If this isn't at least 1, something is wrong
            return
        
        if len(allowedIPs) > 0:
            if s.client_address[0] not in allowedIPs:
                try:
                    s.send_response(403)
                    s.send_header("Content-type", "text/html")
                    s.end_headers()
                    s.wfile.write("<html><body><p>Connections not authorized from your IP address</p>".encode("utf-8"))
                    s.wfile.write("</body></html>".encode("UTF-8"))
                except:
                    pass
                return
                
        if (not s.isValidPostURL()):
            try:
                s.send_response(404)
                s.send_header("Content-type", "text/html")
                s.end_headers()
                s.wfile.write("<html><body><p>Page not found.</p>".encode("utf-8"))
                s.wfile.write("</body></html>".encode("UTF-8"))
            except:
                pass
            return
            
        # Get the size of the posted data
        try:
            length = int(s.headers['Content-Length'])
        except:
            length = 0
            
        if length <= 0:
            responsedict = {}
            responsedict['errcode'] = 1
            responsedict['errmsg'] = 'Agent received a zero-length request.'

            try:
                s.send_response(400)
                s.send_header("Content-type", "application/json")
                s.end_headers()
                jsonstr = json.dumps(responsedict)
                s.wfile.write(jsonstr.encode("UTF-8"))
            except:
                pass
            return
            
        # get the POSTed payload
        jsonstr_data = s.rfile.read(length).decode('utf-8')
        
        # Try to convert it to JSON
        try:
            jsondata = json.loads(jsonstr_data)
        except:
            responsedict = {}
            responsedict['errcode'] = 1
            responsedict['errmsg'] = 'bad posted data.'

            try:
                s.send_response(400)
                s.send_header("Content-type", "application/json")
                s.end_headers()
                jsonstr = json.dumps(responsedict)
                s.wfile.write(jsonstr.encode("UTF-8"))
            except:
                pass
            return
            
        if s.path == '/system/config':
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

                    try:
                        s.send_response(400)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            except:
                responsedict = {}
                responsedict['errcode'] = 3
                responsedict['errmsg'] = 'Bad startup config.'

                try:
                    s.send_response(400)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    jsonstr = json.dumps(responsedict)
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass

            # -------------  Check if we should reboot ------------------
            if 'rebootagent' in jsondata:
                rebootFlag = jsondata['rebootagent']
                if rebootFlag:
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = 'Restarting agent.'

                    try:
                        s.send_response(200)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                        
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

                try:
                    s.send_response(400)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    jsonstr = json.dumps(responsedict)
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass

            # -------------  Done updating config ------------------
        elif s.path == '/system/deleterecordings':
            try:
                filelist = jsondata['files']
                
                problemfiles=deleteRecordingFiles(filelist)
                
                responsedict = {}
                
                if len(problemfiles) == 0:
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ""
                else:
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = problemfiles
                    
                jsonstr = json.dumps(responsedict)
                
                try:
                    s.send_response(200)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            except:
                try:
                    s.send_response(400)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Error parsing json"
                except:
                    pass
        elif s.path == '/falcon/stopdeauth':
            if not hasFalcon:
                try:
                    s.send_response(400)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            else:
                # Should get a FalconDeauth object
                # This is in jsondata
                try:
                    apMacAddr = jsondata['apmacaddr']
                    clientMacAddr = jsondata['stationmacaddr']
                    channel = jsondata['channel']
                    curInterface = jsondata['interface']
                    
                    falconWiFiRemoteAgent.stopDeauth(apMacAddr, clientMacAddr, curInterface, channel)
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ""
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.send_response(200)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                except:
                    try:
                        s.send_response(400)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing json"
                        
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
        elif s.path == '/falcon/deauth':
            if not hasFalcon:
                try:
                    s.send_response(400)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            else:
                # Should get a FalconDeauth object
                # This is in jsondata
                try:
                    apMacAddr = jsondata['apmacaddr']
                    clientMacAddr = jsondata['stationmacaddr']
                    channel = jsondata['channel']
                    curInterface = jsondata['interface']
                    continuous = jsondata['continuous']
                    
                    if len(clientMacAddr) == 0:
                        newDeauth = falconWiFiRemoteAgent.deauthAccessPoint(apMacAddr, curInterface, channel, continuous)
                    else:
                        newDeauth = falconWiFiRemoteAgent.deauthAccessPointAndClient(apMacAddr, clientMacAddr, curInterface, channel, continuous)
                        
                    if not continuous:
                        # There's nothing to check.  Just return
                        try:
                            s.send_response(200)
                            s.send_header("Content-type", "application/json")
                            s.end_headers()
                            responsedict = {}
                            responsedict['errcode'] = 0
                            responsedict['errmsg'] = ""
                            
                            jsonstr = json.dumps(responsedict)
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                    else:
                        if newDeauth:
                            # Deauth was started
                            try:
                                s.send_response(200)
                                #s.send_header("Content-type", "text/html")
                                s.send_header("Content-type", "application/json")
                                s.end_headers()
                                responsedict = {}
                                responsedict['errcode'] = 0
                                responsedict['errmsg'] = ""
                                
                                jsonstr = json.dumps(responsedict)
                                s.wfile.write(jsonstr.encode("UTF-8"))
                            except:
                                pass
                        else:
                            # Something went wrong with the start
                            try:
                                s.send_response(400)
                                s.send_header("Content-type", "application/json")
                                s.end_headers()
                                responsedict = {}
                                responsedict['errcode'] = 1
                                responsedict['errmsg'] = "An error occurred starting the deauth process."
                                
                                jsonstr = json.dumps(responsedict)
                                s.wfile.write(jsonstr.encode("UTF-8"))
                            except:
                                pass
                except:
                    try:
                        s.send_response(400)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing json"
                        
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
        elif s.path == '/falcon/startcrack':
            if not hasFalcon:
                try:
                    s.send_response(400)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            else:
                # Extract necessary info for cracking
                try:
                    crackType = jsondata['cracktype']  # This will be wep or wpapsk
                    curInterface = jsondata['interface']
                    channel = jsondata['channel']
                    ssid = jsondata['ssid']
                    apMacAddr=jsondata['apmacaddr']
                    hasClient = jsondata['hasclient']

                    # For now you can only run 1 crack globally due to tmp flie naming.
                    # At some point I'll scale it out
                    if crackType == 'wep':
                        if curInterface in falconWiFiRemoteAgent.WEPCrackList:
                            wepCrack = falconWiFiRemoteAgent.WEPCrackList[curInterface]
                            # Stop one if it was already running
                            wepCrack.stopCrack()
                        else:
                            wepCrack = WEPCrack()
                            falconWiFiRemoteAgent.WEPCrackList[curInterface] = wepCrack
                            
                        wepCrack.cleanupTempFiles()
                        retVal, errMsg = wepCrack.startCrack(curInterface, channel, ssid, apMacAddr, hasClient)
                    else:
                        if curInterface in falconWiFiRemoteAgent.WPAPSKCrackList:
                            wpaPSKCrack = falconWiFiRemoteAgent.WPAPSKCrackList[curInterface]
                            # Stop one if it was already running
                            wpaPSKCrack.stopCrack()
                        else:
                            wpaPSKCrack = WPAPSKCrack()
                            falconWiFiRemoteAgent.WPAPSKCrackList[curInterface] = wpaPSKCrack
                        
                        wpaPSKCrack.cleanupTempFiles()
                        retVal, errMsg = wpaPSKCrack.startCrack(curInterface, channel, ssid, apMacAddr, hasClient)
                    
                    try:
                        s.send_response(200)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        responsedict = {}

                        # For start, retVal is True/False
                        responsedict['errcode'] = retVal
                        responsedict['errmsg'] = errMsg
                        
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                except:
                    try:
                        s.send_response(400)
                        s.send_header("Content-type", "application/json")
                        s.end_headers()
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing json"
                        
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
        else:
            try:
                responsedict = {}
                responsedict['errcode'] = 5
                responsedict['errmsg'] = 'Bad request.'

                s.send_response(400)
                s.send_header("Content-type", "application/json")
                s.end_headers()
                jsonstr = json.dumps(responsedict)
                s.wfile.write(jsonstr.encode("UTF-8"))
            except:
                pass
                
    def isValidPostURL(s):
        allowedfullurls = ['/system/config', 
                                    '/falcon/startcrack', 
                                    '/falcon/deauth', 
                                    '/falcon/stopdeauth', 
                                    '/system/deleterecordings']
                                    
        allowedstarturls=[]
        
        if s.path in allowedfullurls:
            return True
        else:
            for curURL in allowedstarturls:
                if s.path.startswith(curURL):
                    return True
        
        return False
        
    def isValidGetURL(s):
        # Full urls
        allowedfullurls = ['/wireless/interfaces', 
                                    '/wireless/moninterfaces', 
                                    '/falcon/getscanresults',
                                    '/falcon/getalldeauths', 
                                    '/system/getrecordings',
                                    '/bluetooth/present', 
                                   '/bluetooth/scanstart', 
                                  '/bluetooth/scanstop',  
                                  '/bluetooth/scanstatus',  
                                  '/bluetooth/running', 
                                  '/bluetooth/beaconstart', 
                                  '/bluetooth/beaconstop', 
                                  '/bluetooth/discoverystartp', 
                                  '/bluetooth/discoverystarta', 
                                  '/bluetooth/discoverystop', 
                                  '/bluetooth/discoverystatus', 
                                  '/spectrum/scanstart24', 
                                  '/spectrum/scanstart5', 
                                  '/spectrum/scanstop', 
                                  '/spectrum/scanstatus', 
                                  '/spectrum/hackrfstatus', 
                                    '/gps/status']
                                    
        # partials that have more in the URL
        allowedstarturls=['/wireless/networks/', 
                                    '/falcon/startmonmode/', 
                                    '/falcon/stopmonmode/', 
                                    '/falcon/scanrunning/', 
                                    '/falcon/startscan/', 
                                    '/falcon/stopscan/', 
                                    '/falcon/stopalldeauths', 
                                    '/falcon/crackstatuswpapsk', 
                                    '/falcon/crackstatuswep', 
                                    '/falcon/stopcrack', 
                                    '/system/config', 
                                    '/system/startrecord', 
                                    '/system/stoprecord', 
                                    '/system/getrecording']
        
        if s.path in allowedfullurls:
            return True
        else:
            for curURL in allowedstarturls:
                if s.path.startswith(curURL):
                    return True
        
        return False
        
    def sendFile(s, passedfilename):
        # Directory traversal safety check
        dirname, runfilename = os.path.split(os.path.abspath(__file__))
        tmpdirname, filename = os.path.split(passedfilename)
        recordingsDir = dirname + '/recordings'

        fullPath = recordingsDir + '/' + filename
        
        if not os.path.isfile(fullPath):
            s.send_response(400)
            s.send_header("Content-type", "application/json")
            s.end_headers()
            responsedict = {}
            responsedict['errcode'] = 1
            responsedict['errmsg'] = 'File not found.'
            jsonstr = json.dumps(responsedict)
            s.wfile.write(jsonstr.encode("UTF-8"))
            return
        
        try:
            f = open(fullPath, 'rb')
        except:
            s.send_response(400)
            s.send_header("Content-type", "application/json")
            s.end_headers()
            responsedict = {}
            responsedict['errcode'] = 2
            responsedict['errmsg'] = 'Unable to open file.'
            jsonstr = json.dumps(responsedict)
            s.wfile.write(jsonstr.encode("UTF-8"))
            return
            
        fileExtension = filename.split(".")[-1]
        
        if fileExtension in ['txt', 'csv', 'json', 'xml']:
            contentType = 'text/plain'
        elif fileExtension == 'html':
            contentType = 'text/html'
        else:
            contentType = 'application/octet-stream'
            
        s.send_response(200)
        #s.send_header("Content-type", "text/html")
        s.send_header("Content-type", contentType)
        s.end_headers()

        try:
            s.wfile.write(f.read())
        except:
            pass
            
        f.close()
        
        return
        
    def do_GET(s):
        global gpsEngine
        global useMavlink
        global mavlinkGPSThread
        global lockList
        global allowedIPs
        global runningcfg
        global falconWiFiRemoteAgent
        global hasBluetooth
        global hasUbertooth
        global bluetooth
        
        # For RPi LED's, using it during each get request wasn't completely working.  Short transactions like
        # status and interface list were so quick the light would get "confused" and stay off.  So
        # the LED is only used for long calls like scan
        
        if len(s.client_address) == 0:
            # This should have the connecting client IP.  If this isn't at least 1, something is wrong
            return
            
        try:
            # If the pipe gets broken mid-stream it'll throw an exception
            if len(allowedIPs) > 0:
                if s.client_address[0] not in allowedIPs:
                    try:
                        s.send_response(403)
                        s.send_header("Content-type", "text/html")
                        s.end_headers()
                        s.wfile.write("<html><body><p>Connections not authorized from your IP address</p>".encode("utf-8"))
                        s.wfile.write("</body></html>".encode("UTF-8"))
                    except:
                        pass
                    if useRPILeds:
                    # Green will heartbeat when servicing requests. Turn back solid here
                        SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)
                    return

            if not s.isValidGetURL():
                try:
                    s.send_response(404)
                    s.send_header("Content-type", "text/html")
                    s.end_headers()
                    s.wfile.write("<html><body><p>Bad Request</p>".encode("utf-8"))
                    s.wfile.write("</body></html>".encode("UTF-8"))
                except:
                    pass
                if useRPILeds:
                    # Green will heartbeat when servicing requests. Turn back solid here
                    SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)
                return
                
            """Respond to a GET request."""
            if (not s.path.startswith('/system/getrecording/') and (not s.path == ('/bluetooth/scanstatus')) and 
                (not s.path == ('/spectrum/scanstatus'))):
                # In getrecording we may adjust the content type header based on file extension
                # Spectrum we'll gzip
                try:
                    s.send_response(200)
                    s.send_header("Content-type", "application/json")
                    s.end_headers()
                except:
                    pass
                    
            # NOTE: In python 3, string is a bit different.  Examples write strings directly for Python2,
            # In python3 you have to convert it to UTF-8 bytes
            # s.wfile.write("<html><head><title>Sparrow-wifi agent</title></head><body>".encode("utf-8"))

            if s.path == '/wireless/interfaces':
                wirelessInterfaces = WirelessEngine.getInterfaces()
                jsondict={}
                jsondict['interfaces']=wirelessInterfaces
                jsonstr = json.dumps(jsondict)
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif '/wireless/networks/' in s.path:
                # THIS IS THE NORMAL SCAN
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
                        
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
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
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif s.path == '/wireless/moninterfaces':
                wirelessInterfaces = WirelessEngine.getMonitoringModeInterfaces()
                jsondict={}
                jsondict['interfaces']=wirelessInterfaces
                jsonstr = json.dumps(jsondict)
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif s.path == '/system/getrecordings':
                filelist = getRecordingFiles()
                
                responsedict = {}
                responsedict['files'] = filelist
                
                jsonstr = json.dumps(responsedict)
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif s.path.startswith('/system/getrecording/'):
                filename = s.path.replace('/system/getrecording/', '')
                s.sendFile(filename)
            elif s.path == '/bluetooth/present':
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    responsedict['hasbluetooth'] = hasBluetooth
                    if hasBluetooth:
                        responsedict['scanrunning'] = bluetooth.scanRunnning()
                    else:
                        responsedict['scanrunning'] = False
                        
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif s.path.startswith('/bluetooth/beacon'):
                if not hasBluetooth:
                    responsedict = {}
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = 'Bluetooth not supported on this agent'
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    function=s.path.replace('/bluetooth/beacon', '')
                    function = function.replace('/', '')
                    
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    
                    if function=='start':
                        if bluetooth.discoveryRunning():
                            bluetooth.stopDiscovery()

                        retVal = bluetooth.startBeacon()
                        
                        if not retVal:
                            responsedict['errcode'] = 1
                            responsedict['errmsg'] = 'Unable to start beacon.'
                    elif function == 'stop':
                        bluetooth.stopBeacon()
                    else:
                        responsedict['errcode'] = 1
                        responsedict['errmsg'] = 'Unknown command'
                        
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif s.path.startswith('/bluetooth/scan'):
                if not hasBluetooth:
                    responsedict = {}
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = 'Bluetooth not supported on this agent'
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    function=s.path.replace('/bluetooth/scan', '')
                    function = function.replace('/', '')
                    
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    
                    if function=='start':
                        bluetooth.startScanning()
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                    elif function == 'stop':
                        bluetooth.stopScanning()
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                    elif function == 'status':
                        channelData = bluetooth.spectrumToChannels()
                        responsedict['channeldata'] = channelData
                        try:
                            s.send_response(200)
                            s.send_header("Content-type", "application/json")
                            s.send_header("Content-Encoding", "gzip")
                            s.end_headers()
                        except:
                            pass
                        jsonstr = json.dumps(responsedict)
                        gzipBytes = gzipCompress(jsonstr)
                        # s.wfile.write(jsonstr.encode("UTF-8"))
                        try:
                            s.wfile.write(gzipBytes)
                        except:
                            pass
                    else:
                        responsedict['errcode'] = 1
                        responsedict['errmsg'] = 'Unknown command'
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        
            elif s.path.startswith('/bluetooth/discovery'):
                if not hasBluetooth:
                    responsedict = {}
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = 'Bluetooth not supported on this agent'
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    function=s.path.replace('/bluetooth/discovery', '')
                    function = function.replace('/', '')
                    
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    
                    if function=='startp':
                        # Promiscuous with ubertooth
                        if hasUbertooth:
                            bluetooth.startDiscovery(True)
                        else:
                            responsedict['errcode'] = 2
                            responsedict['errmsg'] = 'Ubertooth not supported on this agent'
                    elif function == 'starta':
                        # Normal with Bluetooth
                        bluetooth.startDiscovery(False)
                    elif function == 'stop':
                        bluetooth.stopDiscovery()
                    elif function == 'status':
                            # have to get the GPS:
                        gpsCoord = GPSStatus()
                        if useMavlink:
                            gpsCoord.gpsInstalled = True
                            gpsCoord.gpsRunning = True
                            gpsCoord.isValid = mavlinkGPSThread.synchronized
                            gpsCoord.latitude = mavlinkGPSThread.latitude
                            gpsCoord.longitude = mavlinkGPSThread.longitude
                            gpsCoord.altitude = mavlinkGPSThread.altitude
                            gpsCoord.speed = mavlinkGPSThread.vehicle.getAirSpeed()
                        elif gpsEngine.gpsValid():
                            gpsCoord.copy(gpsEngine.lastCoord)
                            
                        # errcode, devices = bluetooth.getDiscoveredDevices()
                        bluetooth.updateDeviceList()
                        
                        bluetooth.deviceLock.acquire()
                        devdict = []
                        now = datetime.datetime.now()
                        for curKey in bluetooth.devices.keys():
                            curDevice = bluetooth.devices[curKey]
                            elapsedTime =  now - curDevice.lastSeen
                            
                            # This is a little bit of a hack for the BlueHydra side since it can take a while to see devices or have
                            # them show up in the db.  For LE discovery scans this will always be pretty quick.
                            if elapsedTime.total_seconds() < 120:
                                curDevice.gps.copy(gpsCoord)
                                if curDevice.rssi >= curDevice.strongestRssi:
                                    curDevice.strongestRssi = curDevice.rssi
                                    curDevice.strongestgps.copy(gpsCoord)
                                
                            entryDict = curDevice.toJsondict()
                            devdict.append(entryDict)
                            
                        bluetooth.deviceLock.release()
                        responsedict['devices'] = devdict
                    else:
                        responsedict['errcode'] = 1
                        responsedict['errmsg'] = 'Unknown command'
                        
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif s.path == '/bluetooth/running':
                if not hasBluetooth:
                    responsedict = {}
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = 'Bluetooth not supported on this agent'
                    responsedict['hasbluetooth'] = hasBluetooth
                    responsedict['hasubertooth'] = hasUbertooth
                    responsedict['spectrumscanrunning'] = False
                    responsedict['discoveryscanrunning'] = False
                    responsedict['beaconrunning'] = False
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''

                    responsedict['hasbluetooth'] = hasBluetooth
                    responsedict['hasubertooth'] = hasUbertooth
                    responsedict['spectrumscanrunning'] = bluetooth.scanRunning()
                    responsedict['discoveryscanrunning'] = bluetooth.discoveryRunning()
                    responsedict['beaconrunning'] = bluetooth.beaconRunning()
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif s.path == '/spectrum/hackrfstatus':
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    responsedict['hashackrf'] = hackrf.hasHackrf
                    responsedict['scan24running'] = hackrf.scanRunning24()
                    responsedict['scan5running'] = hackrf.scanRunning5()
                        
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif s.path.startswith('/spectrum/scan'):
                if not hackrf.hasHackrf:
                    responsedict = {}
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = 'HackRF is not supported on this agent'
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    function=s.path.replace('/spectrum/scan', '')
                    function = function.replace('/', '')
                    
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    
                    if function=='start24':
                        hackrf.startScanning24()
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    elif function == 'start5':
                        hackrf.startScanning5()
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    elif function == 'stop':
                        hackrf.stopScanning()
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    elif function == 'status':
                        if hackrf.scanRunning24():
                            channelData = hackrf.spectrum24ToChannels()
                            responsedict['scanrunning'] = hackrf.scanRunning24()
                        elif hackrf.scanRunning5():
                            channelData = hackrf.spectrum5ToChannels()
                            responsedict['scanrunning'] = hackrf.scanRunning24()
                        else:
                            channelData = {}  # Shouldn't be here but just in case.
                            responsedict['scanrunning'] = False
                            
                        responsedict['channeldata'] = channelData

                        try:
                            s.send_response(200)
                            s.send_header("Content-type", "application/json")
                            s.send_header("Content-Encoding", "gzip")
                            s.end_headers()
                            jsonstr = json.dumps(responsedict)
                            gzipBytes = gzipCompress(jsonstr)
                            # s.wfile.write(jsonstr.encode("UTF-8"))
                            s.wfile.write(gzipBytes)
                        except:
                            pass
                    else:
                        responsedict['errcode'] = 1
                        responsedict['errmsg'] = 'Unknown command'
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
            elif s.path == '/system/config':
                cfgSettings = AgentConfigSettings()
                cfgSettings.fromConfigFile('sparrowwifiagent.cfg')
                responsedict = {}
                responsedict['startup'] = cfgSettings.toJsondict()
                
                if recordThread:
                    runningcfg.recordRunning = True
                    runningcfg.recordInterface = recordThread.interface
                    
                responsedict['running'] = runningcfg.toJsondict()
                
                jsonstr = json.dumps(responsedict)
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif s.path.startswith('/system/startrecord'):
                recordinterface = s.path.replace('/system/startrecord/', '')
                
                # Check that the specified interface is valid:
                interfaces = WirelessEngine.getInterfaces()
                
                if recordinterface in interfaces:
                    startRecord(recordinterface)
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ''
                    jsonstr = json.dumps(responsedict)
                else:
                    responsedict = {}
                    responsedict['errcode'] = 1
                    responsedict['errmsg'] = 'The requested interface was not found on the system.'
                    jsonstr = json.dumps(responsedict)
                    
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif s.path == '/system/stoprecord':
                stopRecord()
                responsedict = {}
                responsedict['errcode'] = 0
                responsedict['errmsg'] = ''
                jsonstr = json.dumps(responsedict)
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
            elif '/falcon/startmonmode' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/startmonmode/', '')
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
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                        
                    retVal, errMsg = falconWiFiRemoteAgent.startMonitoringInterface(fieldValue)
                    responsedict = {}
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/stopmonmode' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/stopmonmode/', '')
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
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                        
                    retVal, errMsg = falconWiFiRemoteAgent.stopMonitoringInterface(fieldValue)
                    responsedict = {}
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/scanrunning' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/scanrunning/', '')
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
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                        
                    scanrunning = falconWiFiRemoteAgent.isScanRunning(fieldValue)
                    
                    if scanrunning:
                        retVal = 0
                        errMsg = "scan for " + fieldValue + " is running"
                    else:
                        retVal = 1
                        errMsg = "scan for " + fieldValue + " is not running"
                        
                    responsedict = {}
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/startscan' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/startscan/', '')
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
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                        
                    scanProc = falconWiFiRemoteAgent.startCapture(fieldValue)
                    
                    if scanProc is not None:
                        retVal = 0
                        errMsg = ""
                    else:
                        retVal = -1
                        errMsg = "Unable to start scanning process."
                        
                    responsedict = {}
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/stopscan' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/stopscan/', '')
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
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                        
                    retVal = falconWiFiRemoteAgent.stopCapture(fieldValue)
                    
                    if retVal == 0:
                        errMsg = ""
                    else:
                        errMsg = "Unable to stop scanning process."
                        
                    responsedict = {}
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/stopcrack' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/stopcrack/', '')
                    # Sanitize command-line input here:
                    p = re.compile('^([0-9a-zA-Z]+)')
                    try:
                        curInterface = p.search(inputstr).group(1)
                    except:
                        curInterface = ""
                        
                    if len(curInterface) == 0:
                        if useRPILeds:
                            # Green will heartbeat when servicing requests. Turn back solid here
                            SparrowRPi.greenLED(LIGHT_STATE_ON)
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                    
                    try:
                        if curInterface in falconWiFiRemoteAgent.WEPCrackList:
                            falconWiFiRemoteAgent.WEPCrackList[curInterface].stopCrack()
                            falconWiFiRemoteAgent.WEPCrackList[curInterface].cleanupTempFiles()
                            del falconWiFiRemoteAgent.WEPCrackList[curInterface]
                            
                        if curInterface in falconWiFiRemoteAgent.WPAPSKCrackList:
                            falconWiFiRemoteAgent.WPAPSKCrackList[curInterface].stopCrack()
                            falconWiFiRemoteAgent.WPAPSKCrackList[curInterface].cleanupTempFiles()
                            del falconWiFiRemoteAgent.WPAPSKCrackList[curInterface]
                    except:
                        pass
                        
                    retVal = 0
                    errMsg = ""
                    
                    responsedict = {}
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/crackstatus' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    if 'crackstatuswep' in s.path:
                        type='wep'
                    else:
                        type = 'wpapsk'
                        
                    inputstr = s.path.replace('/falcon/crackstatus'+type+'/', '')
                    # Sanitize command-line input here:
                    p = re.compile('^([0-9a-zA-Z]+)')
                    try:
                        curInterface = p.search(inputstr).group(1)
                    except:
                        curInterface = ""
                        
                    if len(curInterface) == 0:
                        if useRPILeds:
                            # Green will heartbeat when servicing requests. Turn back solid here
                            SparrowRPi.greenLED(LIGHT_STATE_ON)
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + curInterface
                        jsonstr = json.dumps(responsedict)
                        try:
                            s.wfile.write(jsonstr.encode("UTF-8"))
                        except:
                            pass
                        return
                    
                    responsedict = {}
                    retVal = -1
                    errMsg = "Unable to find running crack."
                                    
                    try:
                        if type == 'wep':
                            if curInterface in falconWiFiRemoteAgent.WEPCrackList:
                                wepCrack = falconWiFiRemoteAgent.WEPCrackList[curInterface]
                                retVal = 0
                                errMsg = ""
                                responsedict['isrunning'] = wepCrack.isRunning()
                                responsedict['ivcount'] = wepCrack.getIVCount()
                                responsedict['ssid'] = wepCrack.SSID
                                responsedict['crackedpasswords'] = wepCrack.getCrackedPasswords()
                        else:
                            if curInterface in falconWiFiRemoteAgent.WPAPSKCrackList:
                                wpaPSKCrack = falconWiFiRemoteAgent.WPAPSKCrackList[curInterface]
                                retVal = 0
                                errMsg = ""
                                responsedict['isrunning'] = wpaPSKCrack.isRunning()
                                hasHandshake = wpaPSKCrack.hasHandshake()
                                responsedict['hashandshake'] = hasHandshake
                                
                                if hasHandshake:
                                    # For WPAPSK, lets copy the capture file to our recording directory for recovery
                                    dirname, filename = os.path.split(os.path.abspath(__file__))
                                    fullpath, filename=wpaPSKCrack.copyCaptureFile(dirname + '/recordings')
                                    responsedict['capturefile'] = filename
                                else:
                                    responsedict['capturefile'] = ""
                    except:
                        pass
                        
                    responsedict['errcode'] = retVal
                    responsedict['errmsg'] = errMsg
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif s.path == '/falcon/getscanresults':
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    if useMavlink:
                        gpsCoord = GPSStatus()
                        gpsCoord.gpsInstalled = True
                        gpsCoord.gpsRunning = True
                        gpsCoord.isValid = mavlinkGPSThread.synchronized
                        gpsCoord.latitude = mavlinkGPSThread.latitude
                        gpsCoord.longitude = mavlinkGPSThread.longitude
                        gpsCoord.altitude = mavlinkGPSThread.altitude
                        gpsCoord.speed = mavlinkGPSThread.vehicle.getAirSpeed()
                        retCode, errString, jsonstr=falconWiFiRemoteAgent.getNetworksAsJson(gpsCoord)
                    elif gpsEngine.gpsValid():
                        retCode, errString, jsonstr=falconWiFiRemoteAgent.getNetworksAsJson(gpsEngine.lastCoord)
                        if useRPILeds:
                            SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)
                    else:
                        retCode, errString, jsonstr=falconWiFiRemoteAgent.getNetworksAsJson(None)
                        if useRPILeds:
                            # This just signals that the GPS isn't synced
                            SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_HEARTBEAT)
                    
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/stopalldeauths' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    inputstr = s.path.replace('/falcon/stopalldeauths/', '')
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
                            
                        responsedict = {}
                        responsedict['errcode'] = 5
                        responsedict['errmsg'] = "Error parsing interface.  Identified interface: " + fieldValue
                        jsonstr = json.dumps(responsedict)
                        s.wfile.write(jsonstr.encode("UTF-8"))
                        return
                        
                    falconWiFiRemoteAgent.stopAllDeauths(fieldValue)
                    responsedict = {}
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ""
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            elif '/falcon/getalldeauths' in s.path:
                if not hasFalcon:
                    responsedict = {}
                    responsedict['errcode'] = 5
                    responsedict['errmsg'] = "Unknown request: " + s.path
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
                else:
                    responsedict = falconWiFiRemoteAgent.getAllDeauthsAsJsonDict()
                    # Add in successful response
                    responsedict['errcode'] = 0
                    responsedict['errmsg'] = ""
                    
                    jsonstr = json.dumps(responsedict)
                    try:
                        s.wfile.write(jsonstr.encode("UTF-8"))
                    except:
                        pass
            else:
                # Catch-all.  Should never be here
                responsedict = {}
                responsedict['errcode'] = 5
                responsedict['errmsg'] = "Unknown request: " + s.path
                
                jsonstr = json.dumps(responsedict)
                try:
                    s.wfile.write(jsonstr.encode("UTF-8"))
                except:
                    pass
        except:
            pass
            
        if useRPILeds:
            # Green will heartbeat when servicing requests. Turn back solid here
            SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_ON)

# ----------------- Bluetooth check -----------------------------
def checkForBluetooth():
    global hasBluetooth
    global hasUbertooth
    global bluetooth
    
    numBtAdapters = len(SparrowBluetooth.getBluetoothInterfaces())
    if numBtAdapters > 0:
        hasBluetooth = True
    
    if SparrowBluetooth.getNumUbertoothDevices() > 0:
        #SparrowBluetooth.ubertoothStopSpecan()
        errcode, errmsg = SparrowBluetooth.hasUbertoothTools()
        # errcode, errmsg = SparrowBluetooth.ubertoothOnline()
        if errcode == 0:
            hasUbertooth = True
    
    bluetooth = SparrowBluetooth()
    
    if hasBluetooth:
        print("Found bluetooth hardware.  Bluetooth capabilities enabled.")
    else:
        print("Bluetooth hardware not found.  Bluetooth capabilities disabled.")
        
    if hasUbertooth:
        print("Found ubertooth hardware and software.  Ubertooth capabilities enabled.")
    else:
        print("Ubertooth hardware and/or software not found.  Ubertooth capabilities disabled.")

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

    # Code to add paths
    dirname, filename = os.path.split(os.path.abspath(__file__))
    
    if dirname not in sys.path:
        sys.path.insert(0, dirname)

    # Check for Falcon offensive plugin
    pluginsdir = dirname+'/plugins'
    if  os.path.exists(pluginsdir):
        if pluginsdir not in sys.path:
            sys.path.insert(0,pluginsdir)
        if  os.path.isfile(pluginsdir + '/falconwifi.py'):
            from falconwifi import FalconWiFiRemoteAgent, WPAPSKCrack, WEPCrack
            hasFalcon = True
            falconWiFiRemoteAgent = FalconWiFiRemoteAgent()
            if not falconWiFiRemoteAgent.toolsInstalled():
                print("ERROR: aircrack suite of tools does not appear to be installed.  Please install it.")
                exit(4)

    checkForBluetooth()
    
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
    
    if len(runningcfg.mavlinkGPS) > 0 and hasDroneKit:
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
        
    if hasDroneKit and useMavlink and vehicle:
        vehicle.close()

    stopAnnounceThread()
    
    if runningcfg.useRPiLEDs:
        SparrowRPi.greenLED(SparrowRPi.LIGHT_STATE_OFF)
        SparrowRPi.redLED(SparrowRPi.LIGHT_STATE_ON)

    #for curKey in lockList.keys():
    #    curLock = lockList[curKey]
    #    try:
    #        curLock.release()
    #    except:
    #        pass

    # os._exit(0)
    exit(0)
