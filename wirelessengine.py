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
import subprocess
import re
import datetime
from dateutil import parser
import json
import copy
from sparrowgps import SparrowGPS

# ------------------  Global functions ------------------------------
def stringtobool(instr):
    if (instr == 'True' or instr == 'true'):
        return True
    else:
        return False
        

# ------------------  Classes ------------------------------------

class WirelessNetwork(object):
    ERR_NETDOWN = 156
    ERR_OPNOTSUPPORTED = 161
    ERR_DEVICEBUSY = 240
    ERR_OPNOTPERMITTED = 255
    
    def __init__(self):
        self.macAddr = ""
        self.ssid = ""
        self.mode = "" # master, managed, monitor, etc.
        self.security = "Open" # on or off
        self.privacy = "None" # group cipher
        self.cipher = ""  # pairwise cipher
        self.channel = 0   # Channel #
        self.frequency = 0
        self.signal = -1000 # dBm
        self.bandwidth = 20 # Default to 20.  we'll bump it up as we see params.  max BW in any protocol 20 or 40 or 80 or 160 MHz
        self.secondaryChannel = 0  # used for 40+ MHz
        self.thirdChannel = 0  # used for 80+ MHz channels
        self.secondaryChannelLocation = ''  # above/below
        now=datetime.datetime.now()
        self.firstSeen = now
        self.lastSeen = now
        self.gps = SparrowGPS()
        # Used for tracking in network table
        self.foundInList = False
        
        super().__init__()

    def __str__(self):
        retVal = ""
        
        retVal += "MAC Address: " + self.macAddr + "\n"
        retVal += "SSID: " + self.ssid + "\n"
        retVal += "Mode: " + self.mode + "\n"
        retVal += "Security: " + self.security + "\n"
        retVal += "Privacy: " + self.privacy + "\n"
        retVal += "Cipher: " + self.cipher + "\n"
        retVal += "Frequency: " + str(self.frequency) + " MHz\n"
        retVal += "Channel: " + str(self.channel) + "\n"
        retVal += "Secondary Channel: " + str(self.secondaryChannel) + "\n"
        retVal += "Secondary Channel Location: " + self.secondaryChannelLocation + "\n"
        retVal += "Third Channel: " + str(self.thirdChannel) + "\n"
        retVal += "Signal: " + str(self.signal) + " dBm\n"
        retVal += "Bandwidth: " + str(self.bandwidth) + "\n"
        retVal += "First Seen: " + str(self.firstSeen) + "\n"
        retVal += "Last Seen: " + str(self.lastSeen) + "\n"
        retVal += str(self.gps)

        return retVal

    def defatul(self, obj):
        pass
        
    def copy(self):
        return copy.deepcopy(self)
        
    def __eq__(self, obj):
        # This is equivance....   ==
        if not isinstance(obj, WirelessNetwork):
           return False
          
        if self.macAddr != obj.macAddr:
            return False
        if self.ssid != obj.ssid:
            return False

        if self.mode != obj.mode:
            return False
            
        if self.security != obj.security:
            return False
            
        if self.channel != obj.channel:
            return False
            
        return True

    def createFromJsonDict(jsondict):
        retVal = WirelessNetwork()
        retVal.fromJsondict(jsondict)
        return retVal
        
    def fromJsondict(self, dictjson):
        try:
            self.macAddr = dictjson['macAddr']
            self.ssid = dictjson['ssid']
            self.mode = dictjson['mode']
            self.security = dictjson['security']
            self.privacy = dictjson['privacy']
            self.cipher = dictjson['cipher']
            self.frequency = int(dictjson['frequency'])
            self.channel = int(dictjson['channel'])
            self.secondaryChannel = int(dictjson['secondaryChannel'])
            self.secondaryChannelLocation = dictjson['secondaryChannelLocation']
            self.thirdChannel = int(dictjson['thirdChannel'])
            self.signal = int(dictjson['signal'])
            self.bandwidth = int(dictjson['bandwidth'])
            self.firstSeen = parser.parse(dictjson['firstseen'])
            self.lastSeen = parser.parse(dictjson['lastseen'])
            self.gps.latitude = float(dictjson['lat'])
            self.gps.longitude = float(dictjson['lon'])
            self.gps.altitude = float(dictjson['alt'])
            self.gps.speed = float(dictjson['speed'])
            self.gps.isValid = stringtobool(dictjson['gpsvalid'])
        except:
            pass
            
    def fromJson(self, jsonstr):
        dictjson = json.loads(jsonstr)
        self.fromJsondict(dictjson)
            
    def toJsondict(self):
        dictjson = {}
        dictjson['macAddr'] = self.macAddr
        dictjson['ssid'] = self.ssid
        dictjson['mode'] = self.mode
        dictjson['security'] = self.security
        dictjson['privacy'] = self.privacy
        dictjson['cipher'] = self.cipher
        dictjson['frequency'] = self.frequency
        dictjson['channel'] = self.channel
        dictjson['secondaryChannel'] = self.secondaryChannel
        dictjson['secondaryChannelLocation'] = self.secondaryChannelLocation
        dictjson['thirdChannel'] = self.thirdChannel
        dictjson['signal'] = self.signal
        dictjson['bandwidth'] = self.bandwidth
        dictjson['firstseen'] = str(self.firstSeen)
        dictjson['lastseen'] = str(self.lastSeen)
        dictjson['lat'] = str(self.gps.latitude)
        dictjson['lon'] = str(self.gps.longitude)
        dictjson['alt'] = str(self.gps.altitude)
        dictjson['speed'] = str(self.gps.speed)
        dictjson['gpsvalid'] = str(self.gps.isValid)
        
        return dictjson
        
    def toJson(self):
        dictjson = self.tojsondict()
        return json.dumps(dictjson)
        
    def getChannelString(self):
        retVal = self.channel
        
        if self.bandwidth == 40 and self.secondaryChannel > 0:
            retVal = str(self.channel) + '+' + str(self.secondaryChannel)
            
        return retVal
        
    def getKey(self):
        return self.macAddr + self.ssid+str(self.channel)
        
