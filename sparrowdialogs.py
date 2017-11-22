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

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QApplication, QLabel, QComboBox, QLineEdit, QPushButton, QAbstractItemView
from PyQt5.QtWidgets  import QFileDialog, QSpinBox, QDesktopWidget, QMessageBox, QTableWidget, QHeaderView,QTableWidgetItem,  QMenu, QAction
from sparrowtablewidgets import DateTableWidgetItem, FloatTableWidgetItem, IntTableWidgetItem
from PyQt5.QtCore import Qt,QTimer
from PyQt5 import QtCore

from socket import *
import datetime
from threading import Thread, Lock
from time import sleep
import requests
import json
import re
# import urllib
from urllib.request import urlretrieve

import os

from sparrowmap import MapEngine
from sparrowwifiagent import FileSystemFile
from sparrowbluetooth import SparrowBluetooth, BluetoothDevice
from telemetry import BluetoothTelemetry
from sparrowmap import MapMarker
from wirelessengine import WirelessEngine

# ------------------  Global File Dialogs ------------------------------
def openFileDialog(fileSpec="CSV Files (*.csv);;All Files (*)"):    
    options = QFileDialog.Options()
    options |= QFileDialog.DontUseNativeDialog
    fileName, _ = QFileDialog.getOpenFileName(None,"QFileDialog.getOpenFileName()", "",fileSpec, options=options)
    if fileName:
        return fileName
    else:
        return None


def saveFileDialog(fileSpec="CSV Files (*.csv);;All Files (*)"):    
    options = QFileDialog.Options()
    options |= QFileDialog.DontUseNativeDialog
    fileName, _ = QFileDialog.getSaveFileName(None,"QFileDialog.getSaveFileName()","",fileSpec, options=options)
    if fileName:
        return fileName
    else:
        return None


# ------------------  Global functions for agent HTTP requests ------------------------------
def makeGetRequest(url):
    try:
        # Not using a timeout can cause the request to hang indefinitely
        response = requests.get(url, timeout=2)
    except:
        return -1, ""
        
    if response.status_code != 200:
        return response.status_code, ""
        
    htmlResponse=response.text
    return response.status_code, htmlResponse

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
            
            return errcode, errmsg, hasBluetooth, hasUbertooth, spectrumScanRunning, discoveryScanRunning
        except:
            return -1, 'Error parsing response', False, False, False, False
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', False, False, False, False
        
def startRemoteBluetoothDiscoveryScan(agentIP, agentPort, ubertooth):
    if ubertooth:
        # Promiscuous
        url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystartp"
    else:
        # Advertisements only
        url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystarta"
        
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
        
