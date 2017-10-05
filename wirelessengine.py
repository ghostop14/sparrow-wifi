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

import subprocess
import re
import datetime
# import pytz
# import json

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
        
        # Used for tracking in network table
        self.foundInList = False

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

        return retVal

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
        
