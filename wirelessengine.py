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
from time import sleep
from sparrowgps import SparrowGPS
from sparrowcommon import stringtobool

# ------------------  Global channel to frequency definitions ------------------------------
channelToFreq = {}
channelToFreq['1'] = '2412'
channelToFreq['2'] = '2417'
channelToFreq['3'] = '2422'
channelToFreq['4'] = '2427'
channelToFreq['5'] = '2432'
channelToFreq['6'] = '2437'
channelToFreq['7'] = '2442'
channelToFreq['8'] = '2447'
channelToFreq['9'] = '2452'
channelToFreq['10'] = '2457'
channelToFreq['11'] = '2462'
channelToFreq['12'] = '2467'
channelToFreq['13'] = '2472'
channelToFreq['14'] = '2484'

channelToFreq['16'] = '5080'
channelToFreq['34'] = '5170'
channelToFreq['36'] = '5180'
channelToFreq['38'] = '5190'
channelToFreq['40'] = '5200'
channelToFreq['42'] = '5210'
channelToFreq['44'] = '5220'
channelToFreq['46'] = '5230'
channelToFreq['48'] = '5240'
channelToFreq['50'] = '5250'
channelToFreq['52'] = '5260'
channelToFreq['54'] = '5270'
channelToFreq['56'] = '5280'
channelToFreq['58'] = '5290'
channelToFreq['60'] = '5300'
channelToFreq['62'] = '5310'
channelToFreq['64'] = '5320'
channelToFreq['100'] = '5500'
channelToFreq['102'] = '5510'
channelToFreq['104'] = '5520'
channelToFreq['106'] = '5530'
channelToFreq['108'] = '5540'
channelToFreq['110'] = '5550'
channelToFreq['112'] = '5560'
channelToFreq['114'] = '5570'
channelToFreq['116'] = '5580'
channelToFreq['118'] = '5590'
channelToFreq['120'] = '5600'
channelToFreq['122'] = '5610'
channelToFreq['124'] = '5620'
channelToFreq['126'] = '5630'
channelToFreq['128'] = '5640'
channelToFreq['132'] = '5660'
channelToFreq['134'] = '5670'
channelToFreq['136'] = '5680'
channelToFreq['138'] = '5690'
channelToFreq['140'] = '5700'
channelToFreq['142'] = '5710'
channelToFreq['144'] = '5720'
channelToFreq['149'] = '5745'
channelToFreq['151'] = '5755'
channelToFreq['153'] = '5765'
channelToFreq['155'] = '5775'
channelToFreq['157'] = '5785'
channelToFreq['159'] = '5795'
channelToFreq['161'] = '5805'
channelToFreq['165'] = '5825'
channelToFreq['169'] = '5845'
channelToFreq['173'] = '5865'
channelToFreq['183'] = '4915'
channelToFreq['184'] = '4920'
channelToFreq['185'] = '4925'
channelToFreq['187'] = '4935'
channelToFreq['188'] = '4940'
channelToFreq['189'] = '4945'
channelToFreq['192'] = '4960'
channelToFreq['196'] = '4980'
   