def stopRemoteBluetoothDiscoveryScan(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystop"
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

def getRemoteBluetoothDiscoveryStatus(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystatus"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            tmpDeviceData = responsedict['devices']
            devices = {}
            for curDevice in tmpDeviceData:
                newdevice = BluetoothDevice()
                try:
                    newdevice.fromJsondict(curDevice)
                    devices[newdevice.macAddress] = newdevice
                except:
                    pass
            return errcode, errmsg, devices
        except:
            return -1, 'Error parsing response', None
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', None
        
def getRemoteRecordingsFiles(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/system/getrecordings"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            filelist = []
            try:
                for curFileDict in responsedict['files']:
                    curFile = FileSystemFile()
                    curFile.fromJsondict(curFileDict)
                    filelist.append(curFile)
                return 0, "", filelist
            except:
                return 2, "Error parsing response: " + responsestr, None
        except:
            return 1, "Error parsing response: " + responsestr, None
    else:
        return statusCode, 'Received error code: ' + str(statusCode), None
        
def delRemoteRecordingFiles(remoteIP, remotePort, filelist):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/system/deleterecordings"
    
    filedict={}
    filedict['files'] = filelist
        
    jsonstr = json.dumps(filedict)
    statusCode, responsestr = makePostRequest(url, jsonstr)

    errcode = -1
    errmsg = ""
    
    if statusCode == 200 or statusCode == 400:
        try:
            responsedict = json.loads(responsestr)
            try:
                errcode = responsedict['errcode']
                errmsg = responsedict['errmsg']
            except:
                # response json didn't have the expected field
                if len(responsestr) == 0:
                    errmsg = "Error parsing agent response.  Is it still running?"
                else:
                    errmsg = "Error parsing agent response:" + responsestr                
        except:
            # Parsing json threw exception
            if len(responsestr) == 0:
                errmsg = "Error parsing agent response.  Is it still running?"
            else:
                errmsg = "Error parsing agent response:" + responsestr
    else:
        # This should never happen
        if len(responsestr) == 0:
            errmsg = "Error updating remote agent.  Is it still running?"
        else:
            errmsg = "Error updating remote agent:" + responsestr
            
    return errcode, errmsg

def startRecord(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/system/startrecord/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            try:
                errcode = responsedict['errcode']
                errmsg = responsedict['errmsg']
                return errcode, errmsg
            except:
                return 2, "Error parsing response: " + responsestr
        except:
            return 1, "Error parsing response: " + responsestr
    else:
        return statusCode, 'Received error code: ' + str(statusCode)
        
def stopRecord(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/system/stoprecord"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            try:
                errcode = responsedict['errcode']
                errmsg = responsedict['errmsg']
                return errcode, errmsg
            except:
                return 2, "Error parsing response: " + responsestr
        except:
            return 1, "Error parsing response: " + responsestr
    else:
        return statusCode, 'Received error code: ' + str(statusCode)
        
def makePostRequest(url, jsonstr):
        # use something like jsonstr = json.dumps(somestring) to get the right format
        try:
            response = requests.post(url, data=jsonstr, timeout=2)
        except:
            return -1, ""
        
        htmlResponse=response.text
        return response.status_code, htmlResponse
        
def updateRemoteConfig(remoteIP, remotePort, startupCfg, runningCfg, sendRestart=False):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/system/config"
    
    cfgdict = {}
    cfgdict['startup'] = startupCfg.toJsondict()
    cfgdict['running'] = runningCfg.toJsondict()
    
    if sendRestart:
        cfgdict['rebootagent'] = True
        
    jsonstr = json.dumps(cfgdict)
    statusCode, responsestr = makePostRequest(url, jsonstr)

    errmsg = ""
    
    if statusCode == 200:
        return 0, ""
    elif statusCode == 400:
        # 400 is a JSON response
        try:
            responsedict = json.loads(responsestr)
            try:
                errmsg = responsedict['errmsg']
            except:
                # response json didn't have the expected field
                if len(responsestr) == 0:
                    errmsg = "Error parsing agent response.  Is it still running?"
                else:
                    errmsg = "Error parsing agent response:" + responsestr                
        except:
            # Parsing json threw exception
            if len(responsestr) == 0:
                errmsg = "Error parsing agent response.  Is it still running?"
            else:
                errmsg = "Error parsing agent response:" + responsestr
    else:
        # This should never happen
        if len(responsestr) == 0:
            errmsg = "Error updating remote agent.  Is it still running?"
        else:
            errmsg = "Error updating remote agent:" + responsestr
            
        return -1, errmsg

#  -----------  DB Settings ----------------------------
# Note: This is not used in the main GUI
class DBSettings(object):
    SQLITE = 1
    POSTGRES = 2
    
    def __init__(self):
        super().__init__()
        self.dbMode = DBSettings.SQLITE
        self.db = ""  # This will be a file for SQLite, or a database name for Postgres
        self.tablename = "wirelessnetworks"
        
        # These are only needed for Postgres
        self.hostip = ""
        self.username = ""
        self.password = ""
        
class DBSettingsDialog(QDialog):
    def __init__(self, parent = None):
        super(DBSettingsDialog, self).__init__(parent)

        self.dbMode = DBSettings.SQLITE  # 1 = SQLite, 2 = Postgres
        # layout = QVBoxLayout(self)

        # DB Type droplist
        self.lblDBType = QLabel("DB Type", self)
        self.lblDBType.setGeometry(30, 26, 100, 30)
        
        self.combo = QComboBox(self)
        self.combo.move(110, 30)
        self.combo.addItem("SQLite")
        self.combo.addItem("Postgres")
        self.combo.currentIndexChanged.connect(self.onDBChanged)

        # SQLLite:
        self.lblDB = QLabel("DB/File: ", self)
        self.lblDB.move(30, 84)
        self.dbinput = QLineEdit(self)
        self.dbinput.setGeometry(110, 80, 250, 20)
        self.btnOpen = QPushButton("&Open", self)
        self.btnOpen.move(380, 80)
        self.btnOpen.clicked.connect(self.onFileClicked)

        spacing = 35
        # Table name
        self.lblDBHost = QLabel("Table Name: ", self)
        self.lblDBHost.move(30, 88+spacing)
        self.dbtable = QLineEdit(self)
        self.dbtable.setText("wirelessnetworks")
        self.dbtable.setGeometry(110, 84+spacing, 200, 20)

        # Postgres:
        self.lblDBHost = QLabel("Host IP: ", self)
        self.lblDBHost.move(30, 87+spacing*2)
        self.dbhost = QLineEdit(self)
        self.dbhost.setText("127.0.0.1")
        self.dbhost.setGeometry(110, 84+spacing*2, 200, 20)
        
        self.lblDBUser = QLabel("Username: ", self)
        self.lblDBUser.move(30, 90+spacing*3)
        self.dbuser = QLineEdit(self)
        self.dbuser.setGeometry(110, 88+spacing*3, 200, 20)
        
        self.lblDBPass = QLabel("Password: ", self)
        self.lblDBPass.move(30, 86+spacing*4)
        self.dbpass = QLineEdit(self)
        self.dbpass.setEchoMode(QLineEdit.Password)
        self.dbpass.setGeometry(110, 84+spacing*4, 200, 20)

        # Start in SQLite Mode:
        self.setPostgresVisible(False)
        
        # OK and Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.move(170, 280)
        #layout.addWidget(buttons)
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("Database Settings")


    def setPostgresVisible(self, vis):
        self.lblDBHost.setVisible(vis)
        self.dbhost.setVisible(vis)
        self.lblDBUser.setVisible(vis)
        self.dbuser.setVisible(vis)
        self.lblDBPass.setVisible(vis)
        self.dbpass.setVisible(vis)

    def onFileClicked(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
        else:
            self.dbinput.setText(fileName)

    def onDBChanged(self, index):
        self.dbMode = index
        
        if index == 0:
            self.setPostgresVisible(False)
        else:
            self.setPostgresVisible(True)
        
    def saveFileDialog(self):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self,"QFileDialog.getSaveFileName()","","SQLite3 Files (*.sqlite3);;All Files (*)", options=options)
        if fileName:
            return fileName
        else:
            return None

    def getDBSettings(self):
        dbSettings = DBSettings()
        dbSettings.dbMode = self.dbMode
        dbSettings.db = self.dbinput.text()
        dbSettings.hostip = self.dbhost.text()
        dbSettings.user = self.dbuser.text()
        dbSettings.password = self.dbpass.text()
        dbSettings.tableName = self.dbtable.text()
        
        return dbSettings
        
    # static method to create the dialog and return (date, time, accepted)
    @staticmethod
    def getSettings(parent = None):
        dialog = DBSettingsDialog(parent)
        result = dialog.exec_()
        # date = dialog.dateTime()
        dbSettings = dialog.getDBSettings()
        return (dbSettings, result == QDialog.Accepted)

class MapSettings(object):
    def __init__(self):
        super().__init__()
        self.maptype = MapEngine.MAP_TYPE_DEFAULT
        self.plotstrongest = True
        self.outputfile = ""
        self.title = ""
        self.maxLabelLength = 15
        
class MapSettingsDialog(QDialog):
    def __init__(self, parent = None, skipControls = False):
        super(MapSettingsDialog, self).__init__(parent)

        self.center()
        
        if skipControls:
            return
            
        # Map Type droplist
        self.lblMapType = QLabel("Map Type", self)
        self.lblMapType.setGeometry(30, 26, 100, 30)
        
        self.combo = QComboBox(self)
        self.combo.setGeometry(115, 30, 140, 30)
        self.combo.addItem("Standard Street")
        self.combo.addItem("Hybrid Satellite")
        self.combo.addItem("Satellite Only")
        self.combo.addItem("Terrain")

        # Plot strongest or last
        self.lblMapType = QLabel("Coord Set", self)
        self.lblMapType.setGeometry(30, 84, 100, 30)
        
        self.comboplot = QComboBox(self)
        self.comboplot.move(115, 84)
        self.comboplot.addItem("Strongest Signal")
        self.comboplot.addItem("Last Signal")

        # File:
        self.lblFile = QLabel("Output File: ", self)
        self.lblFile.move(30, 124)
        self.fileinput = QLineEdit(self)
        self.fileinput.setGeometry(115, 120, 250, 20)
        self.btnOpen = QPushButton("&Save", self)
        self.btnOpen.move(380, 120)
        self.btnOpen.clicked.connect(self.onFileClicked)

        spacing = 35
        # Table name
        self.lblTitle = QLabel("Map Title: ", self)
        self.lblTitle.move(30, 129+spacing)
        self.title = QLineEdit(self)
        self.title.setText("Access Point Map")
        self.title.setGeometry(115, 124+spacing, 200, 20)

        self.lblMaxLen = QLabel("Max Label Length: ", self)
        self.lblMaxLen.move(30, 133+spacing*2)
        self.spinMaxLen = QSpinBox(self)
        self.spinMaxLen.setRange(1, 100)
        
        self.spinMaxLen.setValue(15)
        self.spinMaxLen.setGeometry(150, 125+spacing*2, 50, 28)

        # OK and Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.move(170, 280)
        #layout.addWidget(buttons)
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("Map Settings")

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
        
    def onFileClicked(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
        else:
            self.fileinput.setText(fileName)

    def saveFileDialog(self):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self,"QFileDialog.getSaveFileName()","","HTML Files (*.html);;All Files (*)", options=options)
        if fileName:
            return fileName
        else:
            return None

    def done(self, result):
        if result == QDialog.Accepted:
            if len(self.fileinput.text()) == 0:
                QMessageBox.question(self, 'Error',"Please provide an output file.", QMessageBox.Ok)

                return
            
        super().done(result)
        
    def getMapSettings(self):
        mapSettings = MapSettings()
        
        strType = self.combo.currentText()
        
        if (strType == 'Hybrid Satellite'):
            mapSettings.mapType = MapEngine.MAP_TYPE_HYBRID
        elif (strType == 'Satellite Only'):
            mapSettings.mapType = MapEngine.MAP_TYPE_SATELLITE_ONLY
        elif (strType == 'Terrain'):
            mapSettings.mapType = MapEngine.MAP_TYPE_TERRAIN
        else:
            mapSettings.mapType = MapEngine.MAP_TYPE_DEFAULT
            
        if (self.comboplot.currentText() == 'Strongest Signal'):
            mapSettings.plotstrongest = True
        else:
            mapSettings.plotstrongest = False

        mapSettings.title = self.title.text()
        mapSettings.outputfile = self.fileinput.text()
        mapSettings.maxLabelLength = self.spinMaxLen.value()
        
        return mapSettings
        
    # static method to create the dialog and return (date, time, accepted)
    @staticmethod
    def getSettings(parent = None):
        dialog = MapSettingsDialog(parent)
        result = dialog.exec_()
        # date = dialog.dateTime()
        mapSettings = dialog.getMapSettings()
        return (mapSettings, result == QDialog.Accepted)

class TelemetryMapSettings(MapSettings):
    def __init__(self):
        super().__init__()
        self.inputfile = ""
        self.plotNthPoint = 1
        
class TelemetryMapSettingsDialog(MapSettingsDialog):
    def __init__(self, parent = None):
        super(TelemetryMapSettingsDialog, self).__init__(parent, True)

        # Map Type droplist
        self.lblMapType = QLabel("Map Type", self)
        self.lblMapType.setGeometry(30, 26, 100, 30)
        
        self.combo = QComboBox(self)
        self.combo.setGeometry(115, 30, 140, 30)
        self.combo.addItem("Standard Street")
        self.combo.addItem("Hybrid Satellite")
        self.combo.addItem("Satellite Only")
        self.combo.addItem("Terrain")

        # Input File:
        self.lblInputFile = QLabel("Input File: ", self)
        self.lblInputFile.move(30, 84)
        self.inputfileinput = QLineEdit(self)
        self.inputfileinput.setGeometry(115, 84, 250, 20)
        self.btnInputOpen = QPushButton("&Open", self)
        self.btnInputOpen.move(380, 84)
        self.btnInputOpen.clicked.connect(self.onInputFileClicked)

        # Output File:
        self.lblFile = QLabel("Output File: ", self)
        self.lblFile.move(30, 124)
        self.fileinput = QLineEdit(self)
        self.fileinput.setGeometry(115, 120, 250, 20)
        self.btnOpen = QPushButton("&Save", self)
        self.btnOpen.move(380, 120)
        self.btnOpen.clicked.connect(self.onFileClicked)

        spacing = 35
        # Map Title
        self.lblTitle = QLabel("Map Title: ", self)
        self.lblTitle.move(30, 129+spacing)
        self.title = QLineEdit(self)
        self.title.setText("SSID Map")
        self.title.setGeometry(115, 124+spacing, 200, 20)

        # Max label length
        self.lblMaxLen = QLabel("Max Label Length: ", self)
        self.lblMaxLen.move(30, 133+spacing*2)
        self.spinMaxLen = QSpinBox(self)
        self.spinMaxLen.setRange(1, 100)
        self.spinMaxLen.setValue(15)
        self.spinMaxLen.setGeometry(145, 126+spacing*2, 50, 28)
        
        # Nth Point
        self.lblplot = QLabel("Plot every ", self)
        self.lblplot.move(30, 133+spacing*3)
        self.spinplot = QSpinBox(self)
        self.spinplot.setRange(1, 1000)
        self.lblplot2 = QLabel("points", self)
        self.lblplot2.move(170, 133+spacing*3)
        
        self.spinplot.setValue(1)
        self.spinplot.setGeometry(115, 125+spacing*3, 50, 28)

        # OK and Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.move(170, 280)
        
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("SSID Map Settings")

    def onInputFileClicked(self):
        fileName = self.openFileDialog()

        if not fileName:
            return
        else:
            self.inputfileinput.setText(fileName)
        
    def done(self, result):
        if result == QDialog.Accepted:
            if len(self.inputfileinput.text()) == 0:
                QMessageBox.question(self, 'Error',"Please provide an input file.", QMessageBox.Ok)
                return
                
            if len(self.fileinput.text()) == 0:
                QMessageBox.question(self, 'Error',"Please provide an output file.", QMessageBox.Ok)
                return
            
        super().done(result)
        
    def openFileDialog(self):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "","CSV Files (*.csv);;All Files (*)", options=options)
        if fileName:
            return fileName
        else:
            return None
 
    def getMapSettings(self):
        mapSettings = TelemetryMapSettings()
        
        strType = self.combo.currentText()
        
        if (strType == 'Hybrid Satellite'):
            mapSettings.mapType = MapEngine.MAP_TYPE_HYBRID
        elif (strType == 'Satellite Only'):
            mapSettings.mapType = MapEngine.MAP_TYPE_SATELLITE_ONLY
        elif (strType == 'Terrain'):
            mapSettings.mapType = MapEngine.MAP_TYPE_TERRAIN
        else:
            mapSettings.mapType = MapEngine.MAP_TYPE_DEFAULT
            
        mapSettings.title = self.title.text()
        mapSettings.outputfile = self.fileinput.text()
        mapSettings.maxLabelLength = self.spinMaxLen.value()
        
        mapSettings.inputfile = self.inputfileinput.text()
        
        mapSettings.plotNthPoint = self.spinplot.value()
        
        return mapSettings
        
    # static method to create the dialog and return (date, time, accepted)
    @staticmethod
    def getSettings(parent = None):
        dialog = TelemetryMapSettingsDialog(parent)
        result = dialog.exec_()
        # date = dialog.dateTime()
        mapSettings = dialog.getMapSettings()
        return (mapSettings, result == QDialog.Accepted)

# ------------------  UDP Listen thread  ------------------------------
class AgentListenerThread(Thread):
    def __init__(self, parentWin, port):
        super(AgentListenerThread, self).__init__()
        self.signalStop = False
        self.threadRunning = False
        
        self.port = port
        self.parentWin = parentWin
        
        self.sock = socket(AF_INET, SOCK_DGRAM)
        self.server_address = ('0.0.0.0', self.port)
        
        # This can throw an exception if it can't bind
        self.sock.bind(self.server_address)
        
    def sendAnnounce(self):
        try:
            self.broadcastSocket.sendto(bytes('sparrowwifiagent', "utf-8"),self.broadcastAddr)
        except:
            pass
        
    def run(self):
        
        if not self.sock:
            self.threadRunning = False
            return
            
        self.threadRunning = True
        
        self.sock.settimeout(6) # receive timeout
        
        while (not self.signalStop):
            try:
                data, address = self.sock.recvfrom(1024)
                self.parentWin.agentAnnounce.emit(address[0], self.port)
            except timeout:
                pass
                    
        self.threadRunning = False
        
        if (self.sock):
            self.sock.close()
        
# ------------------  Agent Listener  ------------------------------
class AgentListenerDialog(QDialog):
    agentAnnounce = QtCore.pyqtSignal(str, int)

    def __init__(self, mainWin = None,  parent = None):
        super(AgentListenerDialog, self).__init__(parent)

        self.parentWin = mainWin
        
        self.broadcastSocket = socket(AF_INET, SOCK_DGRAM)
        self.broadcastSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        self.broadcastSocket.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)        
        
        self.agentAnnounce.connect(self.onAgentAnnounce)

        # Map Type droplist
        self.lblAgentPort = QLabel("Agent Port", self)
        self.lblAgentPort.setGeometry(10, 10, 100, 30)

        self.spinPort = QSpinBox(self)
        self.spinPort.setRange(1, 65535)
        self.spinPort.setValue(8020)
        self.spinPort.setGeometry(100, 10, 100, 28)
        self.spinPort.valueChanged.connect(self.spinChanged)
        
        # self.broadcastAddr=('255.255.255.255', int(self.spinPort.value()))
        self.agentListenerThread = AgentListenerThread(self,  int(self.spinPort.value()))
        self.agentListenerThread.start()
        
        self.agentTable = QTableWidget(self)
        self.agentTable.setColumnCount(2)
        self.agentTable.setShowGrid(True)
        self.agentTable.setHorizontalHeaderLabels(['IP Address', 'Port'])
        self.agentTable.setGeometry(10, 30, 100, 30)
        self.agentTable.resizeColumnsToContents()
        self.agentTable.setRowCount(0)
        self.agentTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.agentTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
        
        # OK and Cancel buttons
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.move(170, 280)

        self.setBlackoutColors()
        
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("Remote Agent Detection")
        self.center()

    def setBlackoutColors(self):
        self.agentTable.setStyleSheet("background-color: black;gridline-color: white;color: white")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black}"
        self.agentTable.horizontalHeader().setStyleSheet(headerStyle)
        self.agentTable.verticalHeader().setStyleSheet(headerStyle)
        
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
        
    def spinChanged(self):
        self.agentListenerThread.signalStop = True
        
        while (self.agentListenerThread.threadRunning):
            sleep(1)
            
        self.agentListenerThread = None
        
        self.agentListenerThread = AgentListenerThread(self,  int(self.spinPort.value()))
        self.agentListenerThread.start()

    def done(self, result):
        super().done(result)
        
        if self.parentWin:
            self.parentWin.agentListenerClosed.emit()
        
    def closeEvent(self, event):
        self.agentListenerThread.signalStop = True
        
        while (self.agentListenerThread.threadRunning):
            sleep(1)

        if self.parentWin:
            self.parentWin.agentListenerClosed.emit()
            
        event.accept()
            
    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()
        self.agentTable.setGeometry(10, 50, size.width()-20, size.height()-100)
        self.buttons.move(size.width()/2-80, size.height() - 40)

    def onTableHeadingClicked(self, logical_index):
        header = self.agentTable.horizontalHeader()
        order = Qt.DescendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )
        self.agentTable.sortItems(logical_index, order )
        
    def agentInTable(self, ipAddr, port):
        rowPosition = self.agentTable.rowCount()
        if rowPosition <= 0:
            return False
            
        for curRow in range(0, rowPosition):
            if (self.agentTable.item(curRow, 0).text() == ipAddr) and (self.agentTable.item(curRow, 1).text() == str(port)):
                return True
                
        return False
        
    def onAgentAnnounce(self, ipAddr, port):
        if not self.agentInTable(ipAddr, port):
            rowPosition = self.agentTable.rowCount()
            rowPosition -= 1
            addedFirstRow = False
            if rowPosition < 0:
                addedFirstRow = True
                rowPosition = 0
                
            self.agentTable.insertRow(rowPosition)
            
            # Just make sure we don't get an extra blank row
            if (addedFirstRow):
                self.agentTable.setRowCount(1)

            self.agentTable.setItem(rowPosition, 0, QTableWidgetItem(ipAddr))
            self.agentTable.setItem(rowPosition, 1, IntTableWidgetItem(str(port)))
            self.agentTable.resizeColumnsToContents()
            self.agentTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        
    def getAgentInfo(self):
        curRow = self.agentTable.currentRow()
        if curRow < 0:
            return '', 0
            
        return self.agentTable.item(curRow, 0).text(), int(self.agentTable.item(curRow, 1).text())
        
    # static method to create the dialog and return (date, time, accepted)
    @staticmethod
    def getAgent(parent = None):
        dialog = AgentListenerDialog(parent)
        result = dialog.exec_()
        # date = dialog.dateTime()
        agentIP, port = dialog.getAgentInfo()
        return (agentIP, port, result == QDialog.Accepted)


# ------------------  GPS Coordinate  ------------------------------
class GPSCoordDialog(QDialog):
    visibility = QtCore.pyqtSignal(bool)
    
    def __init__(self, mainWin, parent = None):
        super(GPSCoordDialog, self).__init__(parent)

        self.visibility.connect(self.onVisibilityChanged)
        
        self.mainWin = mainWin
        

        # Set up GPS check timer
        self.gpsTimer = QTimer()
        self.gpsTimer.timeout.connect(self.onGPSTimer)
        self.gpsTimer.setSingleShot(True)
        self.gpsTimerTimeout = 2000
        self.gpsTimer.start(self.gpsTimerTimeout)
        
        self.lastGPS = None
        self.firstUpdate = True
        
        self.lblMsg = QLabel("Newest coordinates are at the top", self)
        self.lblMsg.move(10, 20)

        self.historyTable = QTableWidget(self)
        self.historyTable.setColumnCount(6)
        self.historyTable.setShowGrid(True)
        self.historyTable.setHorizontalHeaderLabels(['Timestamp','Valid','Latitude', 'Longitude', 'Altitude', 'Speed'])
        self.historyTable.setGeometry(10, 30, 100, 30)
        self.historyTable.resizeColumnsToContents()
        self.historyTable.setRowCount(0)
        self.historyTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
       #  self.historyTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
       
        self.ntRightClickMenu = QMenu(self)
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopy)
        self.ntRightClickMenu.addAction(newAct)
 
        # Attach it to the table
        self.historyTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.historyTable.customContextMenuRequested.connect(self.showNTContextMenu)
        
        self.setBlackoutColors()
        
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("GPS Coordinate Viewer")
        self.center()

        # initial update:
        if self.mainWin:
            curGPS = self.mainWin.getCurrentGPS()
            self.updateTable(curGPS)

    def setBlackoutColors(self):
        self.historyTable.setStyleSheet("QTableView {background-color: black;gridline-color: white;color: white} QTableCornerButton::section{background-color: white;}")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black;} QHeaderView::down-arrow,QHeaderView::up-arrow {background: none;}"
        self.historyTable.horizontalHeader().setStyleSheet(headerStyle)
        self.historyTable.verticalHeader().setStyleSheet(headerStyle)
        
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
        
    def closeEvent(self, event):
        self.gpsTimer.stop()
        event.accept()
            
    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()
        self.historyTable.setGeometry(10, 50, size.width()-20, size.height()-60)

    def showNTContextMenu(self, pos):
        curRow = self.historyTable.currentRow()
        
        if curRow == -1:
            return
            
        self.ntRightClickMenu.exec_(self.historyTable.mapToGlobal(pos))
 
    def onCopy(self):
        curRow = self.historyTable.currentRow()
        curCol = self.historyTable.currentColumn()
        
        if curRow == -1 or curCol == -1:
            return
        
        curText = self.historyTable.item(curRow, curCol).text()
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)
        
    def updateTable(self, curGPS):        
        if curGPS == self.lastGPS:
            # Don't update if nothing's changed and we're not on our first iteration
            return
        
        self.lastGPS = curGPS  # Set for the next pass
        
        rowCount = self.historyTable.rowCount()
        rowCount -= 1
        addedFirstRow = False
        if rowCount < 0:
            addedFirstRow = True
            rowCount = 0
            
        # Insert new at the top
        self.historyTable.insertRow(0)

        # Just make sure we don't get an extra blank row
        if (addedFirstRow):
            self.historyTable.setRowCount(1)

        rowPosition = 0 # Always at the first row
        self.historyTable.setItem(rowPosition, 0, DateTableWidgetItem(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")))
        if (curGPS.isValid):
            self.historyTable.setItem(rowPosition,1, QTableWidgetItem('Yes'))
        else:
            self.historyTable.setItem(rowPosition,1, QTableWidgetItem('No'))
            
        self.historyTable.setItem(rowPosition, 2, FloatTableWidgetItem(str(curGPS.latitude)))
        self.historyTable.setItem(rowPosition, 3, FloatTableWidgetItem(str(curGPS.longitude)))
        self.historyTable.setItem(rowPosition, 4, FloatTableWidgetItem(str(curGPS.altitude)))
        self.historyTable.setItem(rowPosition, 5, FloatTableWidgetItem(str(curGPS.speed)))

        # limit to 20 entries
        if (self.historyTable.rowCount() > 20):
            self.historyTable.setRowCount(20)
        
    def onGPSTimer(self):
        if not self.mainWin:
            # We'll just take one shot coming in here for debug purposes.  Technically we don't need to come in here
            # if there's no main win
            return

        curGPS = self.mainWin.getCurrentGPS()
        
        self.updateTable(curGPS)
            
        self.gpsTimer.start(self.gpsTimerTimeout)
        
    def hideEvent(self, event):
        self.visibility.emit(False)
        
    def showEvent(self, event):
        self.visibility.emit(True)
        
    def onVisibilityChanged(self, visible):
        if not visible:
            self.gpsTimer.stop()
        else:
            if not self.gpsTimer.isActive():
                self.gpsTimer.start(self.gpsTimerTimeout)
            
# ------------------  GPS Coordinate  ------------------------------
class BluetoothDialog(QDialog):
    visibility = QtCore.pyqtSignal(bool)
    
    def __init__(self, mainWin, bluetooth,  useRemoteAgent=False, remoteAgentIP="",  remoteAgentPort=8020, parent = None):
        super().__init__()
        self.mainWin = mainWin
        self.visibility.connect(self.onVisibilityChanged)

        self.usingRemoteAgent = useRemoteAgent
        self.remoteAgentIP = remoteAgentIP
        self.remoteAgentPort = remoteAgentPort

        self.updateWindowTitle()

        self.telemetryWindows = {}
        
        self.updateLock = Lock()
        self.telemetryWindows = {}
        self.bluetooth = bluetooth
        self.hasBlueHydra = True
        self.scanPromiscuous = True
        
        # Set up timer
        self.btTimer = QTimer()
        self.btTimer.timeout.connect(self.onBtTimer)
        self.btTimer.setSingleShot(True)
        self.btTimerTimeout = 500
        
        self.firstUpdate = True

        self.lblInterface = QLabel("Scan Type:", self)
        self.lblInterface.setGeometry(5, 10, 70, 30)
        
        self.comboScanType = QComboBox(self)
        self.comboScanType.move(90, 15)

        self.fillScanTypes()

        # Scan Button
        self.btnScan = QPushButton("&Scan", self)
        self.btnScan.setCheckable(True)
        self.btnScan.setShortcut('Ctrl+S')
        self.btnScan.setStyleSheet("background-color: rgba(0,128,192,255); border: none;")
        self.btnScan.setGeometry(298, 12, 120, 27)
        self.btnScan.clicked[bool].connect(self.onScanClicked)

        # Map Button
        self.btnMap = QPushButton("&Map", self)
        self.btnMap.setStyleSheet("background-color: rgba(0,128,192,255);")
        self.btnMap.clicked.connect(self.onMap)
        
        # Export Button
        self.btnExport = QPushButton("&Export", self)
        self.btnExport.setStyleSheet("background-color: rgba(0,128,192,255);")
        self.btnExport.clicked.connect(self.onExportClicked)
        
        # Data table
        self.bluetoothTable = QTableWidget(self)
        self.bluetoothTable.setColumnCount(11)
        self.bluetoothTable.setShowGrid(True)
        self.bluetoothTable.setHorizontalHeaderLabels(['uuid', 'Address', 'Name', 'Company', 'Manufacturer','Type', 'RSSI','TX Power','Est Range (m)','Last Seen','GPS'])
        self.bluetoothTable.setGeometry(10, 30, 100, 30)
        self.bluetoothTable.resizeColumnsToContents()
        self.bluetoothTable.setRowCount(0)
        self.bluetoothTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
       #  self.historyTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
        self.bluetoothTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
        self.bluetoothTable.setSelectionMode( QAbstractItemView.SingleSelection )
       
        self.ntRightClickMenu = QMenu(self)
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopy)
        self.ntRightClickMenu.addAction(newAct)

        self.ntRightClickMenu.addSeparator()
        newAct = QAction('Telemetry', self)        
        newAct.setStatusTip('View network telemetry data')
        newAct.triggered.connect(self.onShowTelemetry)
        self.ntRightClickMenu.addAction(newAct)

        self.btTableSortOrder = Qt.DescendingOrder
        self.btTableSortIndex = -1
 
        # Attach it to the table
        self.bluetoothTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bluetoothTable.customContextMenuRequested.connect(self.showNTContextMenu)
        
        self.setBlackoutColors()
        
        # self.setGeometry(self.geometry().x(), self.geometry().y(), 700,500)
        desktopSize = QApplication.desktop().screenGeometry()
        self.mainWidth = desktopSize.width() * 2 / 3
        self.mainHeight = desktopSize.height() / 2
        self.resize(self.mainWidth, self.mainHeight)

        self.center()
        
        if not self.usingRemoteAgent:
            if self.mainWin.hasUbertooth and (not os.path.isfile('/opt/bluetooth/blue_hydra/bin/blue_hydra')):
                QMessageBox.question(self, 'Error',"Blue Hydra not found at /opt/bluetooth/blue_hydra/bin/blue_hydra.  Promiscuous scans will fail.", QMessageBox.Ok)

    def fillScanTypes(self):
        self.comboScanType.clear()
        
        if not self.usingRemoteAgent:
            # Local
            if self.mainWin.hasUbertooth:
                self.comboScanType.addItem('Promiscuous Discovery')
                
            self.comboScanType.addItem('LE Advertisement Discovery')
        else:
            if self.mainWin.hasRemoteUbertooth:
                self.comboScanType.addItem('Promiscuous Discovery')
                
            self.comboScanType.addItem('LE Advertisement Discovery')
            
    def onShowTelemetry(self):
        self.updateLock.acquire()
        
        curRow = self.bluetoothTable.currentRow()
        
        if curRow == -1:
            self.updateLock.release()
            return
        
        curNet = self.bluetoothTable.item(curRow, 0).data(Qt.UserRole)
        
        if curNet == None:
            self.updateLock.release()
            return
       
        if curNet.getKey() not in self.telemetryWindows.keys():
            telemetryWindow = BluetoothTelemetry()
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
        
    def setLocal(self):
        self.usingRemoteAgent = False
        
        self.updateWindowTitle()
        self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
        self.btnScan.setText('&Scan')
        self.comboScanType.setEnabled(True)
        self.fillScanTypes()
        
    def setRemoteAgent(self, agentIP, agentPort):
        self.usingRemoteAgent = True
        self.remoteAgentIP = agentIP
        self.remoteAgentPort = agentPort

        # Check if we're running local.  If so stop it
        if self.bluetooth.discoveryRunning():
            self.bluetooth.stopDiscovery()

        self.fillScanTypes()
        
        self.updateWindowTitle()
        self.checkScanAlreadyRunning()

    def scanRunning(self):
        return 'Stop' in self.btnScan.text()
        
    def checkScanAlreadyRunning(self):
        errcode, errmsg, hasBluetooth, hasUbertooth, spectrumScanRunning, discoveryScanRunning =  getRemoteBluetoothRunningServices(self.remoteAgentIP, self.remoteAgentPort)      
        
        if errcode == 0:
            if discoveryScanRunning:
                self.btnScan.setStyleSheet("background-color: rgba(255,0,0,255); border: none;")
                self.btnScan.setText('&Stop scanning')
                self.comboScanType.setEnabled(False)
            else:
                self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
                self.btnScan.setText('&Scan')
                self.comboScanType.setEnabled(True)
        else:
                QMessageBox.question(self, 'Error',"Error getting remote agent discovery status: " + errmsg, QMessageBox.Ok)

                self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
                self.btnScan.setText('&Scan')
                self.comboScanType.setEnabled(True)
                
    def updateWindowTitle(self):
        title = 'Bluetooth'
        
        if self.usingRemoteAgent:
            title += " - " + self.remoteAgentIP + ":" + str(self.remoteAgentPort)
            
        self.setWindowTitle(title)
        
    def onScanClicked(self, pressed):
        if self.btnScan.isChecked():
            # Scanning is on.  Turn red to indicate click would stop
            if self.comboScanType.currentText() == 'Promiscuous Discovery':
                ubertooth = True
                
                if not self.usingRemoteAgent:
                    if not self.mainWin.hasUbertooth:
                        self.btnScan.setChecked(False)
                        return
                else:
                    if not self.mainWin.hasRemoteUbertooth:
                        self.btnScan.setChecked(False)
                        return
            else:
                ubertooth = False
                
            self.btnScan.setStyleSheet("background-color: rgba(255,0,0,255); border: none;")
            self.btnScan.setText('&Stop scanning')
            self.comboScanType.setEnabled(False)
            
            if not self.mainWin.remoteAgentUp:
                self.scanPromiscuous = ubertooth
                self.bluetooth.startDiscovery(ubertooth)
            else:
                self.setCursor(Qt.WaitCursor)
                errcode, errmsg = startRemoteBluetoothDiscoveryScan(self.remoteAgentIP, self.remoteAgentPort, ubertooth)
                self.setCursor(Qt.ArrowCursor)

                if errcode != 0:
                    QMessageBox.question(self, 'Error',"Could not start remote scan: " + errmsg, QMessageBox.Ok)
                    self.btnScan.setChecked(False)
                    self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
                    self.btnScan.setText('&Scan')
                    self.comboScanType.setEnabled(True)
                    return
                    
            self.btTimer.start(self.btTimerTimeout)
        else:
            self.btTimer.stop()
            
            self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
            self.btnScan.setText('&Scan')
            self.comboScanType.setEnabled(True)
            self.setCursor(Qt.WaitCursor)
            if not self.mainWin.remoteAgentUp:
                self.bluetooth.stopDiscovery()
            else:
                errcode, errmsg = stopRemoteBluetoothDiscoveryScan(self.remoteAgentIP, self.remoteAgentPort)
            self.setCursor(Qt.ArrowCursor)

    def setBlackoutColors(self):
        self.bluetoothTable.setStyleSheet("background-color: black;gridline-color: white;color: white")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black}"
        self.bluetoothTable.horizontalHeader().setStyleSheet(headerStyle)
        self.bluetoothTable.verticalHeader().setStyleSheet(headerStyle)
        
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
        
    def closeEvent(self, event):
        self.btTimer.stop()
        
        if not self.usingRemoteAgent:
            self.bluetooth.stopDiscovery()

        for curKey in self.telemetryWindows.keys():
            curWindow = self.telemetryWindows[curKey]
            try:
                curWindow.close()
                self.telemetryWindows[curKey] = None
            except:
                pass
        
        if self.mainWin:
            self.mainWin.bluetoothDiscoveryClosed.emit()
            
        event.accept()
            
    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()

        if size.width() < 500:
            self.setGeometry(size.x(), size.y(), 800, size.height())
        
        size = self.geometry()
        
        self.bluetoothTable.setGeometry(10, 50, size.width()-20, size.height()-60)
        
        self.btnExport.setGeometry(size.width()-130, 10, 120, 25)
        self.btnMap.setGeometry(size.width()-280, 10, 120, 25)

    def showNTContextMenu(self, pos):
        curRow = self.bluetoothTable.currentRow()
        
        if curRow == -1:
            return
            
        self.ntRightClickMenu.exec_(self.bluetoothTable.mapToGlobal(pos))
 
    def onCopy(self):
        curRow = self.bluetoothTable.currentRow()
        curCol = self.bluetoothTable.currentColumn()
        
        if curRow == -1 or curCol == -1:
            return
        
        curText = self.bluetoothTable.item(curRow, curCol).text()
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)
        
        
    def tableEntryChanged(self, device1, device2):
        return True
        
        if (device1.lastSeen != device2.lastSeen) or (device1.rssi != device2.rssi):
            if (not self.scanPromiscuous) or (device1.name != device2.name):
                # if we're doing an advertisement scan we won't get the name
                return True
            else:
                return False
        else:
            return False
            
    def updateTable(self, deviceList):        
        self.updateLock.acquire()
        
        rowCount = self.bluetoothTable.rowCount()
        rowCount -= 1
        if rowCount < 0:
            rowCount = 0

        # Update existing
        numRows = self.bluetoothTable.rowCount()
        
        if numRows > 0:
            # Loop through each network in the network table, and compare it against the new networks.
            # If we find one, then we already know the network.  Just update it.
            
            # Range goes to last # - 1
            for curRow in range(0, numRows):
                try:
                    curData = self.bluetoothTable.item(curRow, 0).data(Qt.UserRole)
                except:
                    curData = None
                    
                if (curData):
                    # We already have the network.  just update it
                    for curKey in deviceList.keys():
                        curDevice = deviceList[curKey]
                        if curData.getKey() == curDevice.getKey():
                            curDevice.foundInList = True
                            
                            if not self.tableEntryChanged(curData, curDevice):
                                # Nothing has changed, so don't update anything
                                continue
                            
                            curDevice.firstSeen = curData.firstSeen # This is one field to carry forward
                            
                            if self.scanPromiscuous:
                                # Need the other attributes:
                                curDevice.name = curData.name
                                curDevice.manufacturer = curData.manufacturer
                                curDevice.uuid = curData.uuid
                                curDevice.bluetoothDescription = curData.bluetoothDescription

                            if curDevice.txPowerValid and curDevice.iBeaconRange == -1:
                                curDevice.calcRange()
                                
                            # curData is already in the table
                            if curData.strongestRssi > curDevice.rssi or (curData.strongestRssi > (curDevice.rssi*0.9) and curData.gps.isValid and (not curDevice.strongestgps.isValid)):
                                curDevice.strongestRssi = curData.rssi
                                curDevice.strongestgps.latitude = curData.gps.latitude
                                curDevice.strongestgps.longitude = curData.gps.longitude
                                curDevice.strongestgps.altitude = curData.gps.altitude
                                curDevice.strongestgps.speed = curData.gps.speed
                                curDevice.strongestgps.isValid = curData.gps.isValid
                            
                            self.bluetoothTable.item(curRow,2).setText(curDevice.name)
                            self.bluetoothTable.item(curRow, 6).setText(str(curDevice.rssi))
                            
                            if curDevice.txPowerValid:
                                self.bluetoothTable.item(curRow, 7).setText(str(curDevice.txPower))
                            else:
                                self.bluetoothTable.item(curRow, 7).setText('Unknown')
                            
                            if curDevice.iBeaconRange != -1 and curDevice.txPowerValid:
                                self.bluetoothTable.item(curRow, 8).setText(str(curDevice.iBeaconRange))
                            else:
                                self.bluetoothTable.item(curRow, 8).setText('Unknown')
                                
                            self.bluetoothTable.item(curRow, 9).setText(curDevice.lastSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            if (curDevice.gps.isValid):
                                self.bluetoothTable.item(curRow,10).setText('Yes')
                            else:
                                self.bluetoothTable.item(curRow,10).setText('No')
                                
                            self.bluetoothTable.item(curRow, 0).setData(Qt.UserRole, curDevice)

                            # Check if we have a telemetry window
                            if curDevice.getKey() in self.telemetryWindows.keys():
                                telemetryWindow = self.telemetryWindows[curDevice.getKey()]
                                telemetryWindow.updateNetworkData(curDevice)            
                            break

        addedNetworks = 0
        
        for curKey in deviceList.keys():
            curDevice = deviceList[curKey]
            if not curDevice.foundInList:
                addedNetworks += 1
                # Insert new at the top
                self.bluetoothTable.insertRow(0)

                rowPosition = 0 # Always at the first row
                # 'uuid', 'Address', 'name', 'company', 'manufacturer','type', 'RSSI','iBeacon Range','Last Seen','GPS'
                newDevice = QTableWidgetItem(curDevice.uuid)
                newDevice.setData(Qt.UserRole, curDevice)
                self.bluetoothTable.setItem(rowPosition, 0, newDevice)
                
                self.bluetoothTable.setItem(rowPosition,1, QTableWidgetItem(curDevice.macAddress))
                self.bluetoothTable.setItem(rowPosition,2, QTableWidgetItem(curDevice.name))
                self.bluetoothTable.setItem(rowPosition,3, QTableWidgetItem(curDevice.company))
                self.bluetoothTable.setItem(rowPosition,4, QTableWidgetItem(curDevice.manufacturer))

                if curDevice.btType == BluetoothDevice.BT_LE:
                    self.bluetoothTable.setItem(rowPosition,5, QTableWidgetItem('BTLE'))
                else:
                    self.bluetoothTable.setItem(rowPosition,5, QTableWidgetItem('Classic'))

                self.bluetoothTable.setItem(rowPosition, 6, IntTableWidgetItem(str(curDevice.rssi)))
                
                if curDevice.txPowerValid:
                    self.bluetoothTable.setItem(rowPosition, 7, IntTableWidgetItem(str(curDevice.txPower)))
                else:
                    self.bluetoothTable.setItem(rowPosition, 7, IntTableWidgetItem('Unknown'))
                
                if curDevice.iBeaconRange != -1 and curDevice.txPowerValid:
                    self.bluetoothTable.setItem(rowPosition, 8, FloatTableWidgetItem(str(curDevice.iBeaconRange)))
                else:
                    self.bluetoothTable.setItem(rowPosition, 8, FloatTableWidgetItem('Unknown'))
                    
                self.bluetoothTable.setItem(rowPosition, 9, DateTableWidgetItem(curDevice.lastSeen.strftime("%m/%d/%Y %H:%M:%S")))
                if (curDevice.gps.isValid):
                    self.bluetoothTable.setItem(rowPosition,10, QTableWidgetItem('Yes'))
                else:
                    self.bluetoothTable.setItem(rowPosition,10, QTableWidgetItem('No'))
                
        if addedNetworks > 0:
            if self.btTableSortIndex >=0:
                self.bluetoothTable.sortItems(self.btTableSortIndex, self.btTableSortOrder )

        self.updateLock.release()
        
    def onMap(self):
        rowPosition = self.bluetoothTable.rowCount()

        if rowPosition <= 0:
            QMessageBox.question(self, 'Error',"There's no devices in the table.  Please run a scan first.", QMessageBox.Ok)
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
                curData = self.bluetoothTable.item(curRow, 0).data(Qt.UserRole)
            except:
                curData = None
            
            if (curData):
                newMarker = MapMarker()
                
                if len(curData.name) > 0:
                    newMarker.label = curData.name
                else:
                    newMarker.label = curData.macAddress
                    
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
                        
                    newMarker.barCount = WirelessEngine.getSignalQualityFromDB0To5(curData.strongestRssi)
                else:
                    if curData.gps.isValid:
                        newMarker.gpsValid = True
                        newMarker.latitude = curData.gps.latitude
                        newMarker.longitude = curData.gps.longitude
                    else:
                        newMarker.gpsValid = False
                        newMarker.latitude = 0.0
                        newMarker.longitude = 0.0
                        
                    newMarker.barCount = WirelessEngine.getSignalQualityFromDB0To5(curData.rssi)
                
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
            retVal = MapEngine.createMap(mapSettings.outputfile,mapSettings.title,markers, connectMarkers=False, openWhenDone=True, mapType=mapSettings.mapType)
            
            if not retVal:
                QMessageBox.question(self, 'Error',"Unable to generate map to " + mapSettings.outputfile, QMessageBox.Ok)
        
    def onExportClicked(self):
        fileName = saveFileDialog()

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
            
        outputFile.write('uuid,Address,Name,Company,Manufacturer,Type,RSSI,TX Power,Strongest RSSI,Est Range (m),Last Seen,GPS Valid,Latitude,Longitude,Altitude,Speed,Strongest GPS Valid,Strongest Latitude,Strongest Longitude,Strongest Altitude,Strongest Speed\n')

        self.updateLock.acquire()

        numItems = self.bluetoothTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            self.updateLock.release()
            return
           
        for i in range(0, numItems):
            curData = self.bluetoothTable.item(i, 0).data(Qt.UserRole)

            btType = ""
            if curData.btType == BluetoothDevice.BT_LE:
                btType = "BTLE"
            else:
                btType = "Classic"
                
            if curData.txPowerValid:
                txPower = str(curData.txPower)
            else:
                txPower = 'Unknown'
                
            outputFile.write(curData.uuid  + ',' + curData.macAddress + ',"' + curData.name + '","' + curData.company + '","' + curData.manufacturer)
            outputFile.write('","' + btType + '",' + str(curData.rssi) + ',' + str(curData.strongestRssi) + ',' + txPower + ',' + str(curData.iBeaconRange) + ',' +
                                    curData.lastSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + 
                                    str(curData.gps.isValid) + ',' + str(curData.gps.latitude) + ',' + str(curData.gps.longitude) + ',' + str(curData.gps.altitude) + ',' + str(curData.gps.speed) + ',' + 
                                    str(curData.strongestgps.isValid) + ',' + str(curData.strongestgps.latitude) + ',' + str(curData.strongestgps.longitude) + ',' + str(curData.strongestgps.altitude) + ',' + str(curData.strongestgps.speed) + '\n')
            
        outputFile.close()
        
        self.updateLock.release()
                        
    def onBtTimer(self):
        if not self.mainWin:
            # We'll just take one shot coming in here for debug purposes.  Technically we don't need to come in here
            # if there's no main win
            return

        curGPS = self.mainWin.getCurrentGPS()
        
        if self.usingRemoteAgent:
            errcode, errmsg, devices = getRemoteBluetoothDiscoveryStatus(self.remoteAgentIP, self.remoteAgentPort)
        else:
            errcode = 0
            self.bluetooth.updateDeviceList()
            devices= self.bluetooth.devices
        
        if (errcode == 0) and (devices is not None) and (len(devices) > 0):
            now = datetime.datetime.now()
            if not self.usingRemoteAgent:
                self.bluetooth.deviceLock.acquire()
                
            for curKey in devices.keys():
                curDevice = devices[curKey]
                curDevice.manufacturer = self.mainWin.ouiLookup(curDevice.macAddress)
                if curDevice.manufacturer is None:
                    curDevice.manufacturer = ''
                
                if not self.usingRemoteAgent:
                    # Remote agent takes care of this before sending it.
                    elapsedTime =  now - curDevice.lastSeen
                    
                    # This is a little bit of a hack for the BlueHydra side since it can take a while to see devices or have
                    # them show up in the db.  For LE discovery scans this will always be pretty quick.
                    if elapsedTime.total_seconds() < 120:
                        curDevice.gps.copy(curGPS)
                        if curDevice.rssi >= curDevice.strongestRssi:
                            curDevice.strongestRssi = curDevice.rssi
                            curDevice.strongestgps.copy(curGPS)
                
            self.updateTable(devices)
            
            if not self.usingRemoteAgent:
                self.bluetooth.deviceLock.release()
            
        self.btTimer.start(self.btTimerTimeout)
        
    def hideEvent(self, event):
        self.visibility.emit(False)
        
    def showEvent(self, event):
        self.visibility.emit(True)
        
    def onVisibilityChanged(self, visible):
        if not visible:
            self.btTimer.stop()
        else:
            if self.btnScan.isChecked():
                self.btTimer.start(self.btTimerTimeout)

    def onTableHeadingClicked(self, logical_index):
        header = self.bluetoothTable.horizontalHeader()
        order = Qt.DescendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )

        self.btTableSortOrder = order
        self.btTableSortIndex = logical_index
        self.bluetoothTable.sortItems(logical_index, order )
        
            
