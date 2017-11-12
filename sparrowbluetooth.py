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
import copy
import signal
from time import sleep
from threading import Lock
import datetime
from dateutil import parser
import json

import sqlite3

if '..' not in sys.path:
    sys.path.insert(0, '..')
from sparrowcommon import BaseThreadClass, stringtobool
from sparrowgps import SparrowGPS

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
        self.iBeaconRange = -1
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
        retVal += "iBeacn Range: " + str(self.iBeaconRange) + '\n'

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
        
    def copy(self):
        return copy.deepcopy(self)
        
    def getKey(self):
        key = self.macAddress + "_" + str(self.btType)
        
        return key
        
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
        self.strongestRssi = int(dictjson['strongestrssi'])
        self.iBeaconRange = int(dictjson['ibeaconrange'])

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
class specanThread(BaseThreadClass):
    def __init__(self, parentBluetooth):
        super().__init__()
        self.parentBluetooth= parentBluetooth
        
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
    def __init__(self):
        self.spectrum = {}
        self.spectrumLock = Lock()
        
        self.scanThread = None
        

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
            #                                        0        1          2            3               4                   5              6                   7          8             9                  10 
            cursor.execute('''SELECT uuid,address,name,company,classic_mode,classic_rssi,lmp_version,le_mode,le_rssi,ibeacon_range,last_seen FROM blue_hydra_devices''')
            devices = cursor.fetchall()
            for curDevice in devices:
                btDevice = BluetoothDevice()
                btDevice.uuid = curDevice[0]
                btDevice.macAddress = curDevice[1]
                if curDevice[2]:
                    btDevice.name = curDevice[2]
                if curDevice[3]:
                    btDevice.company = curDevice[3]
                    
                if curDevice[4] == True:
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
        
        frange = 2495.0 - 2401.0
        crange = 14.0
        
        for curKey in self.spectrum.keys():
            if curKey >= 2401.0:
                channel = (curKey - 2401.0) / frange * crange
                rssi = self.spectrum[curKey]
                if rssi > -10.0:
                    rssi = -10.0
                retVal[channel] = rssi
            
        return retVal
        
    def fFreqToChannel(frequency):
        # Note: This function returns a float for partial channels
        
        # Map bluetooth frequency to 2.4 GHz wifi channel
        # ch 1 starts at 2401 MHz and ch 14 tops out at 2495
        if frequency < 2401 or frequency > 2495:
            return float(0.0)
            
        frange = 2495.0 - 2401.0
        crange = 14.0
        channel = float((float(frequency) - 2401.0) / frange * crange)
        
        return channel
        
    def startScanning(self):
        if self.scanThread:
            self.stopScanning()
            
        self.scanThread = specanThread(self)
        self.scanThread.start()
        
    def scanRunnning(self):
        if self.scanThread and self.scanThread.threadRunning:
            return True
        else:
            return False
            
    def scanInitializing(self):
        if len(self.spectrum) < 79:
            return True
        else:
            return False
            
    def stopScanning(self):
        if self.scanThread:
            self.scanThread.stopAndWait()
            self.scanThread = None
            
    def getNumUbertoothDevices():
        result = subprocess.run(['lsusb', '-d', '1d50:'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            return []
            
        hciResult = result.stdout.decode('ASCII')
        p = re.compile('^.*(1d50)', re.MULTILINE)
        tmpInterfaces = p.findall(hciResult)
        
        retVal = 0
        
        if (len(tmpInterfaces) > 0):
            for curInterface in tmpInterfaces:
                retVal += 1

        return retVal
        
        
    def getBluetoothInterfaces(printResults=False):
        result = subprocess.run(['hcitool', 'dev'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        
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
            
        exit(0)
        
    bt=SparrowBluetooth()

    # testSpectrum()