# ------------------  WirelessNetwork class ------------------------------------
class WirelessClient(object):
    def __init__(self):
        self.macAddr = ""
        self.apMacAddr = ""
        self.ssid = ""
        self.channel = 0
        self.signal = -1000 # dBm
        now=datetime.datetime.now()
        self.firstSeen = now
        self.lastSeen = now

        self.gps = SparrowGPS()
        self.strongestsignal = self.signal
        self.strongestgps = SparrowGPS()

        self.probedSSIDs = []
        # Used for tracking in network table
        self.foundInList = False
        
    def __str__(self):
        retVal = ""
        
        retVal += "MAC Address: " + self.macAddr + "\n"
        retVal += "Associated Access Point Mac Address: " + self.apMacAddr + "\n"
        retVal += "SSID: " + self.ssid + "\n"
        retVal += "Channel: " + str(self.channel) + "\n"
        retVal += "Signal: " + str(self.signal) + " dBm\n"
        retVal += "Strongest Signal: " + str(self.strongestsignal) + " dBm\n"
        retVal += "First Seen: " + str(self.firstSeen) + "\n"
        retVal += "Last Seen: " + str(self.lastSeen) + "\n"
        retVal += "Probed SSIDs:"
        
        if (len(self.probedSSIDs) > 0):
            for curSSID in self.probedSSIDs:
                retVal += " " + curSSID
                
            retVal += "\n"
        else:
            retVal += " No probes observed\n"
            
        retVal += "Last GPS:\n"
        retVal += str(self.gps)
        retVal += "Strongest GPS:\n"
        retVal += str(self.strongestgps)
            
        return retVal
        
    def copy(self):
        return copy.deepcopy(self)
        
    def __eq__(self, obj):
        # This is equivance....   ==
        if not isinstance(obj, WirelessClient):
           return False
          
        if self.macAddr != obj.macAddr:
            return False
            
        if self.apMacAddr != obj.apMacAddr:
            return False

        return True

    def __ne__(self, other):
            return not self.__eq__(other)
        
    def getKey(self):
        return self.macAddr
        
    def associated(self):
        if len(self.apMacAddr) == 0 or (self.apMacAddr == "(not associated)"):
            return False
            
        return True
        
    def createFromJsonDict(jsondict):
        retVal = WirelessClient()
        retVal.fromJsondict(jsondict)
        return retVal
        
    def fromJsondict(self, dictjson):
        # Note: if the json dictionary isn't correct, this will naturally throw an exception that may
        # need to be caught for error detection
        self.macAddr = dictjson['macAddr']
        self.apMacAddr = dictjson['apMacAddr']
        self.ssid = dictjson['ssid']
        self.channel = int(dictjson['channel'])
        
        self.signal = int(dictjson['signal'])
        self.strongestsignal = int(dictjson['strongestsignal'])

        self.firstSeen = parser.parse(dictjson['firstseen'])
        self.lastSeen = parser.parse(dictjson['lastseen'])

        self.gps.latitude = float(dictjson['lat'])
        self.gps.longitude = float(dictjson['lon'])
        self.gps.altitude = float(dictjson['alt'])
        self.gps.speed = float(dictjson['speed'])
        self.gps.isValid = stringtobool(dictjson['gpsvalid'])
        
        self.strongestgps.latitude = float(dictjson['strongestlat'])
        self.strongestgps.longitude = float(dictjson['strongestlon'])
        self.strongestgps.altitude = float(dictjson['strongestalt'])
        self.strongestgps.speed = float(dictjson['strongestspeed'])
        self.strongestgps.isValid = stringtobool(dictjson['strongestgpsvalid'])
        
        self.probedSSIDs = dictjson['probedssids']
            
    def fromJson(self, jsonstr):
        dictjson = json.loads(jsonstr)
        self.fromJsondict(dictjson)
            
    def toJson(self):
        dictjson = self.toJsondict()
        return json.dumps(dictjson)
        
    def toJsondict(self):
        dictjson = {}
        dictjson['type'] = 'wifi-client'
        dictjson['macAddr'] = self.macAddr
        dictjson['apMacAddr'] = self.apMacAddr
        dictjson['ssid'] = self.ssid
        dictjson['channel'] = self.channel
        dictjson['signal'] = self.signal
        dictjson['firstseen'] = str(self.firstSeen)
        dictjson['lastseen'] = str(self.lastSeen)
        dictjson['lat'] = str(self.gps.latitude)
        dictjson['lon'] = str(self.gps.longitude)
        dictjson['alt'] = str(self.gps.altitude)
        dictjson['speed'] = str(self.gps.speed)
        dictjson['gpsvalid'] = str(self.gps.isValid)
        
        dictjson['strongestsignal'] = self.strongestsignal
        dictjson['strongestlat'] = str(self.strongestgps.latitude)
        dictjson['strongestlon'] = str(self.strongestgps.longitude)
        dictjson['strongestalt'] = str(self.strongestgps.altitude)
        dictjson['strongestspeed'] = str(self.strongestgps.speed)
        dictjson['strongestgpsvalid'] = str(self.strongestgps.isValid)

        dictjson['probedssids'] = self.probedSSIDs
        
        return dictjson
        
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
        self.stationcount = -1
        self.utilization = -1.0
        self.bandwidth = 20 # Default to 20.  we'll bump it up as we see params.  max BW in any protocol 20 or 40 or 80 or 160 MHz
        self.secondaryChannel = 0  # used for 40+ MHz
        self.thirdChannel = 0  # used for 80+ MHz channels
        self.secondaryChannelLocation = ''  # above/below
        now=datetime.datetime.now()
        self.firstSeen = now
        self.lastSeen = now
        self.gps = SparrowGPS()
        self.strongestsignal = self.signal
        self.strongestgps = SparrowGPS()
        
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
        retVal += "Station Count: " + str(self.stationcount) + "\n"
        retVal += "Utilization: " + str(self.utilization) + "\n"
        retVal += "Strongest Signal: " + str(self.strongestsignal) + " dBm\n"
        retVal += "Bandwidth: " + str(self.bandwidth) + "\n"
        retVal += "First Seen: " + str(self.firstSeen) + "\n"
        retVal += "Last Seen: " + str(self.lastSeen) + "\n"
        retVal += "Last GPS:\n"
        retVal += str(self.gps)
        retVal += "Strongest GPS:\n"
        retVal += str(self.strongestgps)

        return retVal

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

    def __ne__(self, other):
            return not self.__eq__(other)
        
    def createFromJsonDict(jsondict):
        retVal = WirelessNetwork()
        retVal.fromJsondict(jsondict)
        return retVal
        
    def fromJsondict(self, dictjson):
        # Note: if the json dictionary isn't correct, this will naturally throw an exception that may
        # need to be caught for error detection
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
        self.stationcount = int(dictjson['stationcount'])
        self.utilization = float(dictjson['utilization'])
        self.strongestsignal = int(dictjson['strongestsignal'])
        self.bandwidth = int(dictjson['bandwidth'])
        self.firstSeen = parser.parse(dictjson['firstseen'])
        self.lastSeen = parser.parse(dictjson['lastseen'])
        self.gps.latitude = float(dictjson['lat'])
        self.gps.longitude = float(dictjson['lon'])
        self.gps.altitude = float(dictjson['alt'])
        self.gps.speed = float(dictjson['speed'])
        self.gps.isValid = stringtobool(dictjson['gpsvalid'])
        
        self.strongestgps.latitude = float(dictjson['strongestlat'])
        self.strongestgps.longitude = float(dictjson['strongestlon'])
        self.strongestgps.altitude = float(dictjson['strongestalt'])
        self.strongestgps.speed = float(dictjson['strongestspeed'])
        self.strongestgps.isValid = stringtobool(dictjson['strongestgpsvalid'])
            
    def fromJson(self, jsonstr):
        dictjson = json.loads(jsonstr)
        self.fromJsondict(dictjson)
            
    def toJsondict(self):
        dictjson = {}
        dictjson['type'] = 'wifi-ap'
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
        dictjson['stationcount'] = self.stationcount
        dictjson['utilization'] = self.utilization

        dictjson['strongestsignal'] = self.strongestsignal
        dictjson['bandwidth'] = self.bandwidth
        dictjson['firstseen'] = str(self.firstSeen)
        dictjson['lastseen'] = str(self.lastSeen)
        dictjson['lat'] = str(self.gps.latitude)
        dictjson['lon'] = str(self.gps.longitude)
        dictjson['alt'] = str(self.gps.altitude)
        dictjson['speed'] = str(self.gps.speed)
        dictjson['gpsvalid'] = str(self.gps.isValid)
        
        dictjson['strongestlat'] = str(self.strongestgps.latitude)
        dictjson['strongestlon'] = str(self.strongestgps.longitude)
        dictjson['strongestalt'] = str(self.strongestgps.altitude)
        dictjson['strongestspeed'] = str(self.strongestgps.speed)
        dictjson['strongestgpsvalid'] = str(self.strongestgps.isValid)
        
        return dictjson
        
    def toJson(self):
        dictjson = self.toJsondict()
        return json.dumps(dictjson)
        
    def getChannelString(self):
        if self.bandwidth == 40 and self.secondaryChannel > 0:
            retVal = str(self.channel) + '+' + str(self.secondaryChannel)
        else:
            retVal = str(self.channel)
            
        return retVal
        
    def getKey(self):
        return self.macAddr + self.ssid+str(self.channel)
        