# ------------------  Agent Configuration  ------------------------------
class AgentConfigDialog(QDialog):
    def __init__(self, startupCfg, runningCfg, interfaces, agentIP='127.0.0.1', agentPort=8020,parent = None):
        super(AgentConfigDialog,  self).__init__(parent)

        self.agentIP = agentIP
        self.agentPort = agentPort
        self.interfaces = interfaces
        
        agentString = agentIP + ":" + str(agentPort)
        
        self.startupCfg = startupCfg
        self.runningCfg = runningCfg
        
        self.lblMsg = QLabel("Startup", self)
        self.lblMsg.move(120, 20)
        self.lblMsg = QLabel("Running", self)
        self.lblMsg.move(250, 20)


        # Cancel Startup Controls
        self.lblMsg = QLabel("Cancel Startup:", self)
        self.lblMsg.move(10, 50)

        self.comboCancelStartupCfgFile = QComboBox(self)
        self.comboCancelStartupCfgFile.move(118, 45)
        self.comboCancelStartupCfgFile.addItem("Yes")
        self.comboCancelStartupCfgFile.addItem("No")
        
        if startupCfg.cancelStart:
            self.comboCancelStartupCfgFile.setCurrentIndex(0)
        else:
            self.comboCancelStartupCfgFile.setCurrentIndex(1)
        
        # Port controls
        self.lblPort = QLabel("Port: ", self)
        self.lblPort.move(10, 90)
        self.spinPortStartup = QSpinBox(self)
        self.spinPortStartup.move(118, 85)
        self.spinPortStartup.setRange(1, 65535)
        self.spinPortStartup.setValue(startupCfg.port)
        
        self.spinPortRunning = QSpinBox(self)
        self.spinPortRunning.move(250, 85)
        self.spinPortRunning.setRange(1, 65535)
        self.spinPortRunning.setValue(runningCfg.port)
        self.spinPortRunning.setEnabled(False)
        
        # Announce controls
        self.lblMsg = QLabel("Announce Agent:", self)
        self.lblMsg.move(10, 130)

        self.comboSendAnnouncementsStartup = QComboBox(self)
        self.comboSendAnnouncementsStartup.move(118, 125)
        self.comboSendAnnouncementsStartup.addItem("Yes")
        self.comboSendAnnouncementsStartup.addItem("No")
        
        if startupCfg.announce:
            self.comboSendAnnouncementsStartup.setCurrentIndex(0)
        else:
            self.comboSendAnnouncementsStartup.setCurrentIndex(1)
        
        self.comboSendAnnouncementsRunning = QComboBox(self)
        self.comboSendAnnouncementsRunning.move(250, 125)
        self.comboSendAnnouncementsRunning.addItem("Yes")
        self.comboSendAnnouncementsRunning.addItem("No")
        
        if runningCfg.announce:
            self.comboSendAnnouncementsRunning.setCurrentIndex(0)
        else:
            self.comboSendAnnouncementsRunning.setCurrentIndex(1)
        
        # RPi LEDs
        self.lblMsg = QLabel("Use RPi LEDs:", self)
        self.lblMsg.move(10, 170)

        self.comboRPiLEDsStartup = QComboBox(self)
        self.comboRPiLEDsStartup.move(118, 165)
        self.comboRPiLEDsStartup.addItem("Yes")
        self.comboRPiLEDsStartup.addItem("No")
        
        if startupCfg.useRPiLEDs:
            self.comboRPiLEDsStartup.setCurrentIndex(0)
        else:
            self.comboRPiLEDsStartup.setCurrentIndex(1)
        
        self.comboRPiLEDsRunning = QComboBox(self)
        self.comboRPiLEDsRunning.move(250, 165)
        self.comboRPiLEDsRunning.addItem("Yes")
        self.comboRPiLEDsRunning.addItem("No")
        
        if runningCfg.useRPiLEDs:
            self.comboRPiLEDsRunning.setCurrentIndex(0)
        else:
            self.comboRPiLEDsRunning.setCurrentIndex(1)
        
        # Record on Startup
        self.lblMsg = QLabel("Record Local:", self)
        self.lblMsg.move(10, 210)

        # self.comboRecordStartup = QComboBox(self)
        # self.comboRecordStartup.move(118, 205)
        # self.comboRecordStartup.addItem("Yes")
        # self.comboRecordStartup.addItem("No")
        
        self.btnRecordStartStop = QPushButton("Start", self)
        self.btnRecordStartStop.move(250, 205)
        self.btnRecordStartStop.clicked.connect(self.onStartStopRecord)
        
        # Record Interface
        self.lblMsg = QLabel("Record Interface:", self)
        self.lblMsg.move(10, 250)

        self.recordInterfaceStartup = QLineEdit(self)
        self.recordInterfaceStartup.setGeometry(118, 245, 100, 25)
        self.recordInterfaceStartup.setText(startupCfg.recordInterface)
        
        self.recordInterfaceRunning = QLineEdit(self)
        self.recordInterfaceRunning.setGeometry(250, 245, 100, 25)
        self.recordInterfaceRunning.setText(runningCfg.recordInterface)
        
        self.btnShowInterfaces = QPushButton("Interfaces", self)
        self.btnShowInterfaces.move(360, 245)
        self.btnShowInterfaces.clicked.connect(self.onShowInterfaces)
        
        if runningCfg.recordRunning:
            self.recordInterfaceRunning.setEnabled(False)
            self.btnRecordStartStop.setText('Stop')
        
        # Mavlink GPS
        self.lblMsg = QLabel("Mavlink GPS:", self)
        self.lblMsg.move(10, 290)

        self.mavlinkGPSStartup = QLineEdit(self)
        self.mavlinkGPSStartup.setGeometry(118, 285, 100, 25)
        self.mavlinkGPSStartup.setText(startupCfg.mavlinkGPS)
        
        # IP Allow List
        self.lblMsg = QLabel("IP Allow List:", self)
        self.lblMsg.move(10, 330)

        self.ipAllowStartup = QLineEdit(self)
        self.ipAllowStartup.setGeometry(118, 325, 100, 25)
        self.ipAllowStartup.setText(startupCfg.ipAllowedList)
        
        self.ipAllowRunning = QLineEdit(self)
        self.ipAllowRunning.setGeometry(250, 325, 100, 25)
        self.ipAllowRunning.setText(runningCfg.ipAllowedList)
        
        # OK and Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.move(145, 380)

        self.btnReboot = QPushButton("Save and Restart", self)
        self.btnReboot.setGeometry(145, 430, 170, 30)
        self.btnReboot.clicked.connect(self.onRestart)

        # Window geometry
        self.setGeometry(self.geometry().x(), self.geometry().y(), 450,480)
        self.setWindowTitle("Agent Configuration:" + agentString)
        self.center()

    def comboTrueFalse(self, combo):
        if combo.currentIndex() == 0:
            return True
        else:
            return False
    
    def validateAllowedIPs(self, allowedIPstr):
        if len(allowedIPstr) > 0:
            ippattern = re.compile('([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})')
            if ',' in allowedIPstr:
                tmpList = allowedIPstr.split(',')
                for curItem in tmpList:
                    ipStr = curItem.replace(' ', '')
                    try:
                        ipValue = ippattern.search(ipStr).group(1)
                    except:
                        QMessageBox.question(self, 'Error','ERROR: Unknown IP pattern: ' + ipStr, QMessageBox.Ok)
                        return False
            else:
                ipStr = allowedIPstr.replace(' ', '')
                try:
                    ipValue = ippattern.search(ipStr).group(1)
                except:
                    QMessageBox.question(self, 'Error','ERROR: Unknown IP pattern: ' + ipStr, QMessageBox.Ok)
                    return False
                    
        return True
    
    def validateMavlink(self, mavlinkstr):
        if mavlinkstr == '3dr' or mavlinkstr == 'sitl':
            return True
            
        # for the moment we'll assume the user knows how to create a custom mavlink connection string.  I know.....
        return True
        
    def validateAndSend(self, sendRestart=False):
        settingsChanged = False
        
        if self.btnRecordStartStop.text() == 'Start':
            # Just make sure we clear the field or it may start recording on us.
            self.recordInterfaceRunning.setText('')

        tmpBool = self.comboTrueFalse(self.comboCancelStartupCfgFile)
        
        if self.startupCfg.cancelStart != tmpBool:
            self.startupCfg.cancelStart = self.comboTrueFalse(self.comboCancelStartupCfgFile)
            settingsChanged = True
        
        tmpBool = self.comboTrueFalse(self.comboSendAnnouncementsStartup)
        
        if self.startupCfg.announce != tmpBool:
            self.startupCfg.announce = self.comboTrueFalse(self.comboSendAnnouncementsStartup)
            settingsChanged = True
            
        tmpBool = self.comboTrueFalse(self.comboSendAnnouncementsRunning)
        if self.runningCfg.announce != tmpBool:
            self.runningCfg.announce = self.comboTrueFalse(self.comboSendAnnouncementsRunning)
            settingsChanged = True
        
        if self.startupCfg.port != int(self.spinPortStartup.value()):
            self.startupCfg.port = int(self.spinPortStartup.value())
            settingsChanged = True
            
        self.runningCfg.port = self.agentPort # Can't change this
        
        tmpBool = self.comboTrueFalse(self.comboRPiLEDsStartup)
        if self.startupCfg.useRPiLEDs != tmpBool:
            self.startupCfg.useRPiLEDs = self.comboTrueFalse(self.comboRPiLEDsStartup)
            settingsChanged = True
            
        tmpBool = self.comboTrueFalse(self.comboRPiLEDsRunning)
        if self.runningCfg.useRPiLEDs != tmpBool:
            self.runningCfg.useRPiLEDs = self.comboTrueFalse(self.comboRPiLEDsRunning)
            settingsChanged = True

        if self.startupCfg.recordInterface != self.recordInterfaceStartup.text():
            settingsChanged = True
            
            if recordOnStartup:
                self.startupCfg.recordInterface = self.recordInterfaceStartup.text()
            else:
                self.startupCfg.recordInterface = ""
            
        if self.runningCfg.recordInterface != self.recordInterfaceRunning.text():
            self.runningCfg.recordInterface = self.recordInterfaceRunning.text().replace(' ', '')
            settingsChanged = True
        
        mavlinkstr = self.mavlinkGPSStartup.text().replace(' ', '')
        
        if not self.validateMavlink(mavlinkstr):
            return False
        
        if self.startupCfg.mavlinkGPS != mavlinkstr:
            self.startupCfg = mavlinkstr
            settingsChanged = True
            
        iptext = self.ipAllowStartup.text().replace(' ', '')
        if not self.validateAllowedIPs(iptext):
            return False
            
        if self.startupCfg.ipAllowedList != iptext:
            self.startupCfg.ipAllowedList = iptext
            settingsChanged = True
        
        iptext = self.ipAllowRunning.text().replace(' ', '')
        if not self.validateAllowedIPs(iptext):
            return False
            
        if self.runningCfg.ipAllowedList != iptext:
            self.runningCfg.ipAllowedList = iptext
            settingsChanged = True
        
        # Transmit updates here and notify the user if anything went wrong
        if settingsChanged or sendRestart:
            retVal, errmsg = updateRemoteConfig(self.agentIP, self.agentPort, self.startupCfg, self.runningCfg, sendRestart)

            if retVal != 0:
                QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                return False
                
        return True
        
    def onRestart(self):
        retVal = self.validateAndSend(True)
        if not retVal:
            return
        
        # Behave like OK but send restart flag
        super().done(QDialog.Accepted)
        
    def done(self, result):
        if result == QDialog.Accepted:
            retVal = validateAndSend(False)
            if not retVal:
                return
            
        super().done(result)
    
    def onShowInterfaces(self):
        validlist = ""
        for curInt in self.interfaces:
            if len(validlist) > 0:
                validlist += ', ' + curInt
            else:
                validlist = curInt

        if len(validlist) > 0:
            QMessageBox.question(self, 'Error',"Interfaces reported by the remote agent are:\n\n" + validlist, QMessageBox.Ok)
        else:
            QMessageBox.question(self, 'Error',"No wireless interfaces found.", QMessageBox.Ok)
        
    def onStartStopRecord(self):
        if self.btnRecordStartStop.text() == 'Stop':
            # Transition to start
            retVal, errmsg = stopRecord(self.agentIP, self.agentPort)
            
            if retVal != 0:
                QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                return
                
            self.btnRecordStartStop.setText('Start')
            self.recordInterfaceRunning.setEnabled(True)
            self.recordInterfaceRunning.setText('')
        else:
            if len(self.recordInterfaceRunning.text()) == 0:
                QMessageBox.question(self, 'Error',"Please provide a valid wireless interface name.", QMessageBox.Ok)
                return

            interface = self.recordInterfaceRunning.text().replace(' ', '')
            
            if interface not in self.interfaces:
                validlist = ""
                for curInt in self.interfaces:
                    if len(validlist) > 0:
                        validlist += ', ' + curInt
                    else:
                        validlist = curInt

                if len(validlist) > 0:
                    QMessageBox.question(self, 'Error',"The requested interface does not appear to be valid.  Interfaces seen on remote agent are:\n\n" + validlist, QMessageBox.Ok)
                else:
                    QMessageBox.question(self, 'Error',"No wireless interfaces found.", QMessageBox.Ok)
                    
                return
                
            # transition to stop
            retVal, errmsg = startRecord(self.agentIP, self.agentPort, interface)
            
            if retVal != 0:
                QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                return
                
            self.btnRecordStartStop.setText('Stop')
            self.recordInterfaceRunning.setEnabled(False)
        
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
        
    def closeEvent(self, event):
        event.accept()
            
    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()


