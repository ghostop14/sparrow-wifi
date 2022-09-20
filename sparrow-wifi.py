#!/usr/bin/env python3
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

import sys
import csv
import os
import subprocess
import re
import json
import datetime
from dateutil import parser
import requests
from time import sleep
from threading import Thread, Lock

from PyQt5.QtWidgets import QApplication, QMainWindow,  QDesktopWidget, QGraphicsSimpleTextItem, QFrame, QGraphicsView
from PyQt5.QtWidgets import QMessageBox, QFileDialog, QInputDialog, QLineEdit, QAbstractItemView #, QSplitter
from PyQt5.QtWidgets import QMenu, QAction, QComboBox, QLabel, QPushButton, QCheckBox, QTableWidget,QTableWidgetItem, QHeaderView
#from PyQt5.QtWidgets import QTabWidget, QWidget, QVBoxLayout
from PyQt5.QtChart import QChart, QChartView, QLineSeries, QValueAxis
from PyQt5.QtGui import QPen, QFont, QBrush, QColor, QPainter
# Qt for global colors.  See http://doc.qt.io/qt-5/qt.html#GlobalColor-enum
from PyQt5.QtCore import Qt, QRect, QTimer
from PyQt5.QtGui import QIcon, QRegion
from PyQt5 import QtCore

# from PyQt5.QtCore import QCoreApplication # programmatic quit
from wirelessengine import WirelessEngine, WirelessNetwork
from sparrowcommon import BaseThreadClass, portOpen, stringtobool
from sparrowgps import GPSEngine, GPSStatus, SparrowGPS
from telemetry import TelemetryDialog
from sparrowtablewidgets import IntTableWidgetItem, DateTableWidgetItem, FloatTableWidgetItem
from sparrowmap import MapMarker, MapEngineOSM
from sparrowdialogs import MapSettingsDialog, TelemetryMapSettingsDialog, AgentListenerDialog, GPSCoordDialog
from sparrowdialogs import AgentConfigDialog, RemoteFilesDialog, BluetoothDialog
from sparrowwifiagent import AgentConfigSettings
from sparrowbluetooth import SparrowBluetooth
from sparrowhackrf import SparrowHackrf

# There are some "plugins" that are available for addons.  Let's see if they're present
hasFalcon = False
hasBluetooth = False
hasUbertooth = False

try:
    from manuf import manuf
    hasOUILookup = True
except:
    hasOUILookup = False
    
# ------------------ oui db function -----------------------
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
            print("Error updating the MAC address database.  Please check if the manuf module needs updating with 'sudo pip3 install --upgrade manuf'.")
            ouidb = None
    else:
        ouidb = None
        
    return ouidb
    
# ------------------  Global functions for agent HTTP requests ------------------------------
def makeGetRequest(url, waitTimeout=6):
    try:
        # Not using a timeout can cause the request to hang indefinitely
        response = requests.get(url, timeout=waitTimeout)
    except:
        return -1, ""
        
    if response.status_code != 200:
        return response.status_code, ""
        
    htmlResponse=response.text
    return response.status_code, htmlResponse

# ------------------  GPS requests ------------------------------
def requestRemoteGPS(remoteIP, remotePort):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/gps/status"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            gpsjson = json.loads(responsestr)
            gpsStatus = GPSStatus()
            
            gpsStatus.gpsInstalled = stringtobool(gpsjson['gpsinstalled'])
            gpsStatus.gpsRunning = stringtobool(gpsjson['gpsrunning'])
            gpsStatus.isValid = stringtobool(gpsjson['gpssynch'])
            
            if gpsStatus.isValid:
                # These won't be there if it's not synchronized
                gpsStatus.latitude = float(gpsjson['gpspos']['latitude'])
                gpsStatus.longitude = float(gpsjson['gpspos']['longitude'])
                gpsStatus.altitude = float(gpsjson['gpspos']['altitude'])
                gpsStatus.speed = float(gpsjson['gpspos']['speed'])
                
            return 0, "", gpsStatus
        except:
            return -2, "Error parsing remote agent response", None
    else:
        return -1, "Error connecting to remote agent", None

# ------------------  WiFi scan requests ------------------------------
def requestRemoteNetworks(remoteIP, remotePort, remoteInterface, channelList=None):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/wireless/networks/" + remoteInterface
    
    if (channelList is not None) and (len(channelList) > 0):
        url += "?frequencies="
        for curChannel in channelList:
            url += str(curChannel) + ','
            
    if url.endswith(','):
        url = url[:-1]
        
    # Pass a higher timeout since the scan may take a bit
    statusCode, responsestr = makeGetRequest(url, 20)
    
    if statusCode == 200:
        try:
            networkjson = json.loads(responsestr)
            wirelessNetworks = {}
            
            for curNetDict in networkjson['networks']:
                newNet = WirelessNetwork.createFromJsonDict(curNetDict)
                wirelessNetworks[newNet.getKey()] = newNet
                
            return networkjson['errCode'], networkjson['errString'], wirelessNetworks
        except:
            return -2, "Error parsing remote agent response", None
    else:
        return -1, "Error connecting to remote agent", None