class WirelessEngine(object):
    def __init__(self):
        super().__init__()

    def getInterfaces(printResults=False):
        result = subprocess.run(['iwconfig'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        wirelessResult = result.stdout.decode('ASCII')
        p = re.compile('^(.*?) IEEE', re.MULTILINE)
        tmpInterfaces = p.findall(wirelessResult)
        
        retVal = []
        
        if (len(tmpInterfaces) > 0):
            for curInterface in tmpInterfaces:
                tmpStr=curInterface.replace(' ','')
                retVal.append(tmpStr)
                # debug
                if (printResults):
                    print(tmpStr)
        else:
            # debug
            if (printResults):
                print("Error: No wireless interfaces found.")

        return retVal

    def getNetworksAsJson(interfaceName, gpsData):
        retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(interfaceName)
        retVal = {}
        retVal['errCode'] = retCode
        retVal['errString'] = errString
        
        netList = []
        
        for curKey in wirelessNetworks.keys():
            curNet = wirelessNetworks[curKey]
            if gpsData is not None:
                curNet.gps = gpsData
            netList.append(curNet.toJsondict())
            
        gpsdict = {}
        
        if (gpsData is None):
            gpsloc = SparrowGPS()
        else:
            gpsloc = gpsData
        
        gpsdict['latitude'] = gpsloc.latitude
        gpsdict['longitude'] = gpsloc.longitude
        gpsdict['altitude'] = gpsloc.altitude
        gpsdict['speed'] = gpsloc.speed
        retVal['gps'] = gpsdict
        
        retVal['networks'] = netList
        
        jsonstr = json.dumps(retVal)
        
        return retCode, errString, jsonstr
        
    def scanForNetworks(interfaceName, printResults=False):
        result = subprocess.run(['iw', 'dev', interfaceName, 'scan'], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        retCode = result.returncode
        errString = ""
        wirelessResult = result.stdout.decode('ASCII')
        
        # debug
        if (printResults):
            print('Return Code ' + str(retCode))
            print(wirelessResult)
        
        wirelessNetworks = {}
        
        if (retCode == 0):
            wirelessNetworks = WirelessEngine.parseIWoutput(wirelessResult)
        else:
            # errCodes:
            # 156 = Network is down (i.e. switch may be turned off)
            # 240  = command failed: Device or resource busy (-16)
            errString = wirelessResult.replace("\n", "")
            if (retCode == WirelessNetwork.ERR_NETDOWN):
                errString = 'Interface appears down'
            elif (retCode == WirelessNetwork.ERR_DEVICEBUSY):
                errString = 'Device is busy'
            elif (retCode == WirelessNetwork.ERR_OPNOTPERMITTED):
                errString = errString + '. Did you run as root?'
            
        return retCode, errString, wirelessNetworks
        
    def parseIWoutput(iwOutput):
        
        retVal = {}
        curNetwork = None
        now=datetime.datetime.now()
        
        for curLine in iwOutput.splitlines():
            p = re.compile('^BSS (.*?)\(')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                # New object
                if curNetwork is not None:
                    # Store first
                    if curNetwork.channel > 0:
                        # I did see incomplete output from iw where not all the data was there
                        retVal[curNetwork.getKey()] = curNetwork

                # Create a new netowrk.  BSSID will be the header for each network
                curNetwork = WirelessNetwork()
                curNetwork.lastSeen = now
                curNetwork.firstSeen = now
                curNetwork.macAddr = fieldValue
                continue
            
            if curNetwork is None:
                # If we don't have a network object yet, then we haven't
                # seen a BSSID so just keep going through the lines.
                continue
        
            p = re.compile('^.+?SSID: +(.*)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.ssid = fieldValue
                
            p = re.compile('^	capability:.*(ESS)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.mode = "AP"
                continue #Found the item
                
            p = re.compile('^	capability:.*(IBSS)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.mode = "Ad Hoc"
                continue #Found the item
                
            p = re.compile('^	capability:.*(IBSS)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.mode = "Ad Hoc"
                continue #Found the item
                
            p = re.compile('.*?Authentication suites: *(.*)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.security = fieldValue
                continue #Found the item
                
            p = re.compile('.*?Group cipher: *(.*)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.privacy = fieldValue
                continue #Found the item
                
            p = re.compile('.*?Pairwise ciphers: *(.*)')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.cipher = fieldValue
                continue #Found the item
                
            p = re.compile('^.*?primary channel: +([0-9]+).*')
            try:
                fieldValue = int(p.search(curLine).group(1))
            except:
                fieldValue = 0
                
            if (fieldValue > 0):
                curNetwork.channel = fieldValue
                continue #Found the item
                
            p = re.compile('^.*?freq:.*?([0-9]+).*')
            try:
                fieldValue = int(p.search(curLine).group(1))
            except:
                fieldValue = 0
                
            if (fieldValue > 0):
                curNetwork.frequency = fieldValue
                continue #Found the item
                
            p = re.compile('^.*?signal:.*?([\-0-9]+).*?dBm')
            try:
                fieldValue = int(p.search(curLine).group(1))
            except:
                fieldValue = 10
                
            # This test is different.  dBm is negative so can't test > 0.  10dBm is really high so lets use that
            if (fieldValue < 10):
                curNetwork.signal = fieldValue
                curNetwork.minSignal = fieldValue
                curNetwork.maxSignal = fieldValue
                continue #Found the item
                
            p = re.compile('.*?HT20/HT40.*')
            try:
                # This is just a presence check using group(0).
                fieldValue = p.search(curLine).group(0)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                if (curNetwork.bandwidth == 20):
                    curNetwork.bandwidth = 40
                continue #Found the item
                
            p = re.compile('.*?\\* channel width:.*?([0-9]+) MHz.*')
            try:
                fieldValue = int(p.search(curLine).group(1))
            except:
                fieldValue = 0
                
            if (fieldValue > 0):
                curNetwork.bandwidth = fieldValue
                continue #Found the item
                
            p = re.compile('^.*?secondary channel offset: *([^ \\t]+).*')
            try:
                fieldValue = p.search(curLine).group(1)
            except:
                fieldValue = ""
                
            if (len(fieldValue) > 0):
                curNetwork.secondaryChannelLocation = fieldValue
                if (fieldValue == 'above'):
                    curNetwork.secondaryChannel = curNetwork.channel + 4
                elif (fieldValue == 'below'):
                    curNetwork.secondaryChannel = curNetwork.channel - 4
                # else it'll say 'no secondary'
                    
                continue #Found the item
                
            p = re.compile('^.*?center freq segment 1: *([^ \\t]+).*')
            try:
                fieldValue = int(p.search(curLine).group(1))
            except:
                fieldValue = 0
                
            if (fieldValue > 0):
                curNetwork.thirdChannel = fieldValue
                    
                continue #Found the item
                
        # #### End loop ######
        
        # Add the last network
        if curNetwork is not None:
            if curNetwork.channel > 0:
                # I did see incomplete output from iw where not all the data was there
                retVal[curNetwork.getKey()] = curNetwork
        
        return retVal
        
if __name__ == '__main__':
    if os.geteuid() != 0:
        print("ERROR: You need to have root privileges to run this script.  Please try again, this time using 'sudo'. Exiting.\n")
        exit(2)
    # for debugging
    
    # change this interface name to test it.
    wirelessInterfaces = WirelessEngine.getInterfaces()
    
    if len(wirelessInterfaces) == 0:
        print("ERROR: Unable to find wireless interface.\n")
        exit(1)
        
    winterface = wirelessInterfaces[0]
    print('Scanning for wireless networks on ' + winterface + '...')
    
    # Testing to/from Json
    # convert to Json
    retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(winterface, None)
    # Convert back
    j=json.loads(jsonstr)
    
    # print results
    print('Error Code: ' + str(j['errCode']) + '\n')
    
    if j['errCode'] == 0:
        for curNetDict in j['networks']:
            newNet = WirelessNetwork.createFromJsonDict(curNetDict)
            print(newNet)
            
    else:    
        print('Error String: ' + j['errString'] + '\n')
    
    print('Done.\n')