# ------------------  GPS Coordinate  ------------------------------
class RemoteFilesDialog(QDialog):
    visibility = QtCore.pyqtSignal(bool)
    
    def __init__(self, mainWin, agentIP, agentPort, parent = None):
        super(RemoteFilesDialog, self).__init__(parent)

        self.visibility.connect(self.onVisibilityChanged)
        
        self.mainWin = mainWin
        
        self.remoteAgentIP = agentIP
        self.remoteAgentPort = agentPort

        self.lblMsg = QLabel("Remote Files", self)
        self.lblMsg.move(10, 20)

        self.btnRefresh = QPushButton("&Refresh", self)
        self.btnRefresh.setShortcut('Ctrl+R')
        self.btnRefresh.clicked.connect(self.onRefreshFiles)
        # self.btnRefresh.setStyleSheet("background-color: rgba(0,128,192,255); border: none;")
        # self.btnRefresh.move(90, 30)

        self.btnCopy = QPushButton("&Copy", self)
        self.btnCopy.clicked.connect(self.onCopyFiles)
        
        self.btnDelete = QPushButton("&Delete", self)
        self.btnDelete.clicked.connect(self.onDeleteFiles)
        
        self.fileTable = QTableWidget(self)
        self.fileTable.setColumnCount(3)
        self.fileTable.setShowGrid(True)
        self.fileTable.setHorizontalHeaderLabels(['Filename','Size','Last Modified'])
        #self.fileTable.setGeometry(10, 30, 100, 30)
        self.fileTable.resizeColumnsToContents()
        self.fileTable.setRowCount(0)
        self.fileTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.fileTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
       
        self.fileTableSortOrder = Qt.DescendingOrder
        self.fileTableSortIndex = -1
        
        self.ntRightClickMenu = QMenu(self)
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopy)
        self.ntRightClickMenu.addAction(newAct)
 
        # Attach it to the table
        self.fileTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fileTable.customContextMenuRequested.connect(self.showNTContextMenu)
        
        self.setBlackoutColors()
        
        self.setGeometry(self.geometry().x(), self.geometry().y(), 650,400)
        self.setWindowTitle("Remote Files: " + self.remoteAgentIP + ':' + str(self.remoteAgentPort))
        self.center()
        
        self.onRefreshFiles()

    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()
        self.fileTable.setGeometry(10, 50, size.width()-120, size.height()-60)
        
        self.btnRefresh.setGeometry(size.width()-170, 10, 60, 30)

        self.btnCopy.setGeometry(size.width()-90, 80, 80, 30)
        self.btnDelete.setGeometry(size.width()-90, 130, 80, 30)

    def setBlackoutColors(self):
        self.fileTable.setStyleSheet("background-color: black;gridline-color: white;color: white")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black}"
        self.fileTable.horizontalHeader().setStyleSheet(headerStyle)
        self.fileTable.verticalHeader().setStyleSheet(headerStyle)
        
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
        
    def closeEvent(self, event):
        event.accept()
            
    def onTableHeadingClicked(self, logical_index):
        header = self.fileTable.horizontalHeader()
        order = Qt.DescendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )

        self.fileTableSortOrder = order
        self.fileTableSortIndex = logical_index
        self.fileTable.sortItems(logical_index, order )
        
    def showNTContextMenu(self, pos):
        curRow = self.fileTable.currentRow()
        
        if curRow == -1:
            return
            
        self.ntRightClickMenu.exec_(self.fileTable.mapToGlobal(pos))
 
    def onCopy(self):
        curRow = self.fileTable.currentRow()
        curCol = self.fileTable.currentColumn()
        
        if curRow == -1 or curCol == -1:
            return
        
        curText = self.fileTable.item(curRow, curCol).text()
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)

    def onRefreshFiles(self):
        retVal, errmsg, filelist = getRemoteRecordingsFiles(self.remoteAgentIP, self.remoteAgentPort)
        
        if retVal != 0:
            QMessageBox.question(self, 'Error',"Could not list remote files: " + errmsg, QMessageBox.Ok)
            return
            
        self.populateTable(filelist)
            
    
    def getSelectedFilenames(self):
        retVal = []
        
        selectedItems = self.fileTable.selectedIndexes()
        
        for curIndex in selectedItems:
            curRow = curIndex.row()
            curFilename = self.fileTable.item(curRow, 0).text()
            
            retVal.append(curFilename)
            
        return retVal
        
    def getRemoteFile(self, agentIP, agentPort, filename):
        url = "http://" + agentIP + ":" + str(agentPort) + "/system/getrecording/" + filename

        dirname, runfilename = os.path.split(os.path.abspath(__file__))
        recordingsDir = dirname + '/recordings'
        fullPath = recordingsDir + '/' + filename
        
        if os.path.isfile(fullPath):
            reply = QMessageBox.question(self, 'Question',"Local file by that name already exists.  Overwrite?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if reply == QMessageBox.No:
                return
        
        try:
            # urllib.urlretrieve(url, fullPath)
            urlretrieve(url, fullPath)
            return 0, ""
        except:
            return 1, "Error downloading and saving file."
            
    def onCopyFiles(self):
        filenames = self.getSelectedFilenames()
        
        if len(filenames) == 0:
            return
        
        for curFile in filenames:
            retVal, errmsg = self.getRemoteFile(self.remoteAgentIP, self.remoteAgentPort, curFile)
            
            if retVal != 0:
                QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)

        self.onRefreshFiles()
        
    def onDeleteFiles(self):
        filenames = self.getSelectedFilenames()
        
        if len(filenames) == 0:
            return

        retVal, errmsg = delRemoteRecordingFiles(self.remoteAgentIP, self.remoteAgentPort, filenames)
        
        if retVal != 0:
            QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
            
        self.onRefreshFiles()
        
    def populateTable(self, filelist):
        self.fileTable.setRowCount(0)
        
        for curFile in filelist:
            rowCount = self.fileTable.rowCount()
            rowCount -= 1
            addedFirstRow = False
            if rowCount < 0:
                addedFirstRow = True
                rowCount = 0
                
            # Insert new at the top
            self.fileTable.insertRow(0)

            # Just make sure we don't get an extra blank row
            if (addedFirstRow):
                self.fileTable.setRowCount(1)

            rowPosition = 0 # Always at the first row
            self.fileTable.setItem(rowPosition,0, QTableWidgetItem(curFile.filename))
            self.fileTable.setItem(rowPosition,1, IntTableWidgetItem(str(curFile.size)))
            self.fileTable.setItem(rowPosition, 2, DateTableWidgetItem(curFile.timestamp.strftime("%m/%d/%Y %H:%M:%S")))
            
        if self.fileTableSortIndex >=0:
            self.fileTable.sortItems(self.fileTableSortIndex, self.fileTableSortOrder )
            
    def hideEvent(self, event):
        self.visibility.emit(False)
        
    def showEvent(self, event):
        self.visibility.emit(True)
        
    def onVisibilityChanged(self, visible):
        if not visible:
            pass
        else:
            pass
            
# -------  Main Routine For Debugging-------------------------

if __name__ == '__main__':
    app = QApplication([])
    #dbSettings, ok = DBSettingsDialog.getSettings()
    #mapSettings, ok = MapSettingsDialog.getSettings()
    # mapSettings, ok = TelemetryMapSettingsDialog.getSettings()
    # agentIP, port, accepted = AgentListenerDialog.getAgent()
    # testWin = GPSCoordDialog(mainWin=None)
    
    #from sparrowwifiagent import AgentConfigSettings
    #startupCfg = AgentConfigSettings()
    #runningCfg = AgentConfigSettings()
    #testWin = AgentConfigDialog(startupCfg, runningCfg, ['test'])
    
    testWin = RemoteFilesDialog(None,'127.0.0.1', 8020)
    testWin.exec()
    
    app.exec_()