# ------------------  HackRF requests ------------------------------
def remoteHackrfStatus(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/spectrum/hackrfstatus"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            hashackrf = responsedict['hashackrf']
            scan24Running = responsedict['scan24running']
            scan5Running = responsedict['scan5running']
            
            return errcode, errmsg, hashackrf, scan24Running, scan5Running
        except:
            return -1, 'Error parsing response', False, False,  False
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', False, False,  False

def startRemoteSpectrumScan(agentIP, agentPort, scan5):
    if scan5:
        url = "http://" + agentIP + ":" + str(agentPort) + "/spectrum/scanstart5"
    else:
        url = "http://" + agentIP + ":" + str(agentPort) + "/spectrum/scanstart24"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'
        
def stopRemoteSpectrumScan(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/spectrum/scanstop"
    statusCode, responsestr = makeGetRequest(url, 10)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'
            
def getRemoteSpectrumScan(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/spectrum/scanstatus"
    statusCode, responsestr = makeGetRequest(url, 10)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            tmpChannelData = responsedict['channeldata']
            channelData = {}
            for curKey in tmpChannelData.keys():
                channelData[float(curKey)] = float(tmpChannelData[curKey])
            return errcode, errmsg, channelData
        except:
            return -1, 'Error parsing response', None
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', None
        
# ------------------  Bluetooth requests ------------------------------
def remoteHasBluetooth(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/present"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            btPresent = responsedict['hasbluetooth']
            scanRunning = responsedict['scanrunning']
            
            return errcode, errmsg, btPresent, scanRunning
        except:
            return -1, 'Error parsing response', False, False
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', False, False

# Bluetooth Beacon calls
def startRemoteBluetoothBeacon(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/beaconstart"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'
        
def stopRemoteBluetoothBeacon(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/beaconstop"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'

# These scan functions are for the spectrum, not discovery        
def startRemoteBluetoothScan(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/scanstart"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'
        
def stopRemoteBluetoothScan(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/scanstop"
    statusCode, responsestr = makeGetRequest(url, 6)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'
        
def getRemoteBluetoothRunningServices(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/running"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            hasBluetooth = responsedict['hasbluetooth']
            hasUbertooth = responsedict['hasubertooth']
            spectrumScanRunning = responsedict['spectrumscanrunning']
            discoveryScanRunning = responsedict['discoveryscanrunning']
            beaconRunning = responsedict['beaconrunning']
            
            return errcode, errmsg, hasBluetooth, hasUbertooth, spectrumScanRunning, discoveryScanRunning, beaconRunning
        except:
            return -1, 'Error parsing response', False, False, False, False, False
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', False, False, False, False, False
        
def getRemoteBluetoothScanSpectrum(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/scanstatus"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            tmpChannelData = responsedict['channeldata']
            channelData = {}
            for curKey in tmpChannelData.keys():
                channelData[float(curKey)] = float(tmpChannelData[curKey])
            return errcode, errmsg, channelData
        except:
            return -1, 'Error parsing response', None
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', None
        
# ------------------  System interface and config requests ------------------------------
def requestRemoteInterfaces(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/wireless/interfaces"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            interfaces = json.loads(responsestr)
            
            retList = interfaces['interfaces']
            return statusCode, retList
        except:
            return statusCode, None
    else:
        return statusCode, None
        
def requestRemoteConfig(remoteIP, remotePort):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/system/config"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        cfgjson = json.loads(responsestr)
        startupCfg = AgentConfigSettings()
        runningCfg = AgentConfigSettings()
        
        if 'startup' in cfgjson:
            startupCfg.fromJsondict(cfgjson['startup'])
        else:
            return -2, "No startup configuration present in the response", None,  None
            
        if 'running' in cfgjson:
            runningCfg.fromJsondict(cfgjson['running'])
        else:
            return -2, "No running configuration present in the response", None,  None
            
        return 0, "", startupCfg, runningCfg
    else:
        return -1, "Error connecting to remote agent", None, None

# ------------------  GUI Classes ------------------------------

# -----------------  Divider -----------------------------------
class Divider(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.mainWin = parent
        
    def enterEvent(self, event):
        self.setCursor(Qt.SplitVCursor)
        
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            newPos = self.mapToParent(event.pos())
            newPos.setX(1)
            self.move(newPos)
            
            self.mainWin.resizeEvent(event)
        
# ------------------  Hover Line  ------------------------------
class QHoverLineSeries(QLineSeries):
    def __init__(self, parentChart, color=Qt.white):
        super().__init__()
        self.textColor = color
        self.parentChart = parentChart
        
        self.hovered.connect(self.onHover)
        self.callout = Callout(self.name(), parentChart, self.textColor)

        self.callout.setZValue(100)
        self.clicked.connect(self.onClicked)

        self.labelTimer = QTimer()
        self.labelTimerTimeout = 3000
        self.labelTimer.timeout.connect(self.onLabelTimer)
        self.labelTimer.setSingleShot(True)
        self.timerRunning = False
        
    def onLabelTimer(self):
        self.callout.hide()
        self.timerRunning = False
        
    def onClicked(self, point):
        self.callout.show()
        self.timerRunning = True
        self.labelTimer.start(self.labelTimerTimeout)
        
    def onHover(self, point, state):
        if state:
            self.callout.setTextAndPos(self.name(), point)
            # self.callout.setVisible(True)
            self.labelTimer.stop()
            self.callout.show()
        else:
            if not self.timerRunning:
                self.callout.hide()
            
# ------------------  Graphics Callout  ------------------------------
class Callout(QGraphicsSimpleTextItem):
    def __init__(self, displayText, parent, textColor=Qt.white):
        # super().__init__(displayText, parent)
        super().__init__(parent=parent)
        self.textColor = textColor
        self.chartParent = parent
        
        # Pen draws the outline, brush does the character fill
        # The doc says the pen is slow so don't use it unless you have to
        # penBorder = QPen(self.textColor)
        # penBorder.setWidth(2)
        # self.setPen(penBorder)

        newBrush = QBrush(self.textColor)
        self.setBrush(newBrush)
        
    # Has setText() and text() methods
    def setTextAndPos(self, displayText, point):
        self.setText(displayText)
        # self.setText(displayText + " (" + str(round(point.x(), 1)) + "," + str(round(point.y(), 1))+")")
        bR = self.sceneBoundingRect()
        localCoord = self.chartParent.mapToPosition(point)
        # self.setPos(point.x() - bR.width()/2, point.y() - bR.height()/2)
        #self.setPos(point)
        self.setPos(localCoord.x() - bR.width()/2, localCoord.y() - bR.height()/2-7)

# ------------------  Local network scan thread  ------------------------------
class ScanThread(BaseThreadClass):
    def __init__(self, interface, mainWin, channelList=None):
        super().__init__()
        self.interface = interface
        self.mainWin = mainWin
        self.scanDelay = 0.5  # seconds
        self.channelList = channelList
        
    def run(self):
        self.threadRunning = True
        
        while (not self.signalStop):
            # Scan all / normal mode
            if (self.channelList is None) or (len(self.channelList) == 0):
                retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(self.interface)
                if (retCode == 0):
                    # self.statusBar().showMessage('Scan complete.  Found ' + str(len(wirelessNetworks)) + ' networks')
                    if wirelessNetworks and (len(wirelessNetworks) > 0) and (not self.signalStop):
                        self.mainWin.scanresults.emit(wirelessNetworks)
                else:
                        if (retCode != WirelessNetwork.ERR_DEVICEBUSY):
                            self.mainWin.errmsg.emit(retCode, errString)
            
                if (retCode == WirelessNetwork.ERR_DEVICEBUSY):
                    # Shorter sleep for faster results
                    # sleep(0.2)
                    # Switched back for now.  Might be running too fast.
                    sleep(self.scanDelay)
                else:
                    sleep(self.scanDelay)
            else:
                # Channel hunt mode
                for curFrequency in self.channelList:
                    retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(self.interface, curFrequency)
                    if (retCode == 0):
                        # self.statusBar().showMessage('Scan complete.  Found ' + str(len(wirelessNetworks)) + ' networks')
                        if wirelessNetworks and (len(wirelessNetworks) > 0) and (not self.signalStop):
                            self.mainWin.scanresults.emit(wirelessNetworks)
                    else:
                            if (retCode != WirelessNetwork.ERR_DEVICEBUSY):
                                self.mainWin.errmsg.emit(retCode, errString)
                
                    if (retCode == WirelessNetwork.ERR_DEVICEBUSY):
                        # Shorter sleep for faster results
                        # sleep(0.2)
                        # Switched back for now.  Might be running too fast.
                        sleep(self.scanDelay)
                    else:
                        sleep(self.scanDelay)
                    
        self.threadRunning = False

# ------------------  Remote single-shot scan thread  ------------------------------
class remoteSingleShotThread(Thread):
    def __init__(self, interface, mainWin, remoteAgentIP, remoteAgentPort, channelList=None):
        super().__init__()
        self.interface = interface
        self.mainWin = mainWin
        self.huntChannelList = channelList
        self.remoteAgentIP = remoteAgentIP
        self.remoteAgentPort = remoteAgentPort
        
    def run(self):
        self.threadRunning = True
        
        # Run one shot and emit results
        if (not self.huntChannelList) or (len(self.huntChannelList) == 0):
            retCode, errString, wirelessNetworks = requestRemoteNetworks(self.remoteAgentIP, self.remoteAgentPort, self.interface)
        else:
            retCode, errString, wirelessNetworks = requestRemoteNetworks(self.remoteAgentIP, self.remoteAgentPort, self.interface, self.huntChannelList)
        
        mainWin.singleshotscanresults.emit(wirelessNetworks, retCode, errString)
        
        self.threadRunning = False

# ------------------  Remote agent network scan thread  ------------------------------
class RemoteScanThread(BaseThreadClass):
    def __init__(self, interface, mainWin, channelList=None):
        super().__init__()
        self.interface = interface
        self.mainWin = mainWin
        self.scanDelay = 0.5  # seconds
        self.remoteAgentIP = "127.0.0.1"
        self.remoteAgentPort = 8020
        self.channelList = channelList
        
    def run(self):
        self.threadRunning = True
        
        while (not self.signalStop):
            retCode, errString, wirelessNetworks = requestRemoteNetworks(self.remoteAgentIP, self.remoteAgentPort, self.interface, self.channelList)
            if (retCode == 0):
                # self.statusBar().showMessage('Scan complete.  Found ' + str(len(wirelessNetworks)) + ' networks')
                if wirelessNetworks and (len(wirelessNetworks) > 0) and (not self.signalStop):
                    self.mainWin.scanresults.emit(wirelessNetworks)
            else:
                    if (retCode != WirelessNetwork.ERR_DEVICEBUSY):
                        self.mainWin.errmsg.emit(retCode, errString)
            
            if (retCode == WirelessNetwork.ERR_DEVICEBUSY):
                # Shorter sleep for faster results
                sleep(0.2)
            else:
                sleep(self.scanDelay)
            
        self.threadRunning = False

# ------------------  GPSEngine override onGPSResult to notify the main window when the GPS goes synchnronized  ------------------------------
class GPSEngineNotifyWin(GPSEngine):
    def __init__(self, mainWin):
        super().__init__()
        self.mainWin = mainWin
        self.isSynchronized = False

    def onGPSResult(self, gpsResult):
        super().onGPSResult(gpsResult)

        if self.isSynchronized != gpsResult.isValid:
            # Allow GPS to sync / de-sync and notify
            self.isSynchronized = gpsResult.isValid
            self.mainWin.gpsSynchronizedsignal.emit()

# ------------------  Global color list that we'll cycle through  ------------------------------
orange = QColor(255,165,0)
colors = [Qt.black, Qt.red, Qt.darkRed, Qt.green, Qt.darkGreen, Qt.blue, orange, Qt.darkBlue, Qt.cyan, Qt.darkCyan, Qt.magenta, Qt.darkMagenta, Qt.darkGray]

# ------------------  Main Application Window  ------------------------------
class mainWindow(QMainWindow):
    
    # Notify signals
    resized = QtCore.pyqtSignal()
    scanresults = QtCore.pyqtSignal(dict)
    singleshotscanresults = QtCore.pyqtSignal(dict, int, str)
    scanresultsfromadvanced = QtCore.pyqtSignal(dict)
    errmsg = QtCore.pyqtSignal(int, str)
    gpsSynchronizedsignal = QtCore.pyqtSignal()
    advScanClosed = QtCore.pyqtSignal()
    advScanUpdateSSIDs = QtCore.pyqtSignal(dict)
    agentListenerClosed = QtCore.pyqtSignal()
    bluetoothDiscoveryClosed = QtCore.pyqtSignal()
    rescanInterfaces = QtCore.pyqtSignal()
    
    # For help with qt5 GUI's this is a great tutorial:
    # http://zetcode.com/gui/pyqt5/

    def checkForBluetooth(self):
        self.hasBluetooth = False
        self.hasUbertooth = False
        self.hasRemoteBluetooth = False
        self.hasRemoteUbertooth = False
        
        numBtAdapters = len(SparrowBluetooth.getBluetoothInterfaces())
        if numBtAdapters > 0:
            self.hasBluetooth = True
        
        if SparrowBluetooth.getNumUbertoothDevices() > 0:
            #SparrowBluetooth.ubertoothStopSpecan()
            errcode, errmsg = SparrowBluetooth.hasUbertoothTools()
            # errcode, errmsg = SparrowBluetooth.ubertoothOnline()
            if errcode == 0:
                self.hasUbertooth = True
        
        if self.hasBluetooth or self.hasUbertooth:
            self.bluetooth = SparrowBluetooth()
        else:
            self.bluetooth = None

    def __init__(self):
        super().__init__()

        self.rescanInterfaces.connect(self.onRescanInterfaces)
        
        self.hackrf = SparrowHackrf()
        self.hackrfShowSpectrum24 = False
        self.hackrfLastSpectrumState24 = False
        self.hackrfShowSpectrum5 = False
        self.hackrfLastSpectrumState5 = False
        self.hackrfSpectrumTimer = QTimer()
        self.hackrfSpectrumTimeoutLocal = 100
        self.hackrfSpectrumTimeoutRemote = 200
        self.hackrfSpectrumTimer.timeout.connect(self.onHackrfSpectrumTimer)
        self.hackrfSpectrumTimer.setSingleShot(True)

        self.spectrum24Line = None
        self.spectrum5Line = None
        
        self.checkForBluetooth()
        
        self.bluetoothWin = None
        self.bluetoothDiscoveryClosed.connect(self.onBtDiscoveryClosed)
        
        self.btSpectrumGain = 1.0
        
        self.btShowSpectrum = False
        self.btLastSpectrumState = False
        self.btLastBeaconState = False
        self.btBeacon = False
        self.btSpectrumTimer = QTimer()
        self.btSpectrumTimeoutLocal = 100
        self.btSpectrumTimeoutRemote = 200
        self.btSpectrumTimer.timeout.connect(self.onSpectrumTimer)
        self.btSpectrumTimer.setSingleShot(True)
        
        self.ouiLookupEngine = getOUIDB()
            
        self.agentListenerWindow = None
        self.agentListenerClosed.connect(self.onAgentListenerClosed)
        
        self.telemetryWindows = {}
        self.advancedScan = None
        
        self.scanMode="Normal"
        self.huntChannelList = []
        
        # GPS engine
        self.gpsEngine = GPSEngineNotifyWin(self)
        self.gpsSynchronized = False
        self.gpsSynchronizedsignal.connect(self.onGPSSyncChanged)
        
        self.gpsCoordWindow = None
        
        # Advanced Scan
        self.advScanClosed.connect(self.onAdvancedScanClosed)
        self.advScanUpdateSSIDs.connect(self.onAdvScanUpdateSSIDs)
        
        # Local network scan
        self.scanRunning = False
        self.scanIsBlocking = False
        
        self.nextColor = 0
        self.lastSeries = None

        self.updateLock = Lock()
        self.scanThread = None
        self.scanDelay = 0.5
        self.scanresults.connect(self.scanResults)
        self.singleshotscanresults.connect(self.onSingleShotScanResults)
        self.scanresultsfromadvanced.connect(self.scanResultsFromAdvanced)
        self.errmsg.connect(self.onErrMsg)
        
        # Remote Scans
        self.remoteAgentIP = ''
        self.remoteAgentPort = 8020
        self.remoteAutoUpdates = True
        self.remoteScanRunning = False
        self.remoteScanIsBlocking = False
        self.remoteScanThread = None
        self.remoteSingleShotThread = None
        self.remoteScanDelay = 0.5
        self.lastRemoteState = False
        self.remoteAgentUp = False
        self.remoteHasHackrf = False
        
        self.missedAgentCycles = 0
        self.allowedMissedAgentCycles = 1
        
        desktopSize = QApplication.desktop().screenGeometry()
        #self.mainWidth=1024
        #self.mainHeight=768
        self.mainWidth = desktopSize.width() * 3 // 4
        self.mainHeight = desktopSize.height() * 3 // 4
        
        self.initUI()
        
        if os.geteuid() != 0:
            self.runningAsRoot = False
            self.statusBar().showMessage('You need to have root privileges to run local scans.  Please exit and rerun it as root')
            print("You need to have root privileges to run this script.\nPlease try again, this time using 'sudo'. Exiting.")
            QMessageBox.question(self, 'Warning',"You need to have root privileges to run local scans.", QMessageBox.Ok)

            #self.close()
        else:
            self.runningAsRoot = True
    
    def ouiLookup(self, macAddr):
        clientVendor = ""
        
        if hasOUILookup:
            try:
                if self.ouiLookupEngine:
                    clientVendor = self.ouiLookupEngine.get_manuf(macAddr)
                    if clientVendor is None:
                        clientVendor = ''
            except:
                clientVendor = ""
            
        return clientVendor
        
    def initUI(self):
        # self.setGeometry(10, 10, 800, 600)
        self.resize(self.mainWidth, self.mainHeight)
        self.center()
        self.setWindowTitle('Sparrow-WiFi Analyzer')
        self.setWindowIcon(QIcon('wifi_icon.png'))        

        self.createMenu()
        
        self.createControls()

        #self.splitter1 = QSplitter(Qt.Vertical)
        #self.splitter2 = QSplitter(Qt.Horizontal)
        #self.splitter1.addWidget(self.networkTable)
        #self.splitter1.addWidget(self.splitter2)
        #self.splitter2.addWidget(self.Plot24)
        #self.splitter2.addWidget(self.Plot5)
        
        self.setBlackoutColors()
        
        self.setMinimumWidth(800)
        self.setMinimumHeight(400)
        
        self.show()
        
        # Set up GPS check timer
        self.gpsTimer = QTimer()
        self.gpsTimer.timeout.connect(self.onGPSTimer)
        self.gpsTimer.setSingleShot(True)
        
        self.gpsTimerTimeout = 5000
        self.gpsTimer.start(self.gpsTimerTimeout)   # Check every 5 seconds

    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()
        
        if self.initializingGUI:
            self.horizontalDivider.setGeometry(1, int(size.height()/2+2), size.width()-2, 4)

            self.networkTable.setGeometry(10, 103, size.width()-20, int(size.height()/2)-105)
            self.Plot24.setGeometry(10, int(size.height()/2)+10, int(size.width()/2)-10, int(size.height()/2)-40)
            self.Plot5.setGeometry(int(size.width()/2)+5, int(size.height()/2)+10, int(size.width()/2)-15, int(size.height()/2)-40)
            
            self.initializingGUI = False
        
        # self.splitter1.setGeometry(10, 103, size.width()-20, size.height()-20)
        
        if size.width() < 800:
            self.setGeometry(size.x(), size.y(), 800, size.height())

        size = self.geometry()
        
        self.lblGPS.move(size.width()-90, 30)
        self.btnGPSStatus.move(size.width()-50, 34)
            
        dividerPos = self.horizontalDivider.pos()
        if dividerPos.y() < 200:
            dividerPos.setY(200)
        elif dividerPos.y() > size.height()-180:
            dividerPos.setY(size.height()-180)
            
        self.horizontalDivider.setGeometry(dividerPos.x(), dividerPos.y(), size.width()-2, 5)
        self.networkTable.setGeometry(10, 103, size.width()-20, dividerPos.y()-105)
        self.Plot24.setGeometry(10, dividerPos.y()+6, int(size.width()/2)-10, size.height()-dividerPos.y()-30)
        self.Plot5.setGeometry(int(size.width()/2)+5, dividerPos.y()+6,int(size.width()/2)-15, size.height()-dividerPos.y()-30)

    def createControls(self):
        # self.statusBar().setStyleSheet("QStatusBar{background:rgba(204,229,255,255);color:black;border: 1px solid blue; border-radius: 1px;}")
        self.statusBar().setStyleSheet("QStatusBar{background:rgba(192,192,192,255);color:black;border: 1px solid blue; border-radius: 1px;}")
        if GPSEngine.GPSDRunning():
            self.gpsEngine.start()
            self.statusBar().showMessage('Local gpsd Found.  System Ready.')
        else:
            self.statusBar().showMessage('Note: No local gpsd running.  System Ready.')


        # Interface droplist
        self.lblInterface = QLabel("Local Interface", self)
        self.lblInterface.setGeometry(5, 30, 120, 30)
        
        self.combo = QComboBox(self)
        self.combo.move(130, 30)

        interfaces=WirelessEngine.getInterfaces()
        
        if (len(interfaces) > 0):
            for curInterface in interfaces:
                self.combo.addItem(curInterface)
        else:
            self.statusBar().showMessage('No wireless interfaces found.')

        self.combo.activated[str].connect(self.onInterface)        
        
        # Scan Button
        self.btnScan = QPushButton("&Scan", self)
        self.btnScan.setCheckable(True)
        self.btnScan.setShortcut('Ctrl+S')
        self.btnScan.setStyleSheet("background-color: rgba(0,128,192,255); border: none;")
        self.btnScan.move(260, 30)
        self.btnScan.clicked[bool].connect(self.onScanClicked)
        
        # Scan Mode
        self.lblScanMode = QLabel("Scan Mode:", self)
        self.lblScanMode.setGeometry(380, 30, 120, 30)
        
        self.scanModeCombo = QComboBox(self)
        self.scanModeCombo.setStatusTip('All-channel normal scans can take 5-10 seconds per sweep.  Use Hunt mode for faster response time on a selected channel.')
        self.scanModeCombo.move(455, 30)
        self.scanModeCombo.addItem("Normal")
        self.scanModeCombo.addItem("Hunt")
        self.scanModeCombo.currentIndexChanged.connect(self.onScanModeChanged)
        
        self.lblScanMode = QLabel("Hunt Channel or Frequencies(s):", self)
        self.lblScanMode.setGeometry(565, 30, 200, 30)
        self.huntChannels = QLineEdit(self)
        self.huntChannels.setStatusTip('Channels or center frequencies can be specified.  List should be comma-separated.')
        self.huntChannels.setGeometry(763, 30, 100, 30)
        self.huntChannels.setText('1')

        # Hide them to start
        self.huntChannels.setVisible(False)
        self.lblScanMode.setVisible(False)
        
        # Age out checkbox
        self.cbAgeOut = QCheckBox(self)
        self.cbAgeOut.move(10, 70)
        self.lblAgeOut = QLabel("Remove networks not seen in the past 3 minutes", self)
        self.lblAgeOut.setGeometry(30, 70, 300, 30)
        
        # Network Table
        self.networkTable = QTableWidget(self)
        self.networkTable.setColumnCount(14)
        # self.networkTable.setGeometry(10, 100, self.mainWidth-60, self.mainHeight/2-105)
        self.networkTable.setShowGrid(True)
        # self.networkTable.setHorizontalHeaderLabels(['macAddr', 'vendor','SSID', 'Security', 'Privacy', 'Channel', 'Frequency', 'Signal Strength', 'Bandwidth', 'Last Seen', 'First Seen', 'GPS'])
        self.networkTable.setHorizontalHeaderLabels(['macAddr', 'vendor','SSID', 'Security', 'Privacy', 'Channel', 'Frequency', 'Signal Strength', 'Bandwidth', '% Utilization','Stations','Last Seen', 'First Seen', 'GPS'])
        self.networkTable.resizeColumnsToContents()
        self.networkTable.setRowCount(0)
        self.networkTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        self.networkTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
        self.networkTable.cellClicked.connect(self.onTableClicked)
        
        self.networkTable.setSelectionMode( QAbstractItemView.SingleSelection )
        self.networkTable.itemSelectionChanged.connect(self.onNetworkTableSelectionChanged)
        self.networkTableSortOrder = Qt.DescendingOrder
        self.networkTableSortIndex = -1
        
        # Set flag for first time
        self.initializingGUI = True
        
        # Set divider
        self.horizontalDivider = Divider(self)
        self.horizontalDivider.setFrameShape(QFrame.HLine)
        self.horizontalDivider.setFrameShadow(QFrame.Sunken)
        
        # Network Table right-click menu
        self.ntRightClickMenu = QMenu(self)
        newAct = QAction('Telemetry', self)        
        newAct.setStatusTip('View network telemetry data')
        newAct.triggered.connect(self.onShowTelemetry)
        self.ntRightClickMenu.addAction(newAct)
        
        self.ntRightClickMenu.addSeparator()
        
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopyNet)
        self.ntRightClickMenu.addAction(newAct)
        
        self.ntRightClickMenu.addSeparator()
        
        newAct = QAction('Delete', self)        
        newAct.setStatusTip('Remove network from the list')
        newAct.triggered.connect(self.onDeleteNet)
        self.ntRightClickMenu.addAction(newAct)
        
        # Attach it to the table
        self.networkTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.networkTable.customContextMenuRequested.connect(self.showNTContextMenu)
        
        self.createCharts()
        
        # GPS Indicator
        self.lblGPS = QLabel("GPS:", self)
        self.lblGPS.move(850, 30)
        
        rect = QRect(0,0,20,20)
        region = QRegion(rect,QRegion.Ellipse)
        self.btnGPSStatus = QPushButton("", self)
        self.btnGPSStatus.move(900, 34)
        self.btnGPSStatus.setFixedWidth(30)
        self.btnGPSStatus.setFixedHeight(30)
        self.btnGPSStatus.setMask(region)
        self.btnGPSStatus.clicked.connect(self.onGPSStatusIndicatorClicked)
        
        if GPSEngine.GPSDRunning():
            if self.gpsEngine.gpsValid():
                self.btnGPSStatus.setStyleSheet("background-color: green; border: 1px;")
            else:
                self.btnGPSStatus.setStyleSheet("background-color: yellow; border: 1px;")
        else:
            self.btnGPSStatus.setStyleSheet("background-color: red; border: 1px;")

        
    def setBlackoutColors(self):
        global colors
        global orange
        colors = [Qt.red, Qt.yellow, Qt.darkYellow, Qt.green, Qt.darkGreen, orange, Qt.blue,Qt.cyan, Qt.darkCyan, Qt.magenta, Qt.darkMagenta, Qt.gray]

        # return
        # self.setStyleSheet("background-color: black")
        #self.Plot24.setBackgroundBrush(Qt.black)
        mainTitleBrush = QBrush(Qt.red)
        self.chart24.setTitleBrush(mainTitleBrush)
        self.chart5.setTitleBrush(mainTitleBrush)
        
        self.chart24.setBackgroundBrush(QBrush(Qt.black))
        self.chart24.axisX().setLabelsColor(Qt.white)
        self.chart24.axisY().setLabelsColor(Qt.white)
        titleBrush = QBrush(Qt.white)
        self.chart24.axisX().setTitleBrush(titleBrush)
        self.chart24.axisY().setTitleBrush(titleBrush)
        #self.Plot5.setBackgroundBrush(Qt.black)
        self.chart5.setBackgroundBrush(QBrush(Qt.black))
        self.chart5.axisX().setLabelsColor(Qt.white)
        self.chart5.axisY().setLabelsColor(Qt.white)
        self.chart5.axisX().setTitleBrush(titleBrush)
        self.chart5.axisY().setTitleBrush(titleBrush)
        
        #$ self.networkTable.setStyleSheet("QTableCornerButton::section{background-color: white;}")
        # self.networkTable.cornerWidget().setStylesheet("background-color: black")
        self.networkTable.setStyleSheet("QTableView {background-color: black;gridline-color: white;color: white} QTableCornerButton::section{background-color: white;}")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black;} QHeaderView::down-arrow,QHeaderView::up-arrow {background: none;}"
        self.networkTable.horizontalHeader().setStyleSheet(headerStyle)
        self.networkTable.verticalHeader().setStyleSheet(headerStyle)
        
    def createMenu(self):
        # Create main menu bar
        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&File')
        
        # Create File Menu Items
        newAct = QAction('&New', self)        
        newAct.setShortcut('Ctrl+N')
        newAct.setStatusTip('Clear List')
        newAct.triggered.connect(self.onClearData)
        fileMenu.addAction(newAct)

        # import
        importMenu = fileMenu.addMenu('&Import')

        newAct = QAction('&Saved CSV', self)        
        newAct.setStatusTip('Import from saved CSV')
        newAct.triggered.connect(self.onImportCSV)
        importMenu.addAction(newAct)
        
        newAct = QAction('&Saved JSON', self)        
        newAct.setStatusTip('Import from saved JSON')
        newAct.triggered.connect(self.onImportJSON)
        importMenu.addAction(newAct)

        importMenu.addSeparator()
        
        newAct = QAction('&Import iw scan (iw dev <interface> scan > <file>)', self)        
        newAct.setStatusTip("Run 'iw dev <wireless interface> scan > <somefile>' and import the data directly with this option")
        newAct.triggered.connect(self.onImportIWData)
        importMenu.addAction(newAct)
        
        # export
        exportMenu = fileMenu.addMenu('&Export')
        newAct = QAction('&To CSV', self)        
        newAct.setStatusTip('Export to CSV')
        newAct.triggered.connect(self.onExportCSV)
        exportMenu.addAction(newAct)
        
        newAct = QAction('&To JSON', self)        
        newAct.setStatusTip('Export to JSON')
        newAct.triggered.connect(self.onExportJSON)
        exportMenu.addAction(newAct)
        
        # exitAct = QAction(QIcon('exit.png'), '&Exit', self)        
        exitAct = QAction('&Exit', self)        
        exitAct.setShortcut('Ctrl+X')
        exitAct.setStatusTip('Exit application')
        exitAct.triggered.connect(self.close)
        fileMenu.addAction(exitAct)

        # Agent Menu Items
        helpMenu = menubar.addMenu('&Agent')
        self.menuRemoteAgent = QAction('Connect to Remote Agent', self)        
        self.menuRemoteAgent.setStatusTip('Use a Remote Agent')
        self.menuRemoteAgent.setCheckable(True)
        self.menuRemoteAgent.changed.connect(self.onRemoteAgent)
        helpMenu.addAction(self.menuRemoteAgent)

        self.menuRemoteFiles = QAction('Remote Recordings', self)        
        self.menuRemoteFiles.setStatusTip('Get/Manage remote recordings')
        self.menuRemoteFiles.triggered.connect(self.onRemoteFiles)
        helpMenu.addAction(self.menuRemoteFiles)
        
        helpMenu.addSeparator()
        
        self.menuRemoteAgentListener = QAction('Agent Discovery', self)        
        self.menuRemoteAgentListener.setStatusTip('Listen for remote agents')
        self.menuRemoteAgentListener.triggered.connect(self.onRemoteAgentListener)
        helpMenu.addAction(self.menuRemoteAgentListener)
        
        helpMenu.addSeparator()
        
        self.menuRemoteAgentConfig = QAction('Agent Configuration', self)        
        self.menuRemoteAgentConfig.setStatusTip('Configure a remote agent')
        self.menuRemoteAgentConfig.triggered.connect(self.onRemoteAgentConfig)
        helpMenu.addAction(self.menuRemoteAgentConfig)
        
        # GPS Menu Items
        gpsMenu = menubar.addMenu('&Geo')
        newAct = QAction('Create Access Point Map', self)        
        newAct.setStatusTip('Plot access point coordinates from the table on a Google map')
        newAct.triggered.connect(self.onGoogleMap)
        gpsMenu.addAction(newAct)
        
        newAct = QAction('Create SSID Map from Telemetry', self)        
        newAct.setStatusTip('Plot coordinates for a single SSID saved from telemetry window on a Google map')
        newAct.triggered.connect(self.onGoogleMapTelemetry)
        gpsMenu.addAction(newAct)
        
        gpsMenu.addSeparator()
        
        # This has been hidden for now.  Really didn't do anything new since this is covered with the timer now
        newAct = QAction('GPS Status', self)        
        newAct.setStatusTip('Show GPS Status')
        newAct.triggered.connect(self.onGPSStatus)
        newAct.setVisible(False) 
        gpsMenu.addAction(newAct)
            
        newAct = QAction('GPS Coordinate Monitoring', self)        
        newAct.setStatusTip('Show GPS Coordinates')
        newAct.triggered.connect(self.onGPSCoordinates)
        gpsMenu.addAction(newAct)
        
        if self.hasXGPS():
            gpsMenu.addSeparator()
            newAct = QAction('Launch XGPS - Local', self)        
            newAct.setStatusTip('Show GPS GUI against local gpsd')
            newAct.triggered.connect(self.onXGPSLocal)
            gpsMenu.addAction(newAct)
        
            newAct = QAction('Launch XGPS - Remote', self)        
            newAct.setStatusTip('Show GPS GUI against remote gpsd')
            newAct.triggered.connect(self.onXGPSRemote)
            gpsMenu.addAction(newAct)
            
        # View Menu Items
        ViewMenu = menubar.addMenu('&Telemetry')
        newAct = QAction('Telemetry For Selected Network', self)        
        newAct.setStatusTip('Show screen for selected network with history, signal strength, and historical data')
        newAct.triggered.connect(self.onShowTelemetry)
        ViewMenu.addAction(newAct)
        
        # Bluetooth
        ViewMenu = menubar.addMenu('&Bluetooth')
        self.menuBtBeacon = QAction('iBeacon Mode', self)        
        self.menuBtBeacon.setStatusTip("Become an iBeacon")
        self.menuBtBeacon.setCheckable(True)
        self.menuBtBeacon.triggered.connect(self.onBtBeacon)
        ViewMenu.addAction(self.menuBtBeacon)

        ViewMenu.addSeparator()
        
        self.menuBtBluetooth = QAction('Bluetooth Discovery', self)        
        self.menuBtBluetooth.setStatusTip("Find bluetooth devices.  Basic discovery or Ubertooth depending on hardware.")
        self.menuBtBluetooth.triggered.connect(self.onBtBluetooth)
        ViewMenu.addAction(self.menuBtBluetooth)

        # Spectrum
        SpectrumMenu = menubar.addMenu('&Spectrum')
        self.menuBtSpectrumGain = QAction('Spectrum Analyzer Gain', self)        
        self.menuBtSpectrumGain.setStatusTip("Manually override gain to better match spectrum with wifi adapter")
        self.menuBtSpectrumGain.triggered.connect(self.onBtSpectrumOverrideGain)
        SpectrumMenu.addAction(self.menuBtSpectrumGain)
        
        SpectrumMenu.addSeparator()
        
        self.menuBtSpectrum = QAction('2.4 GHz Spectrum Analyzer via Ubertooth', self)        
        self.menuBtSpectrum.setStatusTip("Use Ubertooth for 2.4 GHz spectrum analyzer. NOTE: Wireless cards may have different gain, so results may vary.")
        self.menuBtSpectrum.setCheckable(True)
        self.menuBtSpectrum.triggered.connect(self.onBtSpectrumAnalyzer)
        SpectrumMenu.addAction(self.menuBtSpectrum)
        
        self.menuHackrfSpectrum24 = QAction('2.4 GHz Spectrum Analyzer via HackRF', self)        
        self.menuHackrfSpectrum24.setStatusTip("Use HackRF for 2.4 GHz spectrum analyzer. NOTE: Wireless cards may have different gain, so results may vary.")
        self.menuHackrfSpectrum24.setCheckable(True)
        self.menuHackrfSpectrum24.triggered.connect(self.onHackrfSpectrumAnalyzer24)
        SpectrumMenu.addAction(self.menuHackrfSpectrum24)
        
        SpectrumMenu.addSeparator()
        
        self.menuHackrfSpectrum5 = QAction('5 GHz Spectrum Analyzer via HackRF', self)        
        self.menuHackrfSpectrum5.setStatusTip("Use HackRF for 5 GHz spectrum analyzer. NOTE: Wireless cards may have different gain, so results may vary.")
        self.menuHackrfSpectrum5.setCheckable(True)
        self.menuHackrfSpectrum5.triggered.connect(self.onHackrfSpectrumAnalyzer5)
        SpectrumMenu.addAction(self.menuHackrfSpectrum5)
        
        if not self.hackrf.hasHackrf:
            self.menuHackrfSpectrum24.setEnabled(False)
            self.menuHackrfSpectrum5.setEnabled(False)
            
        self.setBluetoothMenu()
        
        # Falcon
        if hasFalcon:
            # Falcon Menu Items
            ViewMenu = menubar.addMenu('&Falcon')
            if hasFalcon:
                newAct = QAction('Advanced Scan', self)        
                newAct.setStatusTip("Run a scan to find hidden SSID's and client stations")
                newAct.triggered.connect(self.onAdvancedScan)
                ViewMenu.addAction(newAct)

        # Help Menu Items
        helpMenu = menubar.addMenu('&Help')
        newAct = QAction('About', self)        
        newAct.setStatusTip('About')
        newAct.triggered.connect(self.onAbout)
        helpMenu.addAction(newAct)

    def hasXGPS(self):
        if (os.path.isfile('/usr/bin/xgps') or os.path.isfile('/usr/local/bin/xgps')):
            return True
        else:
            return False
        
    def setBluetoothMenu(self):
        self.menuBtBeacon.setEnabled(True)
        self.menuBtSpectrum.setEnabled(True)
        self.menuBtSpectrumGain.setEnabled(True)
        
        if not self.remoteAgentUp:
            # Local
            if not self.hasUbertooth:
                self.menuBtSpectrum.setEnabled(False)
                # self.menuBtSpectrumGain.setEnabled(False)
            if not self.hasBluetooth:
                self.menuBtBluetooth.setEnabled(False)
                self.menuBtBeacon.setEnabled(False)
        else:
            if not self.hasRemoteUbertooth:
                self.menuBtSpectrum.setEnabled(False)
                # self.menuBtSpectrumGain.setEnabled(False)
            if not self.hasRemoteBluetooth:
                self.menuBtBluetooth.setEnabled(False)
                self.menuBtBeacon.setEnabled(False)
            
    def createCharts(self):
        self.chart24 = QChart()
        self.chart24.setAcceptHoverEvents(True)
        titleFont = QFont()
        titleFont.setPixelSize(18)
        titleBrush = QBrush(QColor(0, 0, 255))
        self.chart24.setTitleFont(titleFont)
        self.chart24.setTitleBrush(titleBrush)
        self.chart24.setTitle('2.4 GHz')
        self.chart24.legend().hide()
        
        # Axis examples: https://doc.qt.io/qt-5/qtcharts-multiaxis-example.html
        self.chart24axisx = QValueAxis()
        self.chart24axisx.setMin(0)
        self.chart24axisx.setMax(16)
        self.chart24axisx.setTickCount(17) # Axis count +1 (at 0 crossing)
        self.chart24axisx.setLabelFormat("%d")
        self.chart24axisx.setTitleText("Channel")
        self.chart24.addAxis(self.chart24axisx, Qt.AlignBottom)
        
        self.chart24yAxis = QValueAxis()
        self.chart24yAxis.setMin(-100)
        self.chart24yAxis.setMax(-10)
        self.chart24yAxis.setTickCount(9)
        self.chart24yAxis.setLabelFormat("%d")
        self.chart24yAxis.setTitleText("dBm")
        self.chart24.addAxis(self.chart24yAxis, Qt.AlignLeft)
        
        chartBorder = Qt.darkGray
        self.Plot24 = QChartView(self.chart24, self)
        self.Plot24.setBackgroundBrush(chartBorder)
        self.Plot24.setRenderHint(QPainter.Antialiasing)

        self.chart5 = QChart()
        self.chart5.setAcceptHoverEvents(True)
        self.chart5.setTitleFont(titleFont)
        self.chart5.setTitleBrush(titleBrush)
        self.chart5.setTitle('5 GHz')
        self.chart5.createDefaultAxes()
        self.chart5.legend().hide()
        
        self.chart5axisx = QValueAxis()
        self.chart5axisx .setMin(30)
        self.chart5axisx .setMax(170)
        self.chart5axisx .setTickCount(15)
        self.chart5axisx .setLabelFormat("%d")
        self.chart5axisx .setTitleText("Channel")
        self.chart5.addAxis(self.chart5axisx , Qt.AlignBottom)
        
        newAxis = QValueAxis()
        newAxis.setMin(-100)
        newAxis.setMax(-10)
        newAxis.setTickCount(9)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("dBm")
        self.chart5.addAxis(newAxis, Qt.AlignLeft)
        
        self.Plot5 = QChartView(self.chart5, self)
        self.Plot5.setBackgroundBrush(chartBorder)
        self.Plot5.setRenderHint(QPainter.Antialiasing)
        self.Plot5.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
    
    def onBtSpectrumOverrideGain(self):
        text, okPressed = QInputDialog.getText(self, "Spectrum Analyzer Gain","Enter a gain to apply to the spectrum:", 
                                    QLineEdit.Normal, str(self.btSpectrumGain))
        if okPressed and text != '':
            try:
                newGain = float(text)
                if newGain <= 0:
                    QMessageBox.question(self, 'Error',"Invalid gain setting (gain must be greater then zero)", QMessageBox.Ok)
                else:
                    self.btSpectrumGain = newGain
            except:
                QMessageBox.question(self, 'Error',"Could not convert " + text + " to a number.", QMessageBox.Ok)

    def createSpectrumLine(self):
        if not self.spectrum24Line:
            # add it
            
            # 2.4 GHz
            self.spectrum24Line = self.createNewSeries(Qt.white, self.chart24)
            self.spectrum24Line.setName('Spectrum')
            
            self.chart24.addSeries(self.spectrum24Line)
            self.spectrum24Line.attachAxis(self.chart24.axisX())
            self.spectrum24Line.attachAxis(self.chart24.axisY())

        if not self.spectrum5Line:
            # 5 GHz
            self.spectrum5Line = self.createNewSeries(Qt.white, self.chart5)
            self.spectrum5Line.setName('Spectrum')
            
            self.chart5.addSeries(self.spectrum5Line)
            self.spectrum5Line.attachAxis(self.chart5.axisX())
            self.spectrum5Line.attachAxis(self.chart5.axisY())
            # self.spectrum5Line.setUseOpenGL(True) # This causes the entire window to go blank
            
    def onBtBeacon(self):
        if (self.menuBtBeacon.isChecked() == self.btLastBeaconState):
            # There's an extra bounce in this for some reason.
            return

        if (not self.remoteAgentUp):
            # Local
            if self.bluetooth.discoveryRunning():
                reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for discovery.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                if reply == QMessageBox.Yes:
                    self.bluetooth.stopDiscovery()
                else:
                    self.menuBtBeacon.setChecked(False)
                    self.btBeacon = False
                    self.btLastBeaconState = False
                    return
            
            if self.btShowSpectrum:
                reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for spectrum.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                if reply == QMessageBox.Yes:
                    self.stopSpectrum24Line()
                else:
                    self.menuBtBeacon.setChecked(False)
                    self.btBeacon = False
                    self.btLastBeaconState = False
                    return
                    
            self.btBeacon = self.menuBtBeacon.isChecked()
            self.btLastBeaconState = self.btBeacon
            
            if self.btBeacon:
                self.bluetooth.startBeacon()
            else:
                self.bluetooth.stopBeacon()
        else:
            # Remote
            errcode, errmsg, self.hasRemoteBluetooth, self.hasRemoteUbertooth, scanRunning, discoveryRunning, beaconRunning = getRemoteBluetoothRunningServices(self.remoteAgentIP, self.remoteAgentPort)
            
            if errcode == 0:
                if discoveryRunning:
                    reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for discovery.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                    if reply == QMessageBox.Yes:
                        pass
                        # Stop remote discovery here
                    else:
                        self.menuBtBeacon.setChecked(False)
                        self.btBeacon = False
                        self.btLastBeaconState = False
                        return

            if self.btShowSpectrum:
                reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for spectrum.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.stopSpectrum24Line()
                    errcode, errmsg = stopRemoteBluetoothScan(self.remoteAgentIP, self.remoteAgentPort)
                    if errcode != 0:
                        self.statusBar().showMessage(errmsg)
                        self.menuBtSpectrum.setChecked(False)
                        self.btShowSpectrum = False
                        self.btLastSpectrumState = False
                        self.btSpectrumTimer.stop()
                        return
                else:
                    self.menuBtBeacon.setChecked(False)
                    self.btBeacon = False
                    self.btLastBeaconState = False
                    return
                
            self.btBeacon = self.menuBtBeacon.isChecked()
            self.btLastBeaconState = self.btBeacon

            if self.btBeacon:
                # Call agent here
                errcode, errmsg = startRemoteBluetoothBeacon(self.remoteAgentIP, self.remoteAgentPort)
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
                    self.menuBtBeacon.setChecked(False)
                    self.btBeacon = False
                    self.btLastBeaconState = False
            else:
                # Call agent here
                errcode, errmsg = stopRemoteBluetoothBeacon(self.remoteAgentIP, self.remoteAgentPort)
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
                    self.menuBtBeacon.setChecked(False)
                    self.btBeacon = False
                    self.btLastBeaconState = False
                
    def onBtBluetooth(self):
        if not self.bluetoothWin:
            # Check if we have other bluetooth functions running, if so give the user a chance to decide what to do
            # since only 1 can use the card at a time
            if not self.remoteAgentUp:
                # Local
                if self.bluetooth.scanRunning():
                    reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for spectrum scanning.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                    if reply == QMessageBox.Yes:
                        self.bluetooth.stopScanning()
                        self.stopSpectrum24Line()
                    else:
                        return
                        
                if self.btBeacon:
                    reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for beaconing.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                    if reply == QMessageBox.Yes:
                        self.bluetooth.stopBeacon()
                    else:
                        return
            else:
                errcode, errmsg, self.hasRemoteBluetooth, self.hasRemoteUbertooth, scanRunning, discoveryRunning, beaconRunning = getRemoteBluetoothRunningServices(self.remoteAgentIP, self.remoteAgentPort)
                if scanRunning:
                    reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for spectrum scanning.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                    if reply == QMessageBox.Yes:
                        errcode, errmsg = stopRemoteBluetoothScan(self.remoteAgentIP, self.remoteAgentPort)
                        self.stopSpectrum24Line()
                    else:
                        return

            # We're good so let's create the window
            
            # Create a window, we don't have one yet
            self.bluetoothWin = BluetoothDialog(self, self.bluetooth, self.remoteAgentUp, self.remoteAgentIP, self.remoteAgentPort, None)  # Need to set parent to None to allow it to not always be on top

        self.bluetoothWin.show()
        self.bluetoothWin.activateWindow()

    def onBtDiscoveryClosed(self):
        if self.bluetoothWin:
            self.bluetoothWin = None
            
    def onHackrfSpectrumAnalyzer24(self):
        if (self.menuHackrfSpectrum24.isChecked() == self.hackrfLastSpectrumState24):
            # There's an extra bounce in this for some reason.
            return

        self.hackrfShowSpectrum24 = self.menuHackrfSpectrum24.isChecked()
        self.hackrfLastSpectrumState24 = self.hackrfShowSpectrum24
        
        if self.hackrfShowSpectrum24:
            # Enable it
            # Add it on the plot
            self.createSpectrumLine()

            if not self.remoteAgentUp:
                if self.hackrf.hasHackrf:
                    self.hackrf.startScanning24()
                    self.menuHackrfSpectrum5.setEnabled(False)                
            else:
                if self.remoteHasHackrf:
                    errcode, errmsg = startRemoteSpectrumScan(self.remoteAgentIP, self.remoteAgentPort, False)
                    self.menuHackrfSpectrum5.setEnabled(False)                
                
                    if errcode != 0:
                        self.statusBar().showMessage(errmsg)
                        
            # Disable Ubertooth 2.4 GHz scan since we're using the HackRF
            self.menuBtSpectrum.setEnabled(False)
            
            if not self.remoteAgentUp:
                self.hackrfSpectrumTimer.start(self.hackrfSpectrumTimeoutLocal)
            else:
                self.hackrfSpectrumTimer.start(self.hackrfSpectrumTimeoutRemote)
        else:
            # Disable it
            self.menuHackrfSpectrum5.setEnabled(True)                
            self.hackrfSpectrumTimer.stop()
            
            if (not self.remoteAgentUp):
                # Local
                if self.hackrf.hasHackrf:
                    self.hackrf.stopScanning()

                # If we have bluetooth let's re-enable it.
                if self.hasBluetooth:
                    self.menuBtSpectrum.setEnabled(True)
            else:
                # Remote
                errcode, errmsg = stopRemoteSpectrumScan(self.remoteAgentIP, self.remoteAgentPort)
            
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)

                # If we have bluetooth let's re-enable it.
                if self.hasRemoteBluetooth:
                    self.menuBtSpectrum.setEnabled(True)
                
            # remove it from the plot
            self.stopSpectrum24Line()
            
    def onHackrfSpectrumAnalyzer5(self):
        if (self.menuHackrfSpectrum5.isChecked() == self.hackrfLastSpectrumState5):
            # There's an extra bounce in this for some reason.
            return

        self.hackrfShowSpectrum5 = self.menuHackrfSpectrum5.isChecked()
        self.hackrfLastSpectrumState5 = self.hackrfShowSpectrum5
        
        if self.hackrfShowSpectrum5:
            # Add it on the plot
            self.createSpectrumLine()

            if not self.remoteAgentUp:
                # Local
                if self.hackrf.hasHackrf:
                    self.hackrf.startScanning5()
            else:
                # Remote 
                if self.remoteHasHackrf:
                    errcode, errmsg = startRemoteSpectrumScan(self.remoteAgentIP, self.remoteAgentPort, True)
                
                    if errcode != 0:
                        self.statusBar().showMessage(errmsg)
                
            self.menuHackrfSpectrum24.setEnabled(False)                
                
            if not self.remoteAgentUp:
                self.hackrfSpectrumTimer.start(self.hackrfSpectrumTimeoutLocal)
            else:
                self.hackrfSpectrumTimer.start(self.hackrfSpectrumTimeoutRemote)
        else:
            # Disable it
            self.hackrfSpectrumTimer.stop()
            
            if not self.remoteAgentUp:
                if self.hackrf.hasHackrf:
                    self.hackrf.stopScanning()
            else:
                errcode, errmsg = stopRemoteSpectrumScan(self.remoteAgentIP, self.remoteAgentPort)
            
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
                    
            # remove it from the plot
            self.stopSpectrum5Line()
            
            if not self.btShowSpectrum:
                self.menuHackrfSpectrum24.setEnabled(True)                
        
    def onBtSpectrumAnalyzer(self):
        if (self.menuBtSpectrum.isChecked() == self.btLastSpectrumState):
            # There's an extra bounce in this for some reason.
            return

        if (not self.remoteAgentUp):
            # Local
            if self.bluetooth.discoveryRunning():
                reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for discovery.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                if reply == QMessageBox.Yes:
                    self.bluetooth.stopDiscovery()
                else:
                    self.menuBtSpectrum.setChecked(False)
                    self.btShowSpectrum = False
                    self.btLastSpectrumState = False
                    return
                    
            if self.btBeacon:
                reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for beaconing.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                if reply == QMessageBox.Yes:
                    self.bluetooth.stopBeacon()
                else:
                    self.menuBtSpectrum.setChecked(False)
                    self.btShowSpectrum = False
                    self.btLastSpectrumState = False
                    return
                    
            errcode = 0
            errmsg = ""
        else:
            # Remote
            errcode, errmsg, self.hasRemoteBluetooth, self.hasRemoteUbertooth, scanRunning, discoveryRunning, beaconRunning = getRemoteBluetoothRunningServices(self.remoteAgentIP, self.remoteAgentPort)
            
            if errcode == 0:
                if discoveryRunning:
                    reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for discovery.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                    if reply == QMessageBox.Yes:
                        pass
                        # Stop remote discovery here
                    else:
                        self.menuBtSpectrum.setChecked(False)
                        self.btShowSpectrum = False
                        self.btLastSpectrumState = False
                        return
                
            if beaconRunning:
                reply = QMessageBox.question(self, 'Question',"Bluetooth hardware is being used for beaconing.  Would you like to stop it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                if reply == QMessageBox.Yes:
                    errcode, errmsg = stopRemoteBluetoothBeacon(self.remoteAgentIP, self.remoteAgentPort)
                else:
                    self.menuBtSpectrum.setChecked(False)
                    self.btShowSpectrum = False
                    self.btLastSpectrumState = False
                    return
                    
        self.btShowSpectrum = self.menuBtSpectrum.isChecked()
        self.btLastSpectrumState = self.btShowSpectrum
        
        if self.btShowSpectrum:
            # Add it on the plot
            self.createSpectrumLine()
            
            if self.bluetooth and (not self.remoteAgentUp):
                self.bluetooth.startScanning()
            elif self.remoteAgentUp:
                errcode, errmsg = startRemoteBluetoothScan(self.remoteAgentIP, self.remoteAgentPort)
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
                    self.menuBtSpectrum.setChecked(False)
                    self.btShowSpectrum = False
                    self.btLastSpectrumState = False
                    self.btSpectrumTimer.stop()
                    return
                
            self.menuHackrfSpectrum24.setEnabled(False)
            
            if not self.remoteAgentUp:
                self.btSpectrumTimer.start(self.btSpectrumTimeoutLocal)
            else:
                self.btSpectrumTimer.start(self.btSpectrumTimeoutRemote)
        else:
            self.btSpectrumTimer.stop()
            self.setWaitCursor()
            if self.bluetooth and (not self.remoteAgentUp):
                self.bluetooth.stopScanning()
            elif self.remoteAgentUp:
                errcode, errmsg = stopRemoteBluetoothScan(self.remoteAgentIP, self.remoteAgentPort)
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
            self.setArrowCursor()
                    
            # If we're local:
            if not self.remoteAgentUp:
                hackrfsetting = self.hackrf.hasHackrf
            else:
                hackrfsetting = self.remoteHasHackrf
                
            if hackrfsetting and (not self.hackrfShowSpectrum5):
                self.menuHackrfSpectrum24.setEnabled(True)
            
            # remove it from the plot
            if self.spectrum24Line:
                self.chart24.removeSeries(self.spectrum24Line)
                self.spectrum24Line = None
            
    def setWaitCursor(self):
        self.setCursor(Qt.WaitCursor)
        
    def setArrowCursor(self):
        self.setCursor(Qt.ArrowCursor)
        
    def onAdvancedScanClosed(self):
        self.advancedScan = None
     
    def onAdvScanUpdateSSIDs(self, wirelessNetworks):
        rowPosition = self.networkTable.rowCount()
        
        if rowPosition > 0:
            # Range goes to last # - 1
            for curRow in range(0, rowPosition):
                try:
                    curData = self.networkTable.item(curRow, 2).data(Qt.UserRole+1)
                except:
                    curData = None
                    
                if (curData):
                    # We already have the network.  just update it
                    for curKey in wirelessNetworks.keys():
                        curNet = wirelessNetworks[curKey]
                        if (curData.macAddr == curNet.macAddr) and (curData.channel == curNet.channel):
                            # See if we had an unknown SSID
                            if curData.ssid.startswith('<Unknown') and (not curNet.ssid.startswith('<Unknown')):
                                curData.ssid = curNet.ssid
                                self.networkTable.item(curRow, 2).setText(curData.ssid)
                                curSeries = self.networkTable.item(curRow, 2).data(Qt.UserRole)
                                curSeries.setName(curData.ssid)
        
    def onAdvancedScan(self):
        if not hasFalcon:
            return
                    
        if not self.advancedScan:
            self.advancedScan = AdvancedScanDialog(self.remoteAgentUp,  self.remoteAgentIP,  self.remoteAgentPort, self, self)  # Need to set parent to None to allow it to not always be on top

        self.checkNotifyAdvancedScan()
            
        self.advancedScan.show()
        self.advancedScan.activateWindow()

    def checkNotifyAdvancedScan(self):
        if not self.advancedScan:
            return
            
        # If we've changed from local<->remote, or something about the remote end has changed, let's signal update
        if ((self.advancedScan.usingRemoteAgent != self.remoteAgentUp) or 
            (self.remoteAgentUp and (self.remoteAgentIP !=self.advancedScan.remoteAgentIP or self.remoteAgentPort !=self.advancedScan.remoteAgentPort))) :
            if self.remoteAgentUp:
                self.advancedScan.setRemoteAgent(self.remoteAgentIP, self.remoteAgentPort)
            else:
                self.advancedScan.setLocal()
        
    def checkNotifyBluetoothDiscovery(self):
        if not self.bluetoothWin:
            return
            
        # If we've changed from local<->remote, or something about the remote end has changed, let's signal update
        if ((self.bluetoothWin.usingRemoteAgent != self.remoteAgentUp) or 
            (self.remoteAgentUp and (self.remoteAgentIP !=self.bluetoothWin.remoteAgentIP or self.remoteAgentPort !=self.bluetoothWin.remoteAgentPort))) :
            if self.remoteAgentUp:
                self.bluetoothWin.setRemoteAgent(self.remoteAgentIP, self.remoteAgentPort)
            else:
                self.bluetoothWin.setLocal()
        
    def onScanModeChanged(self):
        self.scanMode = str(self.scanModeCombo.currentText())
        
        if self.scanMode == "Normal":
            self.huntChannels.setVisible(False)
            self.lblScanMode.setVisible(False)
        else:
            self.huntChannels.setVisible(True)
            self.lblScanMode.setVisible(True)
            
        self.getHuntChannels()
        
    def getHuntChannels(self):
        channelStr = self.huntChannels.text()
        channelStr = channelStr.replace(' ', '')
        
        if (',' in channelStr):
            tmpList = channelStr.split(',')
        else:
            tmpList = []
            if (len(channelStr)>0):
                try:
                    # quick check that we really have a number
                    intVal = int(channelStr)
                    tmpList.append(channelStr)
                except:
                    pass
        
        for curItem in tmpList:
            if len(curItem) > 0:
                try:
                    freqForChannel = WirelessEngine.getFrequencyForChannel(curItem)
                    
                    if freqForChannel is not None:
                        self.huntChannelList.append(int(freqForChannel))
                    else:
                        self.huntChannelList.append(int(curItem))
                except:
                    QMessageBox.question(self, 'Error',"Could not figure channel out from " + curItem, QMessageBox.Ok)
        
    def onXGPSLocal(self):
        try:
            subprocess.Popen('xgps')
        except:
            QMessageBox.question(self, 'Error',"Unable to open xgps.  You may need to 'apt install gpsd-clients'", QMessageBox.Ok)
        
    def onXGPSRemote(self):
        text, okPressed = QInputDialog.getText(self, "Remote Agent","Please provide gpsd IP:", QLineEdit.Normal, "127.0.0.1:2947")
        if okPressed and text != '':
            # Validate the input
            p = re.compile('^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]{1,5})')
            specIsGood = True
            try:
                agentSpec = p.search(text).group(1)
                remoteIP = agentSpec.split(':')[0]
                remotePort = int(agentSpec.split(':')[1])
                
                if remotePort < 1 or remotePort > 65535:
                    QMessageBox.question(self, 'Error',"Port must be in an acceptable IP range (1-65535)", QMessageBox.Ok)
                    specIsGood = False
            except:
                QMessageBox.question(self, 'Error',"Please enter it in the format <IP>:<port>", QMessageBox.Ok)
                specIsGood = False
                
            if not specIsGood:
                self.menuRemoteAgent.setChecked(False)
                return
                    
            args = ['xgps', remoteIP + ":" + str(remotePort)]
            
            try:
                subprocess.Popen(args)
            except:
                QMessageBox.question(self, 'Error',"Unable to open xgps.  You may need to 'apt install gpsd-clients'", QMessageBox.Ok)

    def onCopyNet(self):
        self.updateLock.acquire()
        
        curRow = self.networkTable.currentRow()
        curCol = self.networkTable.currentColumn()
        
        if curRow == -1 or curCol == -1:
            self.updateLock.release()
            return
        
        if curCol != 11:
            curText = self.networkTable.item(curRow, curCol).text()
        else:
            curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole+1)
            curText = 'Last Recorded GPS Coordinates:\n' + str(curNet.gps)
            curText += 'Strongest Signal Coordinates:\n'
            curText += 'Strongest Signal: ' + str(curNet.strongestsignal) + '\n'
            curText += str(curNet.strongestgps)
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)
        
        self.updateLock.release()
        
    def onDeleteNet(self):
        self.updateLock.acquire()
        
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            self.updateLock.release()
            return
        
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole+1)
        curSeries = self.networkTable.item(curRow, 2).data(Qt.UserRole)

        if curNet and curSeries:
            if (curNet.channel < 15):
                self.chart24.removeSeries(curSeries)
            else:
                self.chart5.removeSeries(curSeries)
            
        self.networkTable.removeRow(curRow)
        
        self.updateLock.release()
            
    def onShowTelemetry(self):
        self.updateLock.acquire()
        
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            self.updateLock.release()
            return
        
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole+1)
        
        if curNet == None:
            self.updateLock.release()
            return
       
        if curNet.getKey() not in self.telemetryWindows.keys():
            telemetryWindow = TelemetryDialog()
            telemetryWindow.show()
            self.telemetryWindows[curNet.getKey()] = telemetryWindow
        else:
            telemetryWindow = self.telemetryWindows[curNet.getKey()]
        
        # Can also key off of self.telemetryWindow.isVisible()
        telemetryWindow.show()
        telemetryWindow.activateWindow()
        
        # Can do telemetry window updates after release
        self.updateLock.release()
        
        # User could have selected a different network.
        telemetryWindow.updateNetworkData(curNet)            
        
    def showNTContextMenu(self, pos):
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
            
        self.ntRightClickMenu.exec_(self.networkTable.mapToGlobal(pos))
 
    def onSpectrumTimer(self):
        
        # This is actually for the Ubertooth spectrum.  HackRF spectrums are handled by OnHackrfSpectrumAnalyzer<24/5> methods
        
        if self.btShowSpectrum: #  and self.bluetooth:
            if (not self.remoteAgentUp):
                # Get Local data
                channelData = self.bluetooth.spectrumToChannels()
                errcode = 0
            else:
                # Get remote data
                errcode, errmsg, channelData = getRemoteBluetoothScanSpectrum(self.remoteAgentIP, self.remoteAgentPort)
                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
                    channelData = {}
                
            # Plot it
            self.createSpectrumLine()

            self.spectrum24Line.clear()

            if len(channelData) > 0:
                sortedKeys = sorted(channelData.keys())
                
                # For testing the waterfall
                # newArray=[]
                
                for curKey in sortedKeys:
                    fCurKey = float(curKey)
                    if self.btSpectrumGain != 1.0:
                        # Have to do this + / - to apply the gain on the scale since the values are all negative
                        # -90 * 1.1 is -99 (goes the wrong way)
                        dBm = float((channelData[curKey] + 110.0)*self.btSpectrumGain - 110.0)
                    else:
                        dBm = float(channelData[curKey])
                        
                    self.spectrum24Line.append(fCurKey, dBm)
                    # For testing the waterfall
                    # newArray.append(dBm)
                
                # For testing the waterfall
                #if not self.waterfall24:
                #    self.waterfall24 = WaterfallPlotWindow(self, None)
                
                #self.waterfall24.update
            
            if not self.remoteAgentUp:
                self.btSpectrumTimer.start(self.btSpectrumTimeoutLocal)
            else:
                # Slow it down just a bit for remote agents
                self.btSpectrumTimer.start(self.btSpectrumTimeoutRemote)
        
    def onHackrfSpectrumTimer(self):
        if self.hackrfShowSpectrum24 or self.hackrfShowSpectrum5: #  and self.bluetooth:
            if (not self.remoteAgentUp):
                # Get Local data
                if self.hackrfShowSpectrum5:
                    channelData = self.hackrf.spectrum5ToChannels()
                else:
                    channelData = self.hackrf.spectrum24ToChannels()
                    
                errcode = 0
            else:
                # Get remote data
                errcode, errmsg, channelData = getRemoteSpectrumScan(self.remoteAgentIP, self.remoteAgentPort)

                if errcode != 0:
                    self.statusBar().showMessage(errmsg)
                    channelData = {}
                        
            # Plot it
            self.createSpectrumLine()

            # Only clear the oen we're working with, just in case btSpectrum is active
            if self.hackrfShowSpectrum24:
                self.spectrum24Line.clear()
            elif self.hackrfShowSpectrum5:
                self.spectrum5Line.clear()

            if len(channelData) > 0 and self.hackrfShowSpectrum24:
                self.Plot24.setViewportUpdateMode(QGraphicsView.NoViewportUpdate)
                sortedKeys = sorted(channelData.keys())
                for curKey in sortedKeys:
                    fCurKey = float(curKey)
                    if self.btSpectrumGain != 1.0:
                        # Have to do this + / - to apply the gain on the scale since the values are all negative
                        # -90 * 1.1 is -99 (goes the wrong way)
                        dBm = float((channelData[curKey] + 110.0)*self.btSpectrumGain - 110.0)
                    else:
                        dBm = float(channelData[curKey])
                    self.spectrum24Line.append(fCurKey, dBm)
                # Re-enable updates and force an update
                self.Plot24.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
                # Not needed
                # self.Plot24.update()
                
            if len(channelData) > 0 and self.hackrfShowSpectrum5:
                # Disable chart update
                self.Plot5.setViewportUpdateMode(QGraphicsView.NoViewportUpdate)

                sortedKeys = sorted(channelData.keys())
                for curKey in sortedKeys:
                    fCurKey = float(curKey)
                    if self.btSpectrumGain != 1.0:
                        # Have to do this + / - to apply the gain on the scale since the values are all negative
                        # -90 * 1.1 is -99 (goes the wrong way)
                        dBm = float((channelData[curKey] + 110.0)*self.btSpectrumGain - 110.0)
                    else:
                        dBm = float(channelData[curKey])
                    self.spectrum5Line.append(fCurKey, dBm)

                # Re-enable updates and force an update
                self.Plot5.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
                # Not needed
                # self.Plot5.update()
                
            if not self.remoteAgentUp:
                self.hackrfSpectrumTimer.start(self.hackrfSpectrumTimeoutLocal)
            else:
                # Slow it down just a bit for remote agents
                self.hackrfSpectrumTimer.start(self.hackrfSpectrumTimeoutRemote)
        
    def onGPSTimer(self):
        self.onGPSStatus(False)
        self.gpsTimer.start(self.gpsTimerTimeout)
        
    def onGoogleMap(self):
        rowPosition = self.networkTable.rowCount()

        if rowPosition <= 0:
            QMessageBox.question(self, 'Error',"There's no access points in the table.  Please run a scan first or open a saved scan.", QMessageBox.Ok)
            return
            
        mapSettings, ok = MapSettingsDialog.getSettings()

        if not ok:
            return
            
        if len(mapSettings.outputfile) == 0:
            QMessageBox.question(self, 'Error',"Please provide an output file.", QMessageBox.Ok)
            return
            
        markerDict = {}
        markers = []
        
        # Range goes to last # - 1
        for curRow in range(0, rowPosition):
            try:
                curData = self.networkTable.item(curRow, 2).data(Qt.UserRole+1)
            except:
                curData = None
            
            if (curData):
                newMarker = MapMarker()
                
                newMarker.label = WirelessEngine.convertUnknownToString(curData.ssid)
                newMarker.label = newMarker.label[:mapSettings.maxLabelLength]
                
                if mapSettings.plotstrongest:
                    if curData.strongestgps.isValid:
                        newMarker.gpsValid = True
                        newMarker.latitude = curData.strongestgps.latitude
                        newMarker.longitude = curData.strongestgps.longitude
                    else:
                        newMarker.gpsValid = False
                        newMarker.latitude = 0.0
                        newMarker.longitude = 0.0
                        
                    newMarker.barCount = WirelessEngine.getSignalQualityFromDB0To5(curData.strongestsignal)
                else:
                    if curData.gps.isValid:
                        newMarker.gpsValid = True
                        newMarker.latitude = curData.gps.latitude
                        newMarker.longitude = curData.gps.longitude
                    else:
                        newMarker.gpsValid = False
                        newMarker.latitude = 0.0
                        newMarker.longitude = 0.0
                        
                    newMarker.barCount = WirelessEngine.getSignalQualityFromDB0To5(curData.signal)
                
                markerKey = newMarker.getKey()
                if markerKey in markerDict:
                    curMarker = markerDict[markerKey]
                    curMarker.addLabel(newMarker.label)
                    if curMarker.barCount > newMarker.barCount:
                        curMarker.barCount = newMarker.barCount
                else:
                    # Move label to list
                    newMarker.addLabel(newMarker.label)
                    newMarker.label = ''
                    markerDict[markerKey] = newMarker

        # Now send consolidated list
        for curKey in markerDict.keys():
            markers.append(markerDict[curKey])
        
        if len(markers) > 0:
            retVal = MapEngineOSM.createMap(mapSettings.outputfile,mapSettings.title,markers, connectMarkers=False, openWhenDone=False, mapType=mapSettings.mapType)
            
            if not retVal:
                QMessageBox.question(self, 'Error',"Unable to generate map to " + mapSettings.outputfile, QMessageBox.Ok)
            else:
                QMessageBox.question(self, 'Info',"Map saved to " + mapSettings.outputfile, QMessageBox.Ok)

    def onGoogleMapTelemetry(self):
        mapSettings, ok = TelemetryMapSettingsDialog.getSettings()

        if not ok:
            return
            
        if len(mapSettings.inputfile) == 0:
            QMessageBox.question(self, 'Error',"Please provide an input file.", QMessageBox.Ok)
            return
            
        if len(mapSettings.outputfile) == 0:
            QMessageBox.question(self, 'Error',"Please provide an output file.", QMessageBox.Ok)
            return
            
        markers = []
        raw_list = []
        
        try:
            with open(mapSettings.inputfile, 'r') as f:
                reader = csv.reader(f)
                raw_list = list(reader)
        except:
            pass
                
            # remove blank lines
            while [] in raw_list:
                raw_list.remove([])
                
            if len(raw_list) > 1:
                # Check header row looks okay
                if str(raw_list[0]) != "['macAddr', 'SSID', 'Strength', 'Timestamp', 'GPS', 'Latitude', 'Longitude', 'Altitude']":
                    QMessageBox.question(self, 'Error',"File format doesn't look like exported telemetry data saved from the telemetry window.", QMessageBox.Ok)
                    return
                        
                # Ignore header row
                # Range goes to last # - 1
                for i in range (1, len(raw_list)):
                    
                    # Plot first, last, and every Nth point
                    if ((i % mapSettings.plotNthPoint == 0) or (i == 1) or (i == (len(raw_list)-1))) and (len(raw_list[i]) > 0):
                        
                        if raw_list[i][4] == 'Yes':
                            # The GPS entry is valid
                            newMarker = MapMarker()
                            
                            if (i == 1) or (i == (len(raw_list)-1)):
                                # Only put first and last marker labels on.  No need to pollute the map if there's a lot of points
                                newMarker.label = WirelessEngine.convertUnknownToString(raw_list[i][1])
                                newMarker.label = newMarker.label[:mapSettings.maxLabelLength]
                            
                            newMarker.latitude = float(raw_list[i][5])
                            newMarker.longitude = float(raw_list[i][6])
                            newMarker.barCount = WirelessEngine.getSignalQualityFromDB0To5(int(raw_list[i][2]))
                                
                            markers.append(newMarker)
                    
        if len(markers) > 0:
            retVal = MapEngine.createMap(mapSettings.outputfile,mapSettings.title,markers, connectMarkers=True, openWhenDone=True, mapType=mapSettings.mapType)
            
            if not retVal:
                QMessageBox.question(self, 'Error',"Unable to generate map to " + mapSettings.outputfile, QMessageBox.Ok)
        
    def onGPSCoordinates(self):
        if not self.gpsCoordWindow:
            self.gpsCoordWindow = GPSCoordDialog(mainWin=self)
            
        self.gpsCoordWindow.show()
        self.gpsCoordWindow.activateWindow()

    def getCurrentGPS(self):
        # retVal will be a sparrowGPS object
        
        if (not self.remoteAgentUp):
            # Local
            retVal = self.gpsEngine.getLastCoord()
        else:
            # Remote
            errCode, errMsg, gpsStatus = requestRemoteGPS(self.remoteAgentIP, self.remoteAgentPort)
            
            if errCode == 0:
                retVal = gpsStatus.asSparrowGPSObject()
            else:
                retVal = None
        
        if retVal is None:
            retVal = SparrowGPS()
            
        return retVal
        
    def onGPSStatus(self, updateStatusBar=True):
        if (not self.remoteAgentUp):
            # Checking local GPS
            if GPSEngine.GPSDRunning():
                if self.gpsEngine.gpsValid():
                    self.gpsSynchronized = True
                    self.btnGPSStatus.setStyleSheet("background-color: green; border: 1px;")
                    if updateStatusBar:
                        self.statusBar().showMessage('Local gpsd service is running and satellites are synchronized.')
                else:
                    self.gpsSynchronized = False
                    self.btnGPSStatus.setStyleSheet("background-color: yellow; border: 1px;")
                    if updateStatusBar:
                        self.statusBar().showMessage("Local gpsd service is running but it's not synchronized with the satellites yet.")
                    
            else:
                self.gpsSynchronized = False
                if updateStatusBar:
                    self.statusBar().showMessage('No local gpsd running.')
                self.btnGPSStatus.setStyleSheet("background-color: red; border: 1px;")
        else:
            # Checking remote
            errCode, errMsg, gpsStatus = requestRemoteGPS(self.remoteAgentIP, self.remoteAgentPort)
            
            if errCode == 0:
                self.missedAgentCycles = 0
                if (gpsStatus.isValid):
                    self.gpsSynchronized = True
                    self.btnGPSStatus.setStyleSheet("background-color: green; border: 1px;")
                    self.statusBar().showMessage("Remote GPS is running and synchronized.")
                elif (gpsStatus.gpsRunning):
                    self.gpsSynchronized = False
                    self.btnGPSStatus.setStyleSheet("background-color: yellow; border: 1px;")
                    self.statusBar().showMessage("Remote GPS is running but it has not synchronized with the satellites yet.")
                else:
                    self.gpsSynchronized = False
                    self.statusBar().showMessage("Remote GPS service is not running.")
                    self.btnGPSStatus.setStyleSheet("background-color: red; border: 1px;")
            else:
                agentUp = portOpen(self.remoteAgentIP, self.remoteAgentPort)
                # Agent may be up but just taking a while to respond.
                if (not agentUp) or errCode == -1:
                    self.missedAgentCycles += 1
                    
                    if (not agentUp) or (self.missedAgentCycles > self.allowedMissedAgentCycles):
                        # We may let it miss a cycle or two just as a good practice
                        
                        # Agent disconnected.
                        # Stop any active scan and transition local
                        self.agentDisconnected()
                        self.statusBar().showMessage("Error connecting to remote agent.  Agent disconnected.")
                        QMessageBox.question(self, 'Error',"Error connecting to remote agent.  Agent disconnected.", QMessageBox.Ok)
                else:
                    self.statusBar().showMessage("Remote GPS Error: " + errMsg)
                    self.btnGPSStatus.setStyleSheet("background-color: red; border: 1px;")
            

    def onGPSSyncChanged(self):
        # GPS status has changed
        self.onGPSStatus()

    def onTableHeadingClicked(self, logical_index):
        header = self.networkTable.horizontalHeader()
        order = Qt.DescendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )

        self.networkTableSortOrder = order
        self.networkTableSortIndex = logical_index
        self.networkTable.sortItems(logical_index, order )
        
    def onNetworkTableSelectionChanged(self):
        row = self.networkTable.currentRow()
        if row < 0:
            return
            
        if (self.lastSeries is not None):
            # Change the old one back
            if (self.lastSeries):
                pen = self.lastSeries.pen()
                pen.setWidth(2)
                self.lastSeries.setPen(pen)
                self.lastSeries.setVisible(False)
                self.lastSeries.setVisible(True)
            
        selectedSeries = self.networkTable.item(row, 2).data(Qt.UserRole)
        
        if (selectedSeries):
            pen = selectedSeries.pen()
            pen.setWidth(6)
            selectedSeries.setPen(pen)
            selectedSeries.setVisible(False)
            selectedSeries.setVisible(True)
            
            self.lastSeries = selectedSeries
        else:
            selectedSeries = None

    def onTableClicked(self, row, col):
        pass
        
    def onGPSStatusIndicatorClicked(self):
        if self.hasXGPS():
            if self.menuRemoteAgent.isChecked():
                self.onXGPSRemote()
            else:
                if GPSEngine.GPSDRunning():
                    self.onXGPSLocal()

    def onSingleShotScanResults(self, wirelessNetworks, retCode, errString):
        # Change the GUI controls back
        self.scanModeCombo.setEnabled(True)
        self.huntChannels.setEnabled(True)
        
        self.btnScan.setEnabled(True)
        self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
        self.btnScan.setText('&Scan')
        
        # Display the data or any errors if they occurred
        if (retCode == 0):
            # Good data
            if len(wirelessNetworks) > 0:
                self.populateTable(wirelessNetworks)
                
            self.statusBar().showMessage('Ready')
        else:
            # Errors (note, device busy can happen, but for single-shot mode we want to know because no display change would happen)
            self.errmsg.emit(retCode, errString)
                    
        # reset the shortcut (seems to undo when we change the text)
        self.btnScan.setShortcut('Ctrl+S')
        self.btnScan.setChecked(False)
        
    def onRemoteScanSingleShot(self):
        # Quick sanity check for interfaces
        if (self.combo.count() > 0):
            curInterface = str(self.combo.currentText())
            self.statusBar().showMessage('Scanning on interface ' + curInterface)
        else:
            # No interfaces, don't do anything and just debounce the scan button
            self.btnScan.setChecked(False)
            return
            
        # Disable the GUI controls to indicate we're scanning
        self.scanModeCombo.setEnabled(False)
        self.huntChannels.setEnabled(False)
        
        self.btnScan.setEnabled(False)
        self.btnScan.setStyleSheet("background-color: rgba(224,224,224,255); border: none;")
        self.btnScan.setText('&Scanning')
        self.btnScan.repaint()
        
        # Make the call to get the data

        if self.scanMode == "Normal" or (len(self.huntChannelList) == 0):
            # retCode, errString, wirelessNetworks = requestRemoteNetworks(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            self.remoteSingleShotThread = remoteSingleShotThread(curInterface, self, self.remoteAgentIP, self.remoteAgentPort)
        else:
            # retCode, errString, wirelessNetworks = requestRemoteNetworks(self.remoteAgentIP, self.remoteAgentPort, curInterface, self.huntChannelList)
            self.remoteSingleShotThread = remoteSingleShotThread(curInterface, self, self.remoteAgentIP, self.remoteAgentPort, self.huntChannelList)
        
        self.remoteSingleShotThread.start()
        
        # Change the GUI controls back happens when the emit happens

    def agentDisconnected(self):
        # Don't try to pull any more GPS status
        self.remoteAgentUp = False
        
        # Stop any running scans
        if self.remoteAutoUpdates and self.btnScan.isChecked():
            self.btnScan.setChecked(False)
            self.onScanClicked(False)
            
        # Signal disconnect agent internally
        self.menuRemoteAgent.setChecked(False)
        self.onRemoteAgent()
        
        self.menuHackrfSpectrum24.setChecked(False)
        self.menuHackrfSpectrum5.setChecked(False)
        if self.hackrf.hasHackrf:
            self.menuHackrfSpectrum24.setEnabled(True)
            self.menuHackrfSpectrum5.setEnabled(True)
        else:
            self.menuHackrfSpectrum24.setEnabled(False)
            self.menuHackrfSpectrum5.setEnabled(False)
        
        self.setBluetoothMenu()
        self.checkNotifyBluetoothDiscovery()
        self.checkNotifyAdvancedScan()
                
    def onRemoteScanClicked(self, pressed):
        if not self.remoteAutoUpdates:
            # Single-shot mode.
            self.onRemoteScanSingleShot()
            return
            
        # Auto update
        self.remoteScanRunning = pressed
        
        if not self.remoteScanRunning:
            if self.remoteScanThread:
                self.remoteScanThread.signalStop = True
                
                while (self.remoteScanThread.threadRunning):
                    self.statusBar().showMessage('Waiting for active scan to terminate...')
                    sleep(0.2)
                    
                self.remoteScanThread = None
                    
            self.statusBar().showMessage('Ready')
        else:
            if (self.combo.count() > 0):
                curInterface = str(self.combo.currentText())
                self.statusBar().showMessage('Scanning on interface ' + curInterface)
                if self.scanMode == "Normal" or (len(self.huntChannelList) == 0):
                    self.remoteScanThread = RemoteScanThread(curInterface, self)
                else:
                    self.remoteScanThread = RemoteScanThread(curInterface, self, self.huntChannelList)
                    
                self.remoteScanThread.scanDelay = self.remoteScanDelay
                self.remoteScanThread.start()
            else:
                QMessageBox.question(self, 'Error',"No wireless adapters found.", QMessageBox.Ok)
                self.remoteScanRunning = False
                self.btnScan.setChecked(False)
                
        if self.btnScan.isChecked():
            # Scanning is on.  Turn red to indicate click would stop
            self.btnScan.setStyleSheet("background-color: rgba(255,0,0,255); border: none;")
            self.btnScan.setText('&Stop scanning')
            self.menuRemoteAgent.setEnabled(False)
        else:
            self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
            self.btnScan.setText('&Scan')
            self.menuRemoteAgent.setEnabled(True)

        # Need to reset the shortcut after changing the text
        self.btnScan.setShortcut('Ctrl+S')

    def onScanClicked(self, pressed):
        if self.menuRemoteAgent.isChecked():
            # We're in remote mode.  Let's handle it there
            self.onRemoteScanClicked(pressed)
            return
            
        # We're in local mode.
        self.scanRunning = pressed
        
        if not self.scanRunning:
            # Want to stop a running scan (self.scanRunning represents the NEW pressed state)
            if self.scanThread:
                self.setWaitCursor()
                
                self.scanThread.signalStop = True

                while (self.scanThread.threadRunning):
                    self.statusBar().showMessage('Waiting for active scan to terminate...')
                    sleep(0.2)
                    
                self.scanThread = None
                
                self.setArrowCursor()
                
            self.statusBar().showMessage('Ready')
        else:
            # Want to start a new scan
            if (not self.runningAsRoot):
                QMessageBox.question(self, 'Warning',"You need to have root privileges to run local scans.", QMessageBox.Ok)
                self.btnScan.setChecked(False)
                self.scanRunning = False
                return

            if (self.combo.count() > 0):
                curInterface = str(self.combo.currentText())
                self.statusBar().showMessage('Scanning on interface ' + curInterface)
                if self.scanMode == "Normal" or (len(self.huntChannelList) == 0):
                    self.scanThread = ScanThread(curInterface, self)
                else:
                    self.getHuntChannels()
                    self.scanThread = ScanThread(curInterface, self, self.huntChannelList)
                self.scanThread.scanDelay = self.scanDelay
                self.scanThread.start()
            else:
                QMessageBox.question(self, 'Error',"No wireless adapters found.", QMessageBox.Ok)
                self.scanRunning = False
                self.btnScan.setChecked(False)
                
        if self.btnScan.isChecked():
            # Scanning is on.  Turn red to indicate click would stop
            self.btnScan.setStyleSheet("background-color: rgba(255,0,0,255); border: none;")
            self.btnScan.setText('&Stop scanning')
            self.menuRemoteAgent.setEnabled(False)
            self.scanModeCombo.setEnabled(False)
            self.huntChannels.setEnabled(False)
            self.combo.setEnabled(False)
        else:
            self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
            self.btnScan.setText('&Scan')
            self.menuRemoteAgent.setEnabled(True)
            self.scanModeCombo.setEnabled(True)
            self.huntChannels.setEnabled(True)
            self.combo.setEnabled(True)
            
        # Need to reset the shortcut after changing the text
        self.btnScan.setShortcut('Ctrl+S')
        
    def scanResultsFromAdvanced(self, wirelessNetworks):
            self.populateTable(wirelessNetworks, True)
        
    def scanResults(self, wirelessNetworks):
        if self.scanRunning:
            # Running local.  If we have a good GPS, update the networks
            # NOTE: We don't have to worry about remote scans.  They'll fill the GPS results in the data that gets passed to us.
            if self.gpsSynchronized and (self.gpsEngine.lastCoord is not None) and (self.gpsEngine.lastCoord.isValid):
                for curKey in wirelessNetworks.keys():
                    curNet = wirelessNetworks[curKey]
                    curNet.gps.copy(self.gpsEngine.lastCoord)
                    curNet.strongestgps.copy(self.gpsEngine.lastCoord)
        
        if self.menuRemoteAgent.isChecked() or ((not self.menuRemoteAgent.isChecked()) and self.scanRunning):
            # If is to prevent a messaging issue on last iteration
            # Scan results will come over from the remote agent with the GPS fields already populated.
            self.populateTable(wirelessNetworks)
        
    def onErrMsg(self, errCode, errMsg):
        self.statusBar().showMessage("Error ["+str(errCode) + "]: " + errMsg)
        
        if ((errCode == WirelessNetwork.ERR_NETDOWN) or (errCode == WirelessNetwork.ERR_OPNOTSUPPORTED) or 
            (errCode == WirelessNetwork.ERR_OPNOTPERMITTED)):
            if self.scanThread:
                self.scanThread.signalStop = True
                
                while (self.scanThread.threadRunning):
                    sleep(0.2)
                    
                self.scanThread = None
                self.scanRunning = False
                self.btnScan.setChecked(False)
                
                # Undo button
                self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
                self.btnScan.setText('&Scan')
                self.menuRemoteAgent.setEnabled(True)
                self.scanModeCombo.setEnabled(True)
                self.huntChannels.setEnabled(True)
                self.combo.setEnabled(True)

    def plotSSID(self, curSeries, curNet):
        tmpssid = curNet.ssid
        if (len(tmpssid) == 0):
            tmpssid = '<Unknown>'
            
        curSeries.setName(tmpssid)
        
        # Both 2.4 and 5 GHz use +-2 channels for 20 MHz channels
        channelDeltaLow = 2
        channelDeltaHigh = 2
        
        # For 2.4 GHz Channels:
        # https://en.wikipedia.org/wiki/List_of_WLAN_channels#5_GHz_.28802.11a.2Fh.2Fj.2Fn.2Fac.29
        # See this for 5 GHz channels:
        # http://stevencrowley.com/wp-content/uploads/2013/02/NTIA5-2.jpg
        if curNet.bandwidth == 40:
            if curNet.channel <= 16:
                if curNet.secondaryChannelLocation == 'above':
                    channelDeltaHigh = 6
                else:
                    channelDeltaLow = 6
            else:
                # 5 GHz channels for these bandwidths center up.
                channelDeltaLow = 4
                channelDeltaHigh = 4
        elif curNet.bandwidth == 80:
                # 5 GHz channels for these bandwidths center up.
                channelDeltaLow = 8
                channelDeltaHigh = 8
        elif curNet.bandwidth == 160:
                channelDeltaLow = 16
                channelDeltaHigh = 16
            
        lowChannel = curNet.channel - channelDeltaLow
        highChannel = curNet.channel + channelDeltaHigh
        
        if curNet.channel <= 16:
            chartChannelLow = 0
            chartChannelHigh = 17
        else:
            chartChannelLow = 30
            chartChannelHigh = 171
            
        curSeries.clear()
        
        if lowChannel <=chartChannelLow:
            # First point on the graph needs to be at dBm rather than -100
            if curNet.signal >= -100:
                curSeries.append( chartChannelLow, curNet.signal)
            else:
                curSeries.append(chartChannelLow, -100)
        else:
            # Add the zero then our plot
            # curSeries.append(chartChannelLow, -100)
            
            if curNet.signal >= -100:
                curSeries.append( lowChannel, -100)
                curSeries.append( lowChannel, curNet.signal)
            else:
                curSeries.append(lowChannel, -100)
            
        if highChannel >= chartChannelHigh:
            # First point on the graph needs to be at dBm rather than -100
            if curNet.signal >= -100:
                curSeries.append( chartChannelHigh, curNet.signal)
            else:
                curSeries.append(chartChannelHigh, -100)
        else:
            if curNet.signal >= -100:
                curSeries.append( highChannel, curNet.signal)
                curSeries.append( highChannel, -100)
            else:
                curSeries.append(highChannel, -100)
            
            # Add the zero then our plot
            # curSeries.append(chartChannelHigh, -100)
            
    def updateNet(self, curSeries, curNet, channelPlotStart, channelPlotEnd):
        self.plotSSID(curSeries, curNet)
        
    def updateNetOld(self, curSeries, curNet, channelPlotStart, channelPlotEnd):
        curSeries.setName(curNet.ssid)
        
        for i in range(channelPlotStart, channelPlotEnd):
            graphPoint = False
            
            if (curNet.bandwidth == 20):
                if i >= (curNet.channel - 1) and i <=(curNet.channel +1):
                    graphPoint = True
            elif (curNet.bandwidth== 40):
                if curNet.channel <= 16:
                    if curNet.secondaryChannelLocation == 'above':
                        if i >= (curNet.channel - 1) and i <=(curNet.channel +5):
                            graphPoint = True
                    else:
                        if i >= (curNet.channel - 5) and i <=(curNet.channel +1):
                            graphPoint = True
                else:
                    # 5 GHz channels for these bandwidths center up.
                    if i >= (curNet.channel - 3) and i <=(curNet.channel +3):
                        graphPoint = True
            elif (curNet.bandwidth == 80):
                    if i >= (curNet.channel - 8) and i <=(curNet.channel +8):
                        graphPoint = True
            elif (curNet.bandwidth == 160):
                    if i >= (curNet.channel - 15) and i <=(curNet.channel +15):
                        graphPoint = True
                    
            if graphPoint:
                if curNet.signal >= -100:
                    curSeries.replace(i-channelPlotStart, i, curNet.signal)
                else:
                    curSeries.replace(i-channelPlotStart, i, -100)
            else:
                curSeries.replace(i-channelPlotStart, i, -100)
        
    def update5Net(self, curSeries, curNet):
        self.updateNet(curSeries, curNet, 30, 171)
        
    def update24Net(self, curSeries, curNet):
        # Loop to channel 14 + 2 for high end, + 1 for range function = 17
        self.updateNet(curSeries, curNet, 0, 17)

    def addNet(self, newSeries, curNet, channelPlotStart, channelPlotEnd, adding5GHz):
        self.plotSSID(newSeries, curNet)

        if adding5GHz:
            self.chart5.addSeries(newSeries)
            newSeries.attachAxis(self.chart5axisx )
            newSeries.attachAxis(self.chart5.axisY())
        else:
            self.chart24.addSeries(newSeries)
            newSeries.attachAxis(self.chart24axisx)
            newSeries.attachAxis(self.chart24.axisY())

    def addNetOld(self, newSeries, curNet, channelPlotStart, channelPlotEnd, adding5GHz):
        for i in range(channelPlotStart, channelPlotEnd):
            graphPoint = False
            
            if (curNet.bandwidth == 20):
                if i >= (curNet.channel - 1) and i <=(curNet.channel +1):
                    graphPoint = True
            elif (curNet.bandwidth== 40):
                if curNet.channel <= 16:
                    if curNet.secondaryChannelLocation == 'above':
                        if i >= (curNet.channel - 1) and i <=(curNet.channel +5):
                            graphPoint = True
                    else:
                        if i >= (curNet.channel - 5) and i <=(curNet.channel +1):
                            graphPoint = True
                else:
                    # 5 GHz channels for these bandwidths center up.
                    if i >= (curNet.channel - 3) and i <=(curNet.channel +3):
                        graphPoint = True
            elif (curNet.bandwidth == 80):
                    # 5 GHz channels for these bandwidths center up.
                    if i >= (curNet.channel - 8) and i <=(curNet.channel +8):
                        graphPoint = True
            elif (curNet.bandwidth == 160):
                    if i >= (curNet.channel - 15) and i <=(curNet.channel +15):
                        graphPoint = True
                    
            if graphPoint:
                if curNet.signal >= -100:
                    newSeries.append( i, curNet.signal)
                else:
                    newSeries.append(i, -100)
            else:
                    newSeries.append(i, -100)
                
        tmpssid = curNet.ssid
        if (len(tmpssid) == 0):
            tmpssid = '<Unknown>'
            
        newSeries.setName(tmpssid)
        
        if adding5GHz:
            self.chart5.addSeries(newSeries)
            newSeries.attachAxis(self.chart5axisx )
            newSeries.attachAxis(self.chart5.axisY())
        else:
            self.chart24.addSeries(newSeries)
            newSeries.attachAxis(self.chart24axisx)
            newSeries.attachAxis(self.chart24.axisY())
            
    def add5Net(self, newSeries, curNet):
        self.addNet(newSeries, curNet, 30, 171, True)
        
    def add24Net(self, newSeries, curNet):
        self.addNet(newSeries, curNet, 0, 17, False)
        
    def populateUpdateExisting(self, wirelessNetworks, FromAdvanced=False):
        numRows = self.networkTable.rowCount()
        
        if numRows > 0:
            # Loop through each network in the network table, and compare it against the new networks.
            # If we find one, then we already know the network.  Just update it.
            
            # Range goes to last # - 1
            for curRow in range(0, numRows):
                try:
                    curData = self.networkTable.item(curRow, 2).data(Qt.UserRole+1)
                except:
                    curData = None
                    
                if (curData):
                    # We already have the network.  just update it
                    for curKey in wirelessNetworks.keys():
                        curNet = wirelessNetworks[curKey]
                        if curData.getKey() == curNet.getKey():
                            # Match.  Item was already in the table.  Let's update it
                            clientVendor = self.ouiLookup(curNet.macAddr)
                            if clientVendor is None:
                                clientVendor = ''
                            self.networkTable.item(curRow, 1).setText(clientVendor)
                            
                            self.networkTable.item(curRow, 3).setText(curNet.security)
                            self.networkTable.item(curRow, 4).setText(curNet.privacy)
                            self.networkTable.item(curRow, 5).setText(str(curNet.getChannelString()))
                            self.networkTable.item(curRow, 6).setText(str(curNet.frequency))
                            self.networkTable.item(curRow, 7).setText(str(curNet.signal))
                            
                            if FromAdvanced:
                                # There are some fields that are not passed forward from advanced.  So let's update our curNet
                                # attributes in the net object that comes from advanced.
                                curNet.bandwidth = curData.bandwidth
                                curNet.secondaryChannel = curData.secondaryChannel
                                curNet.thirdChannel = curData.thirdChannel
                                curNet.secondaryChannelLocation = curData.secondaryChannelLocation
                                
                            self.networkTable.item(curRow, 8).setText(str(curNet.bandwidth))
                            self.networkTable.item(curRow, 9).setText(str(curNet.utilization))
                            self.networkTable.item(curRow, 10).setText(str(curNet.stationcount))
                            self.networkTable.item(curRow, 11).setText(curNet.lastSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            
                            # Carry forward firstSeen
                            curNet.firstSeen = curData.firstSeen # This is one field to carry forward
                            
                            # Check strongest signal
                            # If we have a stronger signal, or we have an equal signal but we now have GPS
                            # Note the 0.9.  Can be close to store strongest with GPS
                            if curData.strongestsignal > curNet.signal or (curData.strongestsignal > (curNet.signal*0.9) and curData.gps.isValid and (not curNet.strongestgps.isValid)):
                                curNet.strongestsignal = curData.signal
                                curNet.strongestgps.latitude = curData.gps.latitude
                                curNet.strongestgps.longitude = curData.gps.longitude
                                curNet.strongestgps.altitude = curData.gps.altitude
                                curNet.strongestgps.speed = curData.gps.speed
                                curNet.strongestgps.isValid = curData.gps.isValid
                            
                            self.networkTable.item(curRow, 12).setText(curNet.firstSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            if curNet.gps.isValid:
                                self.networkTable.item(curRow, 13).setText('Yes')
                            else:
                                self.networkTable.item(curRow, 13).setText('No')
                                
                            curNet.foundInList = True
                            self.networkTable.item(curRow, 2).setData(Qt.UserRole+1, curNet)
                            
                            # Update series
                            curSeries = self.networkTable.item(curRow, 2).data(Qt.UserRole)
                            
                            # Check if we have a telemetry window
                            if curNet.getKey() in self.telemetryWindows.keys():
                                telemetryWindow = self.telemetryWindows[curNet.getKey()]
                                telemetryWindow.updateNetworkData(curNet)            

                            # 3 scenarios: 
                            # 20 MHz, 1 channel
                            # 40 MHz, 2nd channel above/below or non-contiguous for 5 GHz
                            # 80/160 MHz, Specified differently.  It's allocated as a contiguous block
                            if curNet.channel < 15:  # Max 2.4 GHz CENTER channel is 14
                                # 2.4 GHz
                                # range function goes to max-1
                                self.update24Net(curSeries, curNet)
                            else:
                                # 5 GHz
                                self.update5Net(curSeries, curNet)
                                        
                            break  # We found one, so don't bother looping through more

    def getNextColor(self):
        nextColor = colors[self.nextColor]
        self.nextColor += 1
        
        if (self.nextColor >= len(colors)):
            self.nextColor = 0
            
        return nextColor

    def createNewSeries(self, nextColor, parentChart):
        newSeries = QHoverLineSeries(parentChart) # QLineSeries()
        pen = QPen(nextColor)
        pen.setWidth(2)
        newSeries.setPen(pen)
        
        return newSeries

    def populateTable(self, wirelessNetworks, FromAdvanced=False):
        self.updateLock.acquire()
        
        # Update existing if we have it (this will mark the networ's foundInList flag if we did
        self.populateUpdateExisting(wirelessNetworks, FromAdvanced)

        addedNetworks = 0
        firstTableLoad = False
        
        for curKey in wirelessNetworks.keys():
            # Don't add duplicate
            curNet = wirelessNetworks[curKey]
            if (curNet.foundInList):
                continue

            addedNetworks += 1
            
            # ----------- Update the plots -------------------
            nextColor = self.getNextColor()
            
            # 3 scenarios: 
            # 20 MHz, 1 channel
            # 40 MHz, 2nd channel above/below or non-contiguous for 5 GHz
            # 80/160 MHz, Specified differently.  It's allocated as a contiguous block
            if curNet.channel < 15:
                # 2.4 GHz
                newSeries = self.createNewSeries(nextColor, self.chart24)
                self.add24Net(newSeries, curNet)
            else:
                # 5 GHz
                newSeries = self.createNewSeries(nextColor, self.chart5)
                self.add5Net(newSeries, curNet)
                
            # ----------- Update the Table -------------------
            # Do the table second so we can attach the series to it.
            
            rowPosition = self.networkTable.rowCount()
            rowPosition -= 1
            addedFirstRow = False
            if rowPosition < 0:
                addedFirstRow = True
                rowPosition = 0
                firstTableLoad = True
                
            self.networkTable.insertRow(rowPosition)
            
            # Just make sure we don't get an extra blank row
            if (addedFirstRow):
                self.networkTable.setRowCount(1)

            self.networkTable.setItem(rowPosition, 0, QTableWidgetItem(curNet.macAddr))
            tmpssid = curNet.ssid
            if (len(tmpssid) == 0):
                tmpssid = '<Unknown>'
                
            newSeries.setName(tmpssid)

            newSSID = QTableWidgetItem(tmpssid)
            ssidBrush = QBrush(nextColor)
            newSSID.setForeground(ssidBrush)
            # You can bind more than one data.  See this: 
            # https://stackoverflow.com/questions/2579579/qt-how-to-associate-data-with-qtablewidgetitem
            newSSID.setData(Qt.UserRole, newSeries)
            newSSID.setData(Qt.UserRole+1, curNet)
            newSSID.setData(Qt.UserRole+2, None)
            
            clientVendor = self.ouiLookup(curNet.macAddr)
            if clientVendor is None:
                clientVendor = ''
            self.networkTable.setItem(rowPosition, 1, QTableWidgetItem(clientVendor))
            self.networkTable.setItem(rowPosition, 2, newSSID)
            self.networkTable.setItem(rowPosition, 3, QTableWidgetItem(curNet.security))
            self.networkTable.setItem(rowPosition, 4, QTableWidgetItem(curNet.privacy))
            self.networkTable.setItem(rowPosition, 5, IntTableWidgetItem(str(curNet.getChannelString())))
            self.networkTable.setItem(rowPosition, 6, IntTableWidgetItem(str(curNet.frequency)))
            self.networkTable.setItem(rowPosition, 7,  IntTableWidgetItem(str(curNet.signal)))
            self.networkTable.setItem(rowPosition, 8, IntTableWidgetItem(str(curNet.bandwidth)))
            self.networkTable.setItem(rowPosition, 9, FloatTableWidgetItem(str(curNet.utilization)))
            self.networkTable.setItem(rowPosition, 10, IntTableWidgetItem(str(curNet.stationcount)))
            
            self.networkTable.setItem(rowPosition, 11, DateTableWidgetItem(curNet.lastSeen.strftime("%m/%d/%Y %H:%M:%S")))
            self.networkTable.setItem(rowPosition, 12, DateTableWidgetItem(curNet.firstSeen.strftime("%m/%d/%Y %H:%M:%S")))
            if curNet.gps.isValid:
                self.networkTable.setItem(rowPosition, 13, QTableWidgetItem('Yes'))
            else:
                self.networkTable.setItem(rowPosition, 13, QTableWidgetItem('No'))

        self.ageOut()

        if firstTableLoad:
            self.networkTable.resizeColumnsToContents()
            
        if addedNetworks > 0:
            if self.networkTableSortIndex >=0:
                self.networkTable.sortItems(self.networkTableSortIndex, self.networkTableSortOrder )
                
        self.checkTelemetryWindows()
        
        # Last formatting tweaks on network table
        # self.networkTable.resizeColumnsToContents()
        # self.networkTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        
        self.updateLock.release()

    def checkTelemetryWindows(self):
        # See if we have any telemetry windows that are no longer in the network table and not visible
        # Update numRows in case we removed any above
        numRows = self.networkTable.rowCount()
        
        if (numRows > 0) and (len(self.telemetryWindows.keys()) > 0):
            # Build key list just once to cut down # of loops
            netKeyList = []
            for i in range(0, numRows):
                curNet = self.networkTable.item(i, 2).data(Qt.UserRole+1)
                netKeyList.append(curNet.getKey())
                
            try:
                # If the length of this dictionary changes it may throw an exception.
                # We'll just pick it up next pass.  This is low-priority cleanup
                
                keysToRemove = []
                
                for curKey in self.telemetryWindows.keys():
                    # For each telemetry window we have stored,
                    # If it's no longer in the network table and it's not visible
                    # (Meaning the window was closed but we still have an active Window object)
                    # Let's inform the window to close and remove it from the list
                    if curKey not in netKeyList:
                        curWin = self.telemetryWindows[curKey]
                        if not curWin.isVisible():
                            curWin.close()
                            keysToRemove.append(curKey)
                            
                # Have to separate the iteration and the removal or you'll get a "list changed during iteration" exception
                for curKey in keysToRemove:
                    del self.telemetryWindows[curKey]

            except:
                pass
        
    def ageOut(self):
        numRows = self.networkTable.rowCount()

        if self.cbAgeOut.isChecked() and numRows > 0:
            # Handle if timeout checkbox is checked
            maxTime = datetime.datetime.now() - datetime.timedelta(minutes=3)

            rowPosition = numRows - 1  #  convert count to index
            # range goes to last 
            for i in range(rowPosition, -1, -1):
                try:
                    curData = self.networkTable.item(i, 2).data(Qt.UserRole+1)
                    
                    # Age out
                    if curData.lastSeen < maxTime:
                        curSeries = self.networkTable.item(i, 2).data(Qt.UserRole)
                        if curData.channel < 20:
                            self.chart24.removeSeries(curSeries)
                        else:
                            self.chart5.removeSeries(curSeries)
                            
                        self.networkTable.removeRow(i)
                        
                except:
                    curData = None
                    self.networkTable.removeRow(i)

        
    def onInterface(self):
        pass
            
    def onClearData(self):
        self.networkTable.setRowCount(0)
        self.chart24.removeAllSeries()
        self.spectrum24Line = None
        self.chart5.removeAllSeries()
        
        
    def openFileDialog(self, fileSpec="CSV Files (*.csv);;All Files (*)"):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "",fileSpec, options=options)
        if fileName:
            return fileName
        else:
            return None
 
    def saveFileDialog(self, fileSpec="CSV Files (*.csv);;All Files (*)"):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self,"QFileDialog.getSaveFileName()","",fileSpec, options=options)
        if fileName:
            return fileName
        else:
            return None

    def onImportIWData(self):
        fileName = self.openFileDialog("iw scan output Files (*.iw);;All Files (*)")

        if not fileName:
            return
            
        wirelessNetworks = {}
        
        try:
            f = open(fileName, "r")
        except:
            QMessageBox.question(self, 'Error',"Unable to open file " + fileName, QMessageBox.Ok)
            return
        
        # this will be a list
        self.setWaitCursor()
        
        fileLines = f.readlines()
        f.close()
        
        if len(fileLines) > 0:
            wirelessNetworks = WirelessEngine.parseIWoutput(fileLines)
        
        if len(wirelessNetworks) > 0:
            self.populateTable(wirelessNetworks)
        
        self.setArrowCursor()
        
    def onImportJSON(self):
        fileName = self.openFileDialog("JSON Files (*.json);;All Files (*)")

        if not fileName:
            return
            
        if not os.path.isfile(fileName):
            QMessageBox.question(self, 'Error','File ' + fileName + " doesn't exist.", QMessageBox.Ok)
            return
        
        self.setWaitCursor()
        
        wirelessNetworks = {}
        json_data = ""
        
        with open(fileName, 'r') as f:
            json_data = f.read()
        
        try:
            netDict = json.loads(json_data)
        except:
            self.setArrowCursor()
            QMessageBox.question(self, 'Error',"Unable to parse JSON data.", QMessageBox.Ok)
            return
        
        if not 'wifi-aps' in netDict:
            self.setArrowCursor()
            QMessageBox.question(self, 'Error',"JSON appears to be the wrong format (no wifi-aps tag).", QMessageBox.Ok)
            return
            
        netList = netDict['wifi-aps']
        
        for curNet in netList:
            newNet = WirelessNetwork.createFromJsonDict(curNet)
            wirelessNetworks[newNet.getKey()] = newNet
            
        if len(wirelessNetworks) > 0:
            self.onClearData()
            self.populateTable(wirelessNetworks)

        self.setArrowCursor()
            
    def onImportCSV(self):
        fileName = self.openFileDialog()

        if not fileName:
            return
            
        if not os.path.isfile(fileName):
            QMessageBox.question(self, 'Error','File ' + fileName + " doesn't exist.", QMessageBox.Ok)
            return
        
        wirelessNetworks = {}
        
        with open(fileName, 'r') as f:
            self.setWaitCursor()
            reader = csv.reader(f)
            raw_list = list(reader)
            
            # remove blank lines
            while [] in raw_list:
                raw_list.remove([])
                
            if len(raw_list) > 1:
                # Check header row looks okay
                if raw_list[0][0] != 'macAddr' or (len(raw_list[0]) < 22):
                    self.setArrowCursor()
                    QMessageBox.question(self, 'Error',"File format doesn't look like an exported scan.", QMessageBox.Ok)
                    return
                        
                # Ignore header row
                try:
                    for i in range (1, len(raw_list)):
                        if (len(raw_list[i]) >= 22):
                            newNet = WirelessNetwork()
                            newNet.macAddr=raw_list[i][0]
                            # 1 will be vendor
                            newNet.ssid = raw_list[i][2].replace('"', '')
                            newNet.security = raw_list[i][3]
                            newNet.privacy = raw_list[i][4]
                            
                            # Channel could be primary+secondary
                            channelstr = raw_list[i][5]
                            
                            if '+' in channelstr:
                                newNet.channel = int(channelstr.split('+')[0])
                                newNet.secondaryChannel = int(channelstr.split('+')[1])
                                
                                if newNet.secondaryChannel > newNet.channel:
                                    newNet.secondaryChannelLocation = 'above'
                                else:
                                    newNet.secondaryChannelLocation = 'below'
                            else:
                                newNet.channel = int(raw_list[i][5])
                                newNet.secondaryChannel = 0
                                newNet.secondaryChannelLocation = 'none'
                            
                            newNet.frequency = int(raw_list[i][6])
                            newNet.signal = int(raw_list[i][7])
                            newNet.strongestsignal = int(raw_list[i][8])
                            newNet.bandwidth = int(raw_list[i][9])
                            newNet.lastSeen = parser.parse(raw_list[i][10])
                            newNet.firstSeen = parser.parse(raw_list[i][11])
                            newNet.gps.isValid = stringtobool(raw_list[i][12])
                            newNet.gps.latitude = float(raw_list[i][13])
                            newNet.gps.longitude = float(raw_list[i][14])
                            newNet.gps.altitude = float(raw_list[i][15])
                            newNet.gps.speed = float(raw_list[i][16])
                            newNet.strongestgps.isValid = stringtobool(raw_list[i][17])
                            newNet.strongestgps.latitude = float(raw_list[i][18])
                            newNet.strongestgps.longitude = float(raw_list[i][19])
                            newNet.strongestgps.altitude = float(raw_list[i][20])
                            newNet.strongestgps.speed = float(raw_list[i][21])
                            
                            if not newNet.strongestgps.isValid and newNet.gps.isValid:
                                newNet.strongestgps.isValid = True
                                newNet.strongestgps.latitude = newNet.gps.latitude
                                newNet.strongestgps.longitude = newNet.gps.longitude
                                newNet.strongestgps.altitude = newNet.gps.altitude
                                newNet.strongestgps.speed = newNet.gps.speed
                                
                            # Added utilization and station count on the end to not mess up any saved files.
                            if (len(raw_list[i]) >= 24):
                                newNet.utilization = float(raw_list[i][22])
                                newNet.stationcount = int(raw_list[i][23])

                            wirelessNetworks[newNet.getKey()] = newNet
                except:
                    QMessageBox.question(self, 'Error',"File format doesn't look like an exported scan.", QMessageBox.Ok)

        if len(wirelessNetworks) > 0:
            self.onClearData()
            self.populateTable(wirelessNetworks)

        self.setArrowCursor()

    def onExportJSON(self):
        fileName = self.saveFileDialog("JSON Files (*.json);;All Files (*)")

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
        
        self.updateLock.acquire()
        
        numItems = self.networkTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            self.updateLock.release()
            return

        # This will create a dictionary with an item named 'wifi-aps' which will contain a list of networks
        outputdict = {}
        netlist = []
        
        self.setWaitCursor()
        
        for i in range(0, numItems):
            curData = self.networkTable.item(i, 2).data(Qt.UserRole+1)
            netlist.append(curData.toJsondict())
            
        outputdict['wifi-aps'] = netlist
        
        outputstr=json.dumps(outputdict)
        outputFile.write(outputstr)
        
        outputFile.close()
        
        self.setArrowCursor()
        
        self.updateLock.release()
        
    def onExportCSV(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
            
        outputFile.write('macAddr,vendor,SSID,Security,Privacy,Channel,Frequency,Signal Strength,Strongest Signal Strength,Bandwidth,Last Seen,First Seen,GPS Valid,Latitude,Longitude,Altitude,Speed,Strongest GPS Valid,Strongest Latitude,Strongest Longitude,Strongest Altitude,Strongest Speed,% Utilization,# of Stations\n')

        self.updateLock.acquire()

        numItems = self.networkTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            self.updateLock.release()
            return

        self.setWaitCursor()
        
        for i in range(0, numItems):
            curData = self.networkTable.item(i, 2).data(Qt.UserRole+1)

            outputFile.write(curData.macAddr  + ',' + self.networkTable.item(i, 1).text() + ',"' + curData.ssid + '",' + curData.security + ',' + curData.privacy)
            outputFile.write(',' + curData.getChannelString() + ',' + str(curData.frequency) + ',' + str(curData.signal) + ',' + str(curData.strongestsignal) + ',' + str(curData.bandwidth) + ',' +
                                    curData.lastSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + curData.firstSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + 
                                    str(curData.gps.isValid) + ',' + str(curData.gps.latitude) + ',' + str(curData.gps.longitude) + ',' + str(curData.gps.altitude) + ',' + str(curData.gps.speed) + ',' + 
                                    str(curData.strongestgps.isValid) + ',' + str(curData.strongestgps.latitude) + ',' + str(curData.strongestgps.longitude) + ',' + str(curData.strongestgps.altitude) + ',' + str(curData.strongestgps.speed) + ',' +
                                    str(curData.utilization) + ',' + str(curData.stationcount) + '\n')
            
        outputFile.close()
        
        self.setArrowCursor()
        
        self.updateLock.release()
        
    def center(self):
        # Get our geometry
        qr = self.frameGeometry()
        # Find the desktop center point
        cp = QDesktopWidget().availableGeometry().center()
        # Move our center point to the desktop center point
        qr.moveCenter(cp)
        # Move the top-left point of the application window to the top-left point of the qr rectangle, 
        # basically centering the window
        self.move(qr.topLeft())
        
    def requestRemoteInterfaces(self):
        url = "http://" + self.remoteAgentIP + ":" + str(self.remoteAgentPort) + "/wireless/interfaces"
        statusCode, responsestr = makeGetRequest(url)
        
        if statusCode == 200:
            try:
                interfaces = json.loads(responsestr)
                
                retList = interfaces['interfaces']
                return statusCode, retList
            except:
                return statusCode, None
        else:
            return statusCode, None

    def onRemoteAgentListener(self):
        if not self.agentListenerWindow:
            self.agentListenerWindow = AgentListenerDialog(mainWin=self)
            
        self.agentListenerWindow.show()
        self.agentListenerWindow.activateWindow()
        
    def onAgentListenerClosed(self):
        if self.agentListenerWindow:
            self.agentListenerWindow.close()
            self.agentListenerWindow = None

    def getAgentIPandPort(self):
        if self.remoteAgentUp:
            agentIP = self.remoteAgentIP
            agentPort = self.remoteAgentPort
            specIsGood = True
        else:
            text, okPressed = QInputDialog.getText(self, "Remote Agent","Please provide the <IP>:<port> of the remote agent\nor specify 'auto' to launch agent listener\n(auto requires agent to be on the same subnet and started with the --sendannounce flag):", QLineEdit.Normal, "127.0.0.1:8020")
            if (not okPressed) or text == '':
                return False, "", 0
            
            # Validate the input
            p = re.compile('^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]{1,5})')
            specIsGood = True
            try:
                agentSpec = p.search(text).group(1)
                agentIP = agentSpec.split(':')[0]
                agentPort = int(agentSpec.split(':')[1])
                
                if agentPort < 1 or agentPort > 65535:
                    QMessageBox.question(self, 'Error',"Port must be in an acceptable IP range (1-65535)", QMessageBox.Ok)
                    specIsGood = False
            except:
                if text.upper() == 'AUTO':
                    # Need to close the agent listener window.  Need it to get the info and it'll lock the listening port.
                    if self.agentListenerWindow:
                        QMessageBox.question(self, 'Error',"Please close the agent listener window first.", QMessageBox.Ok)
                        specIsGood = False
                    else:
                        agentIP, agentPort, accepted = AgentListenerDialog.getAgent()
                        specIsGood = accepted
                else:
                    QMessageBox.question(self, 'Error',"Please enter it in the format <IP>:<port>", QMessageBox.Ok)
                    specIsGood = False
        
        return specIsGood, agentIP, agentPort
    
    def onRemoteFiles(self):
        specIsGood, agentIP, agentPort = self.getAgentIPandPort()
        
        if not specIsGood:
            return

        filesWin = RemoteFilesDialog(self,agentIP, agentPort, self)
        filesWin.exec()
        
    def onRemoteAgentConfig(self):
        specIsGood, agentIP, agentPort = self.getAgentIPandPort()
        
        if not specIsGood:
            return
            
        # Now we can connect.
        # Request the current config state.  If all is well, open the dialog,
        # if it fails, notify the user
        retVal, retmsg, startupCfg, runningCfg = requestRemoteConfig(agentIP, agentPort)
        
        if retVal != 0:
            QMessageBox.question(self, 'Error',retmsg, QMessageBox.Ok)
            return

        # There is both a global and class-based version of this function.
        # The global can take parameters, the class version uses the remote agent config settings
        retVal, interfaces = requestRemoteInterfaces(agentIP, agentPort)
        
        if retVal != 200:
            QMessageBox.question(self, 'Error','Unable to get remote interfaces.', QMessageBox.Ok)
            return
        
        configDialog = AgentConfigDialog(startupCfg, runningCfg, interfaces, agentIP, agentPort)
        configDialog.exec()

    def stopSpectrum24Line(self):
        if self.spectrum24Line:
            if self.btShowSpectrum:
                self.btSpectrumTimer.stop()
            elif self.hackrfShowSpectrum24:
                self.hackrfSpectrumTimer.stop()
                
            self.spectrum24Line.clear()
            self.chart24.removeSeries(self.spectrum24Line)
            self.spectrum24Line = None
            self.btShowSpectrum = False
            self.btLastSpectrumState = False
            self.menuBtSpectrum.setChecked(False)
            self.menuHackrfSpectrum24.setChecked(False)
            self.hackrftShowSpectrum24 = False
            self.hackrfLastSpectrumState24 = False

    def stopSpectrum5Line(self):
        if self.spectrum5Line:
            self.hackrfSpectrumTimer.stop()
            self.spectrum5Line.clear()
            self.chart5.removeSeries(self.spectrum5Line)
            self.spectrum5Line = None
            self.hackrfShowSpectrum5 = False
            self.hackrfLastSpectrumState5 = False
            self.menuHackrfSpectrum5.setChecked(False)
            
    def onRemoteAgent(self):
        if (self.menuRemoteAgent.isChecked() == self.lastRemoteState):
            # There's an extra bounce in this for some reason.
            return
        
        if self.menuRemoteAgent.isChecked():
            # We're transitioning to a remote agent
            text, okPressed = QInputDialog.getText(self, "Remote Agent","Please provide the <IP>:<port> of the remote agent\nor specify 'auto' to launch agent listener\n(auto requires agent to be on the same subnet and started with the --sendannounce flag):", QLineEdit.Normal, "127.0.0.1:8020")
            if okPressed and text != '':
                # Validate the input
                p = re.compile('^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]{1,5})')
                specIsGood = True
                try:
                    agentSpec = p.search(text).group(1)
                    self.remoteAgentIP = agentSpec.split(':')[0]
                    self.remoteAgentPort = int(agentSpec.split(':')[1])
                    
                    if self.remoteAgentPort < 1 or self.remoteAgentPort > 65535:
                        QMessageBox.question(self, 'Error',"Port must be in an acceptable IP range (1-65535)", QMessageBox.Ok)
                        self.menuRemoteAgent.setChecked(False)
                        specIsGood = False
                except:
                    if text.upper() == 'AUTO':
                        # Need to close the agent listener window.  Need it to get the info and it'll lock the listening port.
                        if self.agentListenerWindow:
                            QMessageBox.question(self, 'Error',"Please close the agent listener window first.", QMessageBox.Ok)
                            specIsGood = False
                        else:
                            self.remoteAgentIP, self.remoteAgentPort, accepted = AgentListenerDialog.getAgent()
                            specIsGood = accepted
                    else:
                        QMessageBox.question(self, 'Error',"Please enter it in the format <IP>:<port>", QMessageBox.Ok)
                        self.menuRemoteAgent.setChecked(False)
                        specIsGood = False
                    
                if not specIsGood:
                    self.menuRemoteAgent.setChecked(False)
                    self.remoteAgentUp = False
                    return
                    
                # Check that the agent is actually online:
                if not portOpen(self.remoteAgentIP, self.remoteAgentPort):
                    QMessageBox.question(self, 'Error',"The agent does not appear to be up or is unreachable.", QMessageBox.Ok)
                    self.remoteAgentUp = False
                    self.menuRemoteAgent.setChecked(False)
                    return
                    
                # If we're here we're probably good.
                reply = QMessageBox.question(self, 'Question',"Would you like to just do 1 scan pass when pressing scan?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

                if reply == QMessageBox.Yes:
                    self.remoteAutoUpdates = False
                else:
                    self.remoteAutoUpdates = True

                # Configure the GUI.
                self.lblInterface.setText("Remote Interface")
                statusCode, interfaces = self.requestRemoteInterfaces()
                
                if statusCode != 200:
                    QMessageBox.question(self, 'Error',"An error occurred getting the remote interfaces.  Please check that the agent is running.", QMessageBox.Ok)
                    self.menuRemoteAgent.setChecked(False)
                    self.lblInterface.setText("Local Interface")
                    self.statusBar().showMessage('An error occurred getting the remote interfaces')
                    return
                    
                # Okay, we have interfaces.  Let's load them
                self.combo.clear()

                # Double check your objects
                if interfaces is not None and len(interfaces) > 0:
                    for curInterface in interfaces:
                        self.combo.addItem(curInterface)
                else:
                    self.statusBar().showMessage('No wireless interfaces found.')

                # Now we can signal that the remote agent is up.
                
                self.remoteAgentUp = True
                self.missedAgentCycles = 0
                
                self.lastRemoteState = self.menuRemoteAgent.isChecked() 

                # HackRF spectrum
                errcode, errmsg, hashackrf, scan24running, scan5running = remoteHackrfStatus(self.remoteAgentIP, self.remoteAgentPort)
                if errcode != 0:
                    self.statusBar().showMessage('An error occurred getting hackrf status')
                    hashackrf = False
                    scan24running = False
                    scan5running = False
                
                self.remoteHasHackrf = hashackrf
                
                self.menuHackrfSpectrum24.setEnabled(self.remoteHasHackrf)
                self.menuHackrfSpectrum5.setEnabled(self.remoteHasHackrf)
                self.menuHackrfSpectrum24.setChecked(scan24running)
                self.menuHackrfSpectrum5.setChecked(scan5running)

                if scan24running:
                    # disable bluetooth spectrum and 5 spectrum
                    self.menuHackrfSpectrum5.setEnabled(False)
                    self.menuBtSpectrum.setEnabled(False)
                    self.menuBtSpectrum.setChecked(False)
                elif scan5running:
                    # disable 24 hackrf spectrum
                    self.menuHackrfSpectrum24.setEnabled(False)
                    
                # Deal with bluetooth.
                # If we're running local spectrum or discovery, stop it.
                # Then reconfigure based on remote agent state
                
                # Local Spectrum
                if self.bluetooth:
                    self.bluetooth.stopScanning()
                    
                errcode, errmsg, self.hasRemoteBluetooth, self.hasRemoteUbertooth, scanRunning, discoveryRunning, beaconRunning = getRemoteBluetoothRunningServices(self.remoteAgentIP, self.remoteAgentPort)
                if errcode == 0:
                    self.setBluetoothMenu()
                    self.menuBtSpectrum.setChecked(scanRunning)
                    self.btShowSpectrum = scanRunning
                    self.btLastSpectrumState = scanRunning
                    if scanRunning:
                        self.btSpectrumTimer.start(self.btSpectrumTimeoutRemote)
                    else:
                        self.stopSpectrum24Line()
                        
                    self.btBeacon = beaconRunning
                    self.menuBtBeacon.setChecked(beaconRunning)
                    self.btLastBeaconState = self.btBeacon

                    self.setBluetoothMenu()
                else:
                    # Error state
                    self.btShowSpectrum = False
                    self.btLastSpectrumState = False
                    self.stopSpectrum24Line()
                        
                    self.menuBtSpectrum.setChecked(False)
                    self.btSpectrumTimer.stop()
                
                    self.btBeacon = False
                    self.btLastBeaconState = self.btBeacon
                    
                    self.menuBtBeacon.setEnabled(False)
                    self.menuBtSpectrum.setEnabled(False)
                    # self.menuBtSpectrumGain.setEnabled(False)
                    
                self.onGPSStatus()
            else:
                # Stay local.
                self.menuRemoteAgent.setChecked(False)
                self.remoteAgentUp = False
                self.setBluetoothMenu()
        else:
            # We're transitioning local
            self.remoteAgentUp = False

            self.lblInterface.setText("Local Interface")
            self.combo.clear()
            interfaces=WirelessEngine.getInterfaces()
            
            if (len(interfaces) > 0):
                for curInterface in interfaces:
                    self.combo.addItem(curInterface)
            else:
                self.statusBar().showMessage('No wireless interfaces found.')

            self.lastRemoteState = self.menuRemoteAgent.isChecked() 
            self.onGPSStatus()
            
            self.btShowSpectrum = False
            if self.spectrum24Line:
                self.stopSpectrum24Line()
                
            self.menuBtSpectrum.setChecked(False)
            self.menuHackrfSpectrum24.setChecked(False)
            self.menuHackrfSpectrum5.setChecked(False)
            self.menuHackrfSpectrum24.setEnabled(self.hackrf.hasHackrf)
            self.menuHackrfSpectrum5.setEnabled(self.hackrf.hasHackrf)
                    
            self.btBeacon = self.bluetooth.beaconRunning()
            self.menuBtBeacon.setChecked(self.btBeacon)
            self.btLastBeaconState = self.btBeacon

            self.setBluetoothMenu()
            
        self.checkNotifyBluetoothDiscovery()
        self.checkNotifyAdvancedScan()

    def onAbout(self):
        aboutMsg = "Sparrow-wifi 802.11 WiFi Graphic Analyzer\n"
        aboutMsg += "Written by ghostop14\n"
        aboutMsg += "https://github.com/ghostop14\n\n"
        aboutMsg += "This application is open source and licensed\n"
        aboutMsg += "under the terms fo the GPL version 3\n"
        
        QMessageBox.question(self, 'Message',aboutMsg, QMessageBox.Ok)
        
    def closeEvent(self, event):
        # reply = QMessageBox.question(self, 'Message',"Are you sure to quit?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        # if reply == QMessageBox.Yes:
            # event.accept()
        #else:
            # event.ignore()       
        
        if self.scanRunning:
            if not self.remoteAgentUp:
                self.scanThread.signalStop = True
            else:
                if self.remoteScanThread:
                    self.remoteScanThread.signalStop = True

        for curKey in self.telemetryWindows.keys():
            curWindow = self.telemetryWindows[curKey]
            try:
                curWindow.close()
                self.telemetryWindows[curKey] = None
            except:
                pass
                    
        if self.advancedScan:
            self.advancedScan.close()
            self.advancedScan = None
            
        if self.bluetoothWin:
            self.bluetoothWin.close()
            self.bluetoothWin = None
        
        if self.btBeacon:
            self.bluetooth.stopBeacon()
                
        if self.agentListenerWindow:
            self.agentListenerWindow.close()
            self.agentListenerWindow = None
            
        if self.gpsCoordWindow:
            self.gpsCoordWindow.close()
            self.gpsCoordWindow = None
            
        if self.bluetooth:
            self.bluetooth.stopScanning()
            
        self.hackrf.stopScanning()
        
        event.accept()

    def onRescanInterfaces(self):
        self.combo.clear()
        
        interfaces=WirelessEngine.getInterfaces()
        
        if (len(interfaces) > 0):
            for curInterface in interfaces:
                self.combo.addItem(curInterface)

# -------  Main Routine and Bluetooth Function -------------------------
if __name__ == '__main__':
    # Code to add paths
    dirname, filename = os.path.split(os.path.abspath(__file__))
    
    if dirname not in sys.path:
        sys.path.insert(0, dirname)
    pluginsdir = dirname+'/plugins'
    if  os.path.exists(pluginsdir):
        if pluginsdir not in sys.path:
            sys.path.insert(0,pluginsdir)
        if  os.path.isfile(pluginsdir + '/falconwifi.py'):
            from falconwifidialogs import AdvancedScanDialog
            hasFalcon = True
            
    app = QApplication(sys.argv)
    mainWin = mainWindow()
    result = app.exec_()
    sys.exit(result)
    # Some thread is still blocking...
    # os._exit(result)

    
