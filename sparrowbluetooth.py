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
import sys
import re
import signal
from time import sleep
from threading import Lock
import datetime
from dateutil import parser
import json
import math # sqrt
import sqlite3
import uuid

from sparrowcommon import BaseThreadClass, stringtobool
from sparrowgps import SparrowGPS

# ------------------ Global Functions --------------------------------------
def toHex(val):
    return format(val, "04x").upper()

def hexSplit(string):
    retVal = ' '
    return retVal.join([string[i:i+2] for i in range(0, len(string), 2)])
  
# ------------------  Bluetooth Device Descriptor Class  ----------------------------------
class BluetoothDevice(object):
    BT_CLASSIC = 1
    BT_LE = 2
    
    def __init__(self):
        self.uuid=""
        self.macAddress = ""
        self.name=""
        self.company=""
        self.manufacturer=""
        self.bluetoothDescription = ""
        self.btType = BluetoothDevice.BT_LE  # Classic or low energy
        self.rssi=-100
        self.txPower = -60
        self.txPowerValid = False
        self.iBeaconRange = -1.0
        self.firstSeen = datetime.datetime.now()
        self.lastSeen = datetime.datetime.now()

        self.gps = SparrowGPS()
        self.strongestRssi = self.rssi
        self.strongestgps = SparrowGPS()
        
        self.foundInList = False
        
    def __str__(self):
        retVal = ""
        retVal += "UUID: " + self.uuid + '\n'
        retVal += "Address: " + self.macAddress + '\n'
        retVal += "Company: " + self.company + '\n'
        retVal += "Manufacturer: " + self.manufacturer + '\n'
        if self.btType == BluetoothDevice.BT_CLASSIC:
            retVal += 'btType: Bluetooth Classic\n'
        else:
            retVal += 'btType: Bluetooth Low Energy (BTLE)\n'
        
        retVal += "Bluetooth Description: " + self.bluetoothDescription + '\n'
        retVal += "RSSI: " + str(self.rssi) + '\n'
        retVal += "TX Power: " + str(self.txPower) + '\n'
        retVal += "TX Power Valid: " + str(self.txPowerValid) + '\n'
        retVal += "Estimated Range (m): " + str(self.iBeaconRange) + '\n'

        retVal += "Strongest RSSI: " + str(self.strongestRssi) + '\n'
        
        retVal += "Last GPS:\n"
        retVal += str(self.gps)
        retVal += "Strongest GPS:\n"
        retVal += str(self.strongestgps)
        
        return retVal

    def __eq__(self, obj):
        # This is equivance....   ==
        if not isinstance(obj, BluetoothDevice):
           return False
          
        if self.uuid != obj.uuid:
            return False
            
        if self.macAddress != obj.macAddress:
            return False
            
        if self.btType != obj.btType:
            return False

        return True

    def __ne__(self, other):
            return not self.__eq__(other)
        
    def copy(self, other):
        self.uuid=other.uuid
        self.macAddress = other.macAddress
        self.name=other.name
        self.company=other.company
        self.manufacturer=other.manufacturer
        self.bluetoothDescription = other.bluetoothDescription
        self.btType = other.btType
        self.rssi=other.rssi
        self.txPower = other.txPower
        self.txPowerValid = other.txPowerValid
        self.iBeaconRange = other.iBeaconRange
        self.firstSeen = other.firstSeen
        self.lastSeen = other.lastSeen

        self.gps.copy(other.gps)
        self.strongestRssi = other.strongestRssi
        self.strongestgps.copy(other.strongestgps)
        
        self.foundInList = False
        
    def getKey(self):
        key = self.macAddress # + "_" + str(self.btType)
        
        return key
        
    def calcRange(self):
        if not self.txPowerValid or self.txPower == 0:
            self.iBeaconRange = -1
            return

        # This is what iOS does:
        # https://stackoverflow.com/questions/20416218/understanding-ibeacon-distancing
        # Also note: "accuracy" is iOS's terminology for distance
        #ratio = self.rssi / self.txPower
        #if ratio < 1.0:
        #    self.iBeaconRange = ratio ** 10
        #else:
        #    self.iBeaconRange = 0.89976* (ratio ** 7.7095) + 0.111
            
        #self.iBeaconRange = round(self.iBeaconRange, 2)
        
        try:
            ratio_db = float(self.txPower - self.rssi)
            
            # txPower is supposed to be the RSSI measured at 1m.
            # In reality that's not quite what I've observed.
            
            # txPower may be a default/guess so watch the math.
            # Generally rssi < txPower making ratio_db >= 0
            if ratio_db < 0.0:
                self.iBeaconRange = 0.0
                return
            elif ratio_db <= 1.5:
                self.iBeaconRange = 0.5
                return
            elif ratio_db <= 3.0:
                self.iBeaconRange = 1.0
                return
                
            #n = 3 # free space n = 2, real-world range is 2.7 - 4.3
           # If we don't have an rssi, this could calc wrong
            #dist = 10 ** (ratio_db / (10*n))
            dist = 10.0 ** (ratio_db / 10.0)
            # Safety check on sqrt
            if dist < 0.0:
                dist = 0.0
                
            dist = math.sqrt(dist)
            self.iBeaconRange = round(dist, 2)
        except:
            self.iBeaconRange = -1
            
        # Old:
        #try:
        #    txPower = int(strTxPower)
        #    ratio_db = float(txPower - rssi)
           # If we don't have an rssi, this could calc wrong
        #    ratio_linear = 10.0 ** ( ratio_db / 10.0 )
        #    if ratio_linear >= 0.0:
        #        dist = round(math.sqrt(ratio_linear), 2)
        #    else:
        #        dist = -1.0
        #except:
        #    dist = -1.0
        
        #try:
        #    txPower = int(strTxPower)
        #    gain = 5  # FSPL unknown gain factor
        #    path_loss = math.fabs(float(txPower - rssi)) + gain
            # If we don't have an rssi, this could calc wrong
            # Calc is based on free space path loss at 2.4ish MHz (freq matters)
            # http://www.electronicdesign.com/communications/understanding-wireless-range-calculations
        #    dist = 10.0 ** ( (path_loss - 32.44 - 67.78) / 20.0 ) * 1000.0
        #    dist = round(dist, 2)
        #except:
        #    dist = -1
        
    def fromJson(self, jsonstr):
        dictjson = json.loads(jsonstr)
        self.fromJsondict(dictjson)
            
    def toJson(self):
        dictjson = self.toJsondict()
        return json.dumps(dictjson)
        
    def fromJsondict(self, dictjson):
        # Note: if the json dictionary isn't correct, this will naturally throw an exception that may
        # need to be caught for error detection
        self.uuid = dictjson['uuid']
        self.macAddress = dictjson['macAddr']
        self.name = dictjson['name']
        self.company = dictjson['company']
        self.manufacturer = dictjson['manufacturer']
        self.bluetoothDescription = dictjson['bluetoothdescription']
        self.btType = int(dictjson['bttype'])
        self.rssi = int(dictjson['rssi'])
        self.txPower = int(dictjson['txpower'])
        self.txPowerValid = stringtobool(dictjson['txpowervalid'])
        self.strongestRssi = int(dictjson['strongestrssi'])
        self.iBeaconRange = float(dictjson['ibeaconrange'])

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
            
    def toJsondict(self):
        dictjson = {}
        dictjson['type'] = 'bluetooth'
        dictjson['uuid'] = self.uuid
        dictjson['macAddr'] = self.macAddress
        dictjson['name'] = self.name
        dictjson['company'] = self.company
        dictjson['manufacturer'] = self.manufacturer
        dictjson['bluetoothdescription'] = self.bluetoothDescription
        dictjson['bttype'] = self.btType
        dictjson['rssi'] = self.rssi
        dictjson['txpower'] = self.txPower
        dictjson['txpowervalid'] = str(self.txPowerValid)
        dictjson['strongestrssi'] = self.strongestRssi
        dictjson['ibeaconrange'] = self.iBeaconRange
    
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
        
    
# ------------------  Ubertooth Specan scanning Thread ----------------------------------
class BtmonThread(BaseThreadClass):
    def __init__(self, parentBluetooth):
        super().__init__()
        self.parentBluetooth= parentBluetooth
        self.hcitoolProc = None
        self.btmonProc = None
        self.daemon = True
        
    def getFieldValue(self, p, curLine):
        matchobj = p.search(curLine)
        
        if not matchobj:
            return ""
            
        try:
            retVal = matchobj.group(1)
        except:
            retVal = ""
            
        return retVal
        
    def resetDevice(self):
        # Have to kill btmon and hcitool if they're running
        subprocess.run(['pkill', 'btmon'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['pkill', '-f','hcitool.*scan'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        subprocess.run(['hciconfig', 'hci0', 'down'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['hciconfig', 'hci0', 'up'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
    def startBTMon(self):
        self.btmonProc = subprocess.Popen(['btmon'],stdout=subprocess.PIPE,bufsize=1, stderr=subprocess.PIPE)

    def startHCITool(self):
        self.hcitoolProc = subprocess.Popen(['hcitool', 'lescan', '--duplicates'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def btMonRunning(self):
        if not self.btmonProc:
            return False
            
        pollrunning = self.btmonProc.poll() is None
        
        return pollrunning
        
    def hcitoolRunning(self):
        if not self.hcitoolProc:
            return False
            
        pollrunning = self.hcitoolProc.poll() is None
        
        return pollrunning
        
    def stopAndWait(self):
        super().stopAndWait()
        if self.threadRunning:
            # May be stuck at readline
            if self.btmonProc:
                self.btmonProc.kill()
                
            if self.hcitoolProc:
                self.hcitoolProc.kill()

    def run(self):
        self.threadRunning = True
        
        # See this for a good example on threading and reading from a streaming proc
        # https://stackoverflow.com/questions/16768290/understanding-popen-communicate

        # Reset interface.  Have had hcitool lescan fail on bad parameters
        self.resetDevice()
        
        # Just a basic sleep for 1 second example loop.
        # Instantiate and call start() to get it going
        
        iteration = 0

        # Have to start hcitool first because it will set the radio.
        # If you try to start btmon first, it may lock it and cause hcitool to fail
        self.startHCITool()
        self.startBTMon()
        
        p_address = re.compile('Address: ([0-9A-F]{2,2}:[0-9A-F]{2,2}:[0-9A-F]{2,2}:[0-9A-F]{2,2}:[0-9A-F]{2,2}:[0-9A-F]{2,2})')
        p_company = re.compile('Company: (.*) \(')
        # p_type = re.compile('Type: (.*?) (')
        p_rssi = re.compile('RSSI: (.*?) dB.*')
        p_txpower = re.compile('TX power: (.*?) dB.*')
        p_uuid = re.compile('UUID: (.*)')
        p_name = re.compile('Name.*?: (.*)')
        # p_eventType = re.compile('Event type: (.*)')
        
        curDevice = None
        # eventType = ""
        
        while not self.signalStop:
            if not self.hcitoolRunning():
                # May have died
                # Note: btmon can still keep running even over an HCI reset
                self.resetDevice()
                self.startHCITool()
                
            if not self.btMonRunning():
                self.startBTMon()
                
            curLine = self.btmonProc.stdout.readline().decode('ASCII').replace('\n', '')

            # Address
            fieldValue = self.getFieldValue(p_address, curLine)
                
            if (len(fieldValue) > 0):
                curDevice = BluetoothDevice()
                # eventType = ""
                # Just doing this scan for LE now.
                curDevice.btType = BluetoothDevice.BT_LE                
                # This will start a new bluetooth device
                curDevice.macAddress = fieldValue
            
            # Name
            if 'Company' in curLine:
                pass
                
            fieldValue = self.getFieldValue(p_name, curLine)
                
            if (len(fieldValue) > 0):
                # This will start a new bluetooth device
                curDevice.name = fieldValue
        
            # UUID
            fieldValue = self.getFieldValue(p_uuid, curLine)
                
            if (len(fieldValue) > 0):
                # This will start a new bluetooth device
                curDevice.uuid = fieldValue
        
            # Company
            fieldValue = self.getFieldValue(p_company, curLine)
                
            if (len(fieldValue) > 0):
                # This will start a new bluetooth device
                curDevice.company = fieldValue
        
            # Event Type
            # eventType = self.getFieldValue(p_eventType, curLine)
                
            # TX Power
            fieldValue = self.getFieldValue(p_txpower, curLine)
                
            if (len(fieldValue) > 0):
                # This will start a new bluetooth
                try:
                    tmpPower = int(fieldValue)
                    
                    # If there's an error in the data or pattern rec,
                    # There's no way "Low Energy" would transmit with 0+ dBm.  That's not LE.
                    if tmpPower < 0:
                        curDevice.txPower = tmpPower
                        curDevice.txPowerValid = True
                except:
                    pass
                
            # RSSI - Will end the block
            fieldValue = self.getFieldValue(p_rssi, curLine)
                
            if (len(fieldValue) > 0):
                # This will start a new bluetooth device
                try:
                    curDevice.rssi = int(fieldValue)
                    curDevice.strongestRssi = curDevice.rssi
                    curDevice.calcRange()
                except:
                    pass
                
                if curDevice and len(curDevice.macAddress) > 0:
                    self.parentBluetooth.deviceLock.acquire()
                    
                    if curDevice.macAddress in self.parentBluetooth.devices:
                        # We may not always get some fields
                        lastDevice = self.parentBluetooth.devices[curDevice.macAddress]
                        curDevice.firstSeen = lastDevice.firstSeen  # copy first seen timestamp
                        curDevice.gps.copy(lastDevice.gps)
                        curDevice.strongestgps.copy(lastDevice.strongestgps)
                        
                        if len(lastDevice.name) > 0 and len(curDevice.name) == 0:
                            curDevice.name = lastDevice.name
                        if len(lastDevice.uuid) > 0 and len(curDevice.uuid) == 0:
                            curDevice.uuid = lastDevice.uuid
                        if lastDevice.txPowerValid and not curDevice.txPowerValid:
                            curDevice.txPower = lastDevice.txPower
                            curDevice.txPowerValid = lastDevice.txPowerValid
                            
                    self.parentBluetooth.devices[curDevice.macAddress] = curDevice
                    self.parentBluetooth.deviceLock.release()
                
            # Just give the thread a chance to release resources
            iteration += 1
            if iteration > 50000:
                iteration = 0
                sleep(0.01)

        try:
            self.hcitoolProc.kill()
        except:
            pass
            
        try:
            self.btmonProc.kill()
        except:
            pass
        
        self.resetDevice()
        
        self.threadRunning = False

# ------------------  Ubertooth Specan scanning Thread ----------------------------------
class specanThread(BaseThreadClass):
    def __init__(self, parentBluetooth):
        super().__init__()
        self.parentBluetooth= parentBluetooth
        self.daemon = True
        
    def run(self):
        self.threadRunning = True
        
        # See this for a good example on threading and reading from a streaming proc
        # https://stackoverflow.com/questions/16768290/understanding-popen-communicate
        specanProc = subprocess.Popen(['ubertooth-specan'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        # Just a basic sleep for 1 second example loop.
        # Instantiate and call start() to get it going
        
        iteration = 0
        rssi_offset = -54   # Note: This is direct from the Ubertooth bluetooth module.  I thought the RSSI's looked really high.
        
        while not specanProc.poll() and not self.signalStop:
            dataline = specanProc.stdout.readline().decode('ASCII').replace('\n', '')
            dataline = dataline.replace(' ', '')
            data = dataline.split(',')
            if len(data) >= 3:
                frequency = data[1]
                rssi = data[2]
                try:
                    self.parentBluetooth.spectrum[int(frequency)] = int(rssi)  + rssi_offset
                    # print('Frequency: ' + str(frequency) + ', RSSI: ' + str(rssi))
                except:
                    pass
        
            # Just give the thread a chance to release resources
            iteration += 1
            if iteration > 50000:
                iteration = 0
                sleep(0.01)
                
        try:
            specanProc.kill()
        except:
            pass
        
        self.threadRunning = False

# ------------------  Sparrow Bluetooth Class ----------------------------------
class SparrowBluetooth(object):
    SCANTYPE_BLUEHYDRA = 1
    SCANTYPE_ADVERTISEMENT = 2
    
    def __init__(self):
        self.spectrum = {}
        for i in range(2402, 2495):
            self.spectrum[i] = -100
            
        self.spectrumLock = Lock()
        self.deviceLock = Lock()
    
        # This scan thread is for the spectrum
        self.spectrumScanThread = None
        
        self.blueHydraProc = None
        self.btmonThread = None
        self.devices = {}
        self.scanType = SparrowBluetooth.SCANTYPE_BLUEHYDRA
        
        self.beaconActive = False
        
        self.hasBluetooth = False
        self.hasUbertooth = False
        self.hasBlueHydra = False

        numBtAdapters = len(SparrowBluetooth.getBluetoothInterfaces())
        if numBtAdapters > 0:
            self.hasBluetooth = True
        
        if SparrowBluetooth.getNumUbertoothDevices() > 0:
            #SparrowBluetooth.ubertoothStopSpecan()
            errcode, errmsg = SparrowBluetooth.hasUbertoothTools()
            # errcode, errmsg = SparrowBluetooth.ubertoothOnline()
            if errcode == 0:
                self.hasUbertooth = True
                
        if os.path.isfile('/opt/bluetooth/blue_hydra/bin/blue_hydra'):
            self.hasBlueHydra = True
            
    def __str__(self):
        retVal = ""
        
        retVal += "Has Bluetooth Hardware: " + str(self.hasBluetooth) + '\n'
        retVal += "Has Ubertooth Hardware and Software: " + str(self.hasUbertooth) + '\n'
        retVal += "Has Blue Hydra: " + str(self.hasBlueHydra) + '\n'
        
        if self.scanRunning():
            retVal += "Scan Running: Yes"+ '\n'
        else:
            retVal += "Scan Running: No"+ '\n'
            
        return retVal
   
    def startBeacon(self, uuidOverride=""):
        # Can gen a uuid with uuid.uuid4().hex
        
        if len(uuidOverride) > 0:
            struuid = uuidOverride
        else:
          # The UUID below is the same as the iOS "Beacon Toolkit" app uses
          # Can pass it any UUID as a parameter
          
            # uuid = 'E20A39F473F54BC4A12F17D1AD07A961'
            struuid = uuid.uuid4().hex

        # First reset
        subprocess.run(['hciconfig', 'hci0', 'down'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['hciconfig', 'hci0', 'up'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        # Turn on LE advertising.  3 says we're not connectable, just like a true iBeacon
        subprocess.run(['hciconfig', 'hci0', 'leadv', '3'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        # Turn off scanning
        subprocess.run(['hciconfig', 'hci0', 'noscan'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        # Issue command
        majorhex = toHex(0)
        minorhex = toHex(0)
        powerhex = toHex(200)
        
        uuid_bytes = hexSplit(struuid).split(' ')
        params = ['hcitool', '-i','hci0', 'cmd', '0x08','0x0008', '1E', '02', '01', '1A', '1A', 'FF', '4C', '00', '02', '15']
        params = params + uuid_bytes
        params.append(majorhex)
        params.append(minorhex)
        params.append(powerhex)
        params.append('00')
        
        result = subprocess.run(params, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        if result.returncode == 0:
            self.beaconActive = True
        else:
            self.beaconActive = False
            
        return self.beaconActive
        
    
    def stopBeacon(self):
        # Stop LE advertisement
        subprocess.run(['hciconfig', 'hci0', 'noleadv'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        # Re-enable scan
        subprocess.run(['hciconfig', 'hci0', 'piscan'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        # Reset device
        subprocess.run(['hciconfig', 'hci0', 'down'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['hciconfig', 'hci0', 'up'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        self.beaconActive = False
     
    def beaconRunning(self):
        return self.beaconActive
        
    def discoveryRunning(self):
        if self.blueHydraProc:
            pollrunning = self.blueHydraProc.poll() is None
            
            # If the process stopped let's update our records
            if not pollrunning:
                self.blueHydraProc = None
                
            # return pollrunning true/false
            return pollrunning
        else:
            if self.btmonThread and self.btmonThread.threadRunning:
               return True
            else:
                return False
            
    def stopDiscovery(self):
        if self.blueHydraProc:
            try:
                self.blueHydraProc.kill()
            except:
                pass
                
            self.blueHydraProc = None
            
            SparrowBluetooth.resetUbertooth()
        
        if self.btmonThread and self.btmonThread.threadRunning:
            self.btmonThread.stopAndWait()
            self.btmonThread = None

    def updateDeviceList(self):
        # Because GPS comes from further up the stack, we maintain a local class list and have to
        # be sure to copy some fields like firstseen forward on updates
        if self.scanType == SparrowBluetooth.SCANTYPE_BLUEHYDRA:
            errcode, retList = SparrowBluetooth.getBlueHydraBluetoothDevices()        
            
            if errcode == 0:
                self.deviceLock.acquire()
                for curDevice in retList:
                    if curDevice.macAddress not in self.devices:
                        self.devices[curDevice.macAddress] = curDevice
                    else:
                        # Already had it so copy forward a few fields then update our list
                        dev = self.devices[curDevice.macAddress]
                        curDevice.firstSeen = dev.firstSeen
                        curDevice.strongestRssi = dev.strongestRssi
                        curDevice.strongestgps.copy(dev.strongestgps)
                        self.devices[curDevice.macAddress] = curDevice
                self.deviceLock.release()                            
        else:
            errcode = 0
            
        return errcode
        
    def getDiscoveredDevices(self):
        errcode = self.updateDeviceList()
        
        retList = []
        
        # Now copy to return list
        self.deviceLock.acquire()
        
        for curKey in self.devices.keys():
            curEntry = self.devices[curKey]
            newDevice = BluetoothDevice()
            newDevice.copy(curEntry)
            retList.append(newDevice)
            
        self.deviceLock.release()
                
        return errcode, retList
        
    def startDiscovery(self, useBlueHydra=True):
        self.devices.clear()
        
        if useBlueHydra:
            # Make sure we don't have a discovery scan running
            if self.btmonThread and self.btmonThread.threadRunning:
                self.btmonThread.stopAndWait()
                self.btmonThread = None

            # If we're already running just return
            if self.blueHydraProc:
                # poll() returns None when a process is running, otherwise the result is an integer
                pollrunning = self.blueHydraProc.poll() is None
                if not pollrunning:
                    self.blueHydraProc = None
                else:
                    # Already running
                    return
                    
            self.scanType = SparrowBluetooth.SCANTYPE_BLUEHYDRA
            # Clear the sqlite table
            SparrowBluetooth.blueHydraClearDevices()
            
            # -d says daemonize
            self.blueHydraProc = subprocess.Popen(['bin/blue_hydra', '-d'],cwd='/opt/bluetooth/blue_hydra', stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:
            # If we're already running just return
            if self.btmonThread and self.btmonThread.threadRunning:
                return

            # Stop blue hydra if it's running
            if self.blueHydraProc:
                try:
                    self.blueHydraProc.kill()
                except:
                    pass
                    
                self.blueHydraProc = None
                
                
            self.scanType = SparrowBluetooth.SCANTYPE_ADVERTISEMENT
            self.btmonThread = BtmonThread(self)
            self.btmonThread.start()
            
            
    def blueHydraClearDevices(filepath='/opt/bluetooth/blue_hydra/blue_hydra.db'):
        if not os.path.isfile(filepath):
            return -1
            
        try:
            blueHydraDB = sqlite3.connect(filepath)
        except:
            return -2
        
        cursor = blueHydraDB.cursor()
        
        try:
            cursor.execute('''delete FROM blue_hydra_devices''')
            blueHydraDB.commit()
        except:
            pass
            
        return 0

    def getBlueHydraBluetoothDevices(filepath='/opt/bluetooth/blue_hydra/blue_hydra.db'):
        if not os.path.isfile(filepath):
            return -1, None
            
        try:
            blueHydraDB = sqlite3.connect(filepath)
        except:
            return -2, None
            
        cursor = blueHydraDB.cursor()
        
        deviceList = []
        
        try:
            #                                        0        1          2            3               4                   5              6                   7          8             9                  10             11                    12
            cursor.execute('''SELECT uuid,address,name,company,classic_mode,classic_rssi,lmp_version,le_mode,le_rssi,ibeacon_range,last_seen,le_tx_power,classic_tx_power FROM blue_hydra_devices''')
            devices = cursor.fetchall()
            for curDevice in devices:
                btDevice = BluetoothDevice()
                btDevice.uuid = curDevice[0]
                btDevice.macAddress = curDevice[1]
                if curDevice[2]:
                    btDevice.name = curDevice[2]
                if curDevice[3]:
                    btDevice.company = curDevice[3]
                    
                if curDevice[4] == 't':
                    btDevice.btType = BluetoothDevice.BT_CLASSIC
                    # parse [5] for RSSI
                    if curDevice[5]:
                        jsonRSSI = json.loads(curDevice[5])
                        highesttimestamp = 0
                        for curEntry in jsonRSSI:
                            if curEntry['t'] > highesttimestamp:
                                strRssi = curEntry['rssi']
                                strRssi = strRssi.replace(' dBm', '')
                                btDevice.rssi = int(strRssi)
                                btDevice.strongestRssi = btDevice.rssi
                                highesttimestamp = curEntry['t']

                    if curDevice[12]:
                        # Have tx power
                        strTxPower = curDevice[12].replace(' dB', '')
                        try:
                            btDevice.txPower = int(strTxPower)
                            btDevice.txPowerValid = True
                            btDevice.calcRange()
                        except:
                            btDevice.txPower = -60
                else:
                    btDevice.btType = BluetoothDevice.BT_LE
                    # parse [8] for RSSI
                    if curDevice[8]:
                        jsonRSSI = json.loads(curDevice[8])
                        highesttimestamp = 0
                        for curEntry in jsonRSSI:
                            if curEntry['t'] > highesttimestamp:
                                strRssi = curEntry['rssi']
                                strRssi = strRssi.replace(' dBm', '')
                                btDevice.rssi = int(strRssi)
                                highesttimestamp = curEntry['t']
                    
                    if curDevice[11]:
                        # Have tx power
                        strTxPower = curDevice[11].replace(' dB', '')
                        try:
                            btDevice.txPower = int(strTxPower)
                            btDevice.txPowerValid = True
                            btDevice.calcRange()
                        except:
                            btDevice.txPower = -60
                            
                if curDevice[6]:
                    btDevice.bluetoothDescription = curDevice[6]
                    
                if curDevice[9]:
                    btDevice.iBeaconRange = int(curDevice[9])
                    
                btDevice.lastSeen = datetime.datetime.fromtimestamp(curDevice[10])
                deviceList.append(btDevice)
        except:
            return -3, None
        
        return 0, deviceList
        
    def spectrumToChannels(self):
        retVal = {}
        
        for curKey in self.spectrum.keys():
            # curKey is frequency
            channel = SparrowBluetooth.fFreqToChannel(curKey)
            rssi = self.spectrum[curKey]
            if rssi > -10.0:
                rssi = -10.0
            retVal[channel] = rssi
            
        return retVal
        
    def fFreqToChannel(frequency):
        # Note: This function returns a float for partial channels

        # ch1 center freq is 2412.  +- 1 channel is 5 MHz
        # 2402 = Ch -1
        
        
        # Map bluetooth frequency to 2.4 GHz wifi channel
        # ch 1 starts at 2401 MHz and ch 14 tops out at 2495
        if frequency < 2402:
            return float(-1.0)
        elif  frequency > 2494:
            return float(16.0)
            
        channel = -1.0 + (float(frequency) - 2402)/5
        return channel
        
        # Frequency range of 2.4 GHz channels 1 (low end 2402) to 14 (high end 2494)
        #frange = 2494.0 - 2402.0
        # The top end of 14 is 2494 but that would map to 16 on the chart
        #crange = 16.0
        #channel = float((float(frequency) - 2402.0) / frange * crange)
        
        #return channel
        
    def startScanning(self):
        if self.spectrumScanThread:
            self.stopScanning()
            
        self.spectrumScanThread = specanThread(self)
        self.spectrumScanThread.start()
        
    def scanRunning(self):
        if self.spectrumScanThread and self.spectrumScanThread.threadRunning:
            return True
        else:
            return False
            
    def scanInitializing(self):
        if len(self.spectrum) < 79:
            return True
        else:
            return False
            
    def stopScanning(self):
        if self.spectrumScanThread:
            self.spectrumScanThread.stopAndWait()
            self.spectrumScanThread = None
            SparrowBluetooth.resetUbertooth()
            
    def resetUbertooth():
        result = subprocess.run(['ubertooth-util', '-r'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        
        return result.returncode
        
    def getNumUbertoothDevices():
        result = subprocess.run(['lsusb', '-d', '1d50:6002'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            return 0
            
        hciResult = result.stdout.decode('ASCII')
        p = re.compile('^.*(1d50)', re.MULTILINE)
        tmpInterfaces = p.findall(hciResult)
        
        retVal = 0
        
        if (len(tmpInterfaces) > 0):
            for curInterface in tmpInterfaces:
                retVal += 1

        return retVal
        
        
    def getBluetoothInterfaces(printResults=False):
        try:
            result = subprocess.run(['hcitool', 'dev'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        except:
            print('ERROR: Unable to run hcitool.  Reporting no bluetooth devices.')
            return []
        
        if result.returncode != 0:
            return []
            
        hciResult = result.stdout.decode('ASCII')
        p = re.compile('^.*(hci[0-9])', re.MULTILINE)
        tmpInterfaces = p.findall(hciResult)
        
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
        
    def getUbertoothSpecanProcesses():
        # Returns a list of process id's
        result = subprocess.run(['pgrep', '-f','ubertooth-specan'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        testResult = result.stdout.decode('ASCII')

        retVal = []
        if result.returncode != 0:
            return retVal
            
        procList = testResult.split('\n')
        for curLine in procList:
            if len(curLine) > 0:
                retVal.append(int(curLine))
            
        return retVal
        
    def ubertoothSpecanRunning():
        result = subprocess.run(['pgrep', '-f','ubertooth-specan'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)

        # it's a grep, so if the pattern exists, pgrep returns 0, else it returns 1 (or something greater than 0 as an err_not_found
        if result.returncode == 0:
            return True
        else:
            return False
        
    def ubertoothStopSpecan():
        procList = SparrowBluetooth.getUbertoothSpecanProcesses()
        
        for curProc in procList:
            try:
                os.kill(curProc, signal.SIGINT)
            except:
                pass

    def hasBluetoothHardware():
        if SparrowBluetooth.getNumUbertoothDevices() == 0:
            return False
            
        numBtAdapters = SparrowBluetooth.getBluetoothInterfaces()
        
        if len(numBtAdapters) == 0:
            return False
            
        return True
            
    def hasUbertoothTools():
        if  not os.path.isfile('/usr/local/bin/ubertooth-specan') and not os.path.isfile('/usr/bin/ubertooth-specan'):
            return -1, 'ubertooth tools not found.'
            
        return 0, ''
        
    def ubertoothOnline():
        # Check if ubertooth-specan is installed and that the ubertooth is actually present
        if  not os.path.isfile('/usr/local/bin/ubertooth-specan') and not os.path.isfile('/usr/bin/ubertooth-specan'):
            return -1, 'ubertooth tools not found.'
                
        if SparrowBluetooth.ubertoothSpecanRunning():
            return -2, 'Ubertooth-specan is running.  Please stop it before continuing.'
            
        # aircrack-ng -a2 -b D8:EB:97:2F:DD:CE -w /opt/wordlists/TopPasswords3-2.txt falconcap-01.cap
        params = ['ubertooth-util', '-v']
        
        result = subprocess.run(params, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        testResult = result.stdout.decode('ASCII')
        if 'could not open Ubertooth device' in testResult:
            return -3, 'Unable to find Ubertooth device'
        
        if result.returncode == 0:
            return 0, ""
        else:
            return -4, 'Unable to open Ubertooth device'

def testSpectrum():
    bt.startScanning()
    
    try:
        while True:
            print('Spectrum Size: ' + str(len(bt.spectrum)))

            if bt.scanInitializing():
                print('Scan intializing...')

            sleep(1)
            
    except KeyboardInterrupt:
        print('Shutting down...')
        bt.stopScanning()
        print('Done')
    
if __name__ == '__main__':
    errcode, devices=SparrowBluetooth.getBlueHydraBluetoothDevices()
    
    btInterfaces = SparrowBluetooth.getBluetoothInterfaces()
    
    if len(btInterfaces) > 0:
        print('Bluetooth (hci) interfaces:')

        for curInterface in btInterfaces:
            print(curInterface)
        
    errcode, errmsg = SparrowBluetooth.ubertoothOnline()
    
    if errcode == 0:
        print('Ubertooth tools found and device is online')
    else:
        print('Error: ' + errmsg)
        specanProcesses = SparrowBluetooth.getUbertoothSpecanProcesses()
        
        for curProc in specanProcesses:
            print(curProc)
            
    bt=SparrowBluetooth()

    print(bt)

    bt.startBeacon()
    sleep(5)
    bt.stopBeacon()
    # testSpectrum()