class WirelessEngine(object):
    def __init__(self):
        super().__init__()

    def getMacAddress(interface):
        macaddr = ""
        
        try:
            f = open('/sys/class/net/'+interface+'/address', 'r')
            macaddr = f.readline().strip()
            f.close()
        except:
            pass
            
        return macaddr
        
    def getFrequencyForChannel(channelNumber):
        channelStr = str(channelNumber)
        if channelStr in channelToFreq:
            return channelToFreq[channelStr]
        else:
            return None
            
    def getSignalQualityFromDB0To5(dBm):
        # Based on same scale tha Microsoft uses.
        # See https://stackoverflow.com/questions/15797920/how-to-convert-wifi-signal-strength-from-quality-percent-to-rssi-dbm
        if (dBm <= -100):
            quality = 0
        elif dBm >= -50:
            quality = 100
        else:
            quality = 2 * (dBm + 100)      
        
        return int(4*quality/100)

    def getSignalQualityFromDB(dBm):
        # Based on same scale tha Microsoft uses.
        # See https://stackoverflow.com/questions/15797920/how-to-convert-wifi-signal-strength-from-quality-percent-to-rssi-dbm
        if (dBm <= -100):
            quality = 0
        elif dBm >= -50:
            quality = 100
        else:
            quality = 2 * (dBm + 100)      
        
        return quality

    def convertUnknownToString(ssid):
        if '\\x00' not in ssid:
            return ssid
            
        retVal = ssid.replace('\\x00', '')
        numblanks = ssid.count('\\x00')
        
        if len(retVal) == 0:
            if numblanks > 0:
                return '<Unknown (' + str(numblanks )+ ')>'
            else:
                return '<Unknown>'
        else:
            return ssid
        
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

    def getMonitoringModeInterfaces(printResults=False):
        # Note: for standard scans with iw, this isn't required.  Just root access.
        # This is only required for some of the more advanced pen testing capabilities
        result = subprocess.run(['iwconfig'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        wirelessResult = result.stdout.decode('ASCII')
        p = re.compile('^(.*?) IEEE.*?Mode:Monitor', re.MULTILINE)
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
            # If we're on a pi or the driver is weird, it may not put IEEE and Mode:Monitor on the same line.
            monLine = -1
            i = 0
            lines = wirelessResult.split('\n')
            for curLine in lines:
                if 'Mode:Monitor' in curLine:
                    monLine = i - 1
                    break
                else:
                    i = i + 1   
            if monLine > -1:
                p = re.compile('^(.*?) .*', re.MULTILINE)
                tmpInterfaces = p.findall(lines[monLine])
                if (len(tmpInterfaces) > 0):
                    for curInterface in tmpInterfaces:
                        tmpStr=curInterface.replace(' ','')
                        retVal.append(tmpStr)

            # debug
            if (len(retVal) == 0 and printResults):
                print("Error: No monitoring mode wireless interfaces found.")

        return retVal

    def getNetworksAsJson(interfaceName, gpsData, huntChannelList=None):
        # This is only used by the remote agent to get and return networks
        if (huntChannelList is None) or (len(huntChannelList) == 0):
            # This code handles the thought that "what if we query for networks and the interface
            # reports busy (it does happen if we query too fast.)
            retries = 0
            retCode = WirelessNetwork.ERR_DEVICEBUSY
            
            while (retCode == WirelessNetwork.ERR_DEVICEBUSY) and (retries < 3):
                # Handle retries in case we get a busy response
                retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(interfaceName)
                retries += 1
                if retCode == WirelessNetwork.ERR_DEVICEBUSY:
                    sleep(0.4)
        else:
            wirelessNetworks = {}
            for curFrequency in huntChannelList:
                # Handle if the device reports busy with some retries
                retries = 0
                retCode = WirelessNetwork.ERR_DEVICEBUSY
                while (retCode == WirelessNetwork.ERR_DEVICEBUSY) and (retries < 3):
                    # Handle retries in case we get a busy response
                    retCode, errString, tmpWirelessNetworks = WirelessEngine.scanForNetworks(interfaceName,curFrequency )
                    retries += 1
                    if retCode == WirelessNetwork.ERR_DEVICEBUSY:
                        sleep(0.2)
                
                for curKey in tmpWirelessNetworks.keys():
                    curNet = tmpWirelessNetworks[curKey]
                    wirelessNetworks[curNet.getKey()] = tmpWirelessNetworks[curNet.getKey()]
            
        retVal = {}
        retVal['errCode'] = retCode
        retVal['errString'] = errString
        
        netList = []
        
        for curKey in wirelessNetworks.keys():
            curNet = wirelessNetworks[curKey]
            if gpsData is not None:
                curNet.gps.copy(gpsData)
            netList.append(curNet.toJsondict())
            
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
        
        jsonstr = json.dumps(retVal)
        
        return retCode, errString, jsonstr
        
    def scanForNetworks(interfaceName, frequency=0, printResults=False):
        
        if frequency == 0:
            result = subprocess.run(['iw', 'dev', interfaceName, 'scan'], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        else:
            result = subprocess.run(['iw', 'dev', interfaceName, 'scan', 'freq', str(frequency)], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

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
        
    def getFieldValue(p, curLine):
        matchobj = p.search(curLine)
        
        if not matchobj:
            return ""
            
        try:
            retVal = matchobj.group(1)
        except:
            retVal = ""
            
        return retVal
        
    def parseIWoutput(iwOutput):
        
        # Define search regexes once:
        p_bss = re.compile('^BSS (.*?)\(')
        p_ssid = re.compile('^.+?SSID: +(.*)')
        p_ess = re.compile('^	capability:.*(ESS)')
        p_ess_privacy = re.compile('^	capability:.*(ESS Privacy)')
        p_ibss = re.compile('^	capability:.*(IBSS)')
        p_ibss_privacy = re.compile('^	capability:.*(IBSS Privacy)')
        p_auth_suites = re.compile('.*?Authentication suites: *(.*)')
        p_pw_ciphers = re.compile('.*?Pairwise ciphers: *(.*)')
        p_param_channel = re.compile('^.*?DS Parameter set: channel +([0-9]+).*')
        p_primary_channel = re.compile('^.*?primary channel: +([0-9]+).*')
        p_freq = re.compile('^.*?freq:.*?([0-9]+).*')
        p_signal = re.compile('^.*?signal:.*?([\-0-9]+).*?dBm')
        p_ht = re.compile('.*?HT20/HT40.*')
        p_bw = re.compile('.*?\\* channel width:.*?([0-9]+) MHz.*')
        p_secondary = re.compile('^.*?secondary channel offset: *([^ \\t]+).*')
        p_thirdfreq = re.compile('^.*?center freq segment 1: *([^ \\t]+).*')
        p_stationcount = re.compile('.*station count: ([0-9]+)')
        p_utilization = re.compile('.*channel utilisation: ([0-9]+)/255')
        # start
        retVal = {}
        curNetwork = None
        now=datetime.datetime.now()
        
        # This now supports direct from STDOUT via scanForNetworks,
        # and input from a file as f.readlines() which returns a list
        if type(iwOutput) == str:
            inputLines = iwOutput.splitlines()
        else:
            inputLines = iwOutput
            
        for curLine in inputLines:
            fieldValue = WirelessEngine.getFieldValue(p_bss, curLine)
                
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

            fieldValue = WirelessEngine.getFieldValue(p_ssid, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.ssid = WirelessEngine.convertUnknownToString(fieldValue)
                
            fieldValue = WirelessEngine.getFieldValue(p_ess, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.mode = "AP"
                # Had issue with WEP not showing up.
                # If capability has "ESS Privacy" there's something there.
                # If it's PSK, etc. there will be other RSN fields, etc.
                # So for now start by assuming WEP
                
                # See: https://wiki.archlinux.org/index.php/Wireless_network_configuration
                fieldValue = WirelessEngine.getFieldValue(p_ess_privacy, curLine)
                    
                if (len(fieldValue) > 0):
                    curNetwork.security = "WEP"
                    curNetwork.privacy = "WEP"
                    
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_ibss, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.mode = "Ad Hoc"
                curNetwork.security = "[Ad-Hoc] Open"

                fieldValue = WirelessEngine.getFieldValue(p_ibss_privacy, curLine)
                    
                if (len(fieldValue) > 0):
                    curNetwork.security = "[Ad-Hoc] WEP"
                    curNetwork.privacy = "WEP"
                    
                continue #Found the item

            # Station count
            fieldValue = WirelessEngine.getFieldValue(p_stationcount, curLine)
            if (len(fieldValue) > 0):
                curNetwork.stationcount = int(fieldValue)
                continue #Found the item
                
            # Utilization
            fieldValue = WirelessEngine.getFieldValue(p_utilization, curLine)
            if (len(fieldValue) > 0):
                utilization = round(float(fieldValue)  / 255.0 * 100.0 * 100.0) / 100.0
                curNetwork.utilization = utilization
                continue #Found the item
                
            # Auth suites
            fieldValue = WirelessEngine.getFieldValue(p_auth_suites, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.security = fieldValue
                continue #Found the item
                
            # p = re.compile('.*?Group cipher: *(.*)')
            fieldValue = WirelessEngine.getFieldValue(p_pw_ciphers, curLine)
            fieldValue = fieldValue.replace(' ', '/')
                
            if (len(fieldValue) > 0):
                curNetwork.privacy = fieldValue
                curNetwork.cipher = fieldValue
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_param_channel, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.channel = int(fieldValue)
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_primary_channel, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.channel = int(fieldValue)
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_freq, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.frequency = int(fieldValue)
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_signal, curLine)
                
            # This test is different.  dBm is negative so can't test > 0.  10dBm is really high so lets use that
            if (len(fieldValue) > 0):
                curNetwork.signal = int(fieldValue)
                curNetwork.strongestsignal = curNetwork.signal
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_ht, curLine)
                
            if (len(fieldValue) > 0):
                if (curNetwork.bandwidth == 20):
                    curNetwork.bandwidth = 40
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_bw, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.bandwidth = int(fieldValue)
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_secondary, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.secondaryChannelLocation = fieldValue
                if (fieldValue == 'above'):
                    curNetwork.secondaryChannel = curNetwork.channel + 4
                elif (fieldValue == 'below'):
                    curNetwork.secondaryChannel = curNetwork.channel - 4
                # else it'll say 'no secondary'
                    
                continue #Found the item
                
            fieldValue = WirelessEngine.getFieldValue(p_thirdfreq, curLine)
                
            if (len(fieldValue) > 0):
                curNetwork.thirdChannel = int(fieldValue)
                    
                continue #Found the item
                
        # #### End loop ######
        
        # Add the last network
        if curNetwork is not None:
            if curNetwork.channel > 0:
                # I did see incomplete output from iw where not all the data was there
                retVal[curNetwork.getKey()] = curNetwork
        
        return retVal
        
if __name__ == '__main__':
    # WirelessEngine.getMacAddress('wlan0mon')
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
