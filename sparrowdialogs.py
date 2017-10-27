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

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QApplication, QLabel, QComboBox, QLineEdit, QPushButton
from PyQt5.QtWidgets  import QFileDialog, QSpinBox, QDesktopWidget, QMessageBox, QTableWidget, QHeaderView,QTableWidgetItem,  QMenu, QAction
from sparrowtablewidgets import DateTableWidgetItem, FloatTableWidgetItem, IntTableWidgetItem
from PyQt5.QtCore import Qt,QTimer
from PyQt5 import QtCore

from socket import *
import datetime
from threading import Thread
from time import sleep
import requests
import json
import re

from sparrowmap import MapEngine

# ------------------  Global functions for agent HTTP requests ------------------------------
def makeGetRequest(url):
    try:
        response = requests.get(url)
    except:
        return -1, ""
        
    if response.status_code != 200:
        return response.status_code, ""
        
    htmlResponse=response.text
    return response.status_code, htmlResponse

def makePostRequest(url, jsonstr):
        # use something like jsonstr = json.dumps(somestring) to get the right format
        try:
            response = requests.post(url, data=jsonstr)
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
        
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("Remote Agent Detection")
        self.center()

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
class GPSCoordDIalog(QDialog):
    visibility = QtCore.pyqtSignal(bool)
    
    def __init__(self, mainWin, parent = None):
        super(GPSCoordDIalog, self).__init__(parent)

        self.visibility.connect(self.onVisibilityChanged)
        
        self.mainWin = mainWin
        

        # Set up GPS check timer
        self.gpsTimer = QTimer()
        self.gpsTimer.timeout.connect(self.onGPSTimer)
        self.gpsTimer.setSingleShot(True)
        self.gpsTimerTimeout = 2000
        self.gpsTimer.start(self.gpsTimerTimeout)   # Check every 5 seconds
        
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
        
        self.setGeometry(self.geometry().x(), self.geometry().y(), 500,320)
        self.setWindowTitle("GPS Coordinate Viewer")
        self.center()

        # initial update:
        if self.mainWin:
            curGPS = self.mainWin.getCurrentGPS()
            self.updateTable(curGPS)

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
            
# ------------------  Agent Configuration  ------------------------------
class AgentConfigDialog(QDialog):
    def __init__(self, startupCfg, runningCfg, agentIP='127.0.0.1', agentPort=8020,parent = None):
        super(AgentConfigDialog,  self).__init__(parent)

        self.agentIP = agentIP
        self.agentPort = agentPort
        
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
        
        #if len(startupCfg.recordInterface) > 0:
        #    self.comboRecordStartup.setCurrentIndex(0)
        #    self.btnRecordStartStop.setText('Stop')
        #else:
        #    self.comboRecordStartup.setCurrentIndex(1)
        
        # Record Interface
        self.lblMsg = QLabel("Record Interface:", self)
        self.lblMsg.move(10, 250)

        self.recordInterfaceStartup = QLineEdit(self)
        self.recordInterfaceStartup.setGeometry(118, 245, 100, 25)
        self.recordInterfaceStartup.setText(startupCfg.recordInterface)
        
        self.recordInterfaceRunning = QLineEdit(self)
        self.recordInterfaceRunning.setGeometry(250, 245, 100, 25)
        self.recordInterfaceRunning.setText(runningCfg.recordInterface)
        if self.btnRecordStartStop.text() == 'Stop':
            self.recordInterfaceRunning.setEnabled(False)
        
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
        buttons.move(125, 390)

        self.btnReboot = QPushButton("Save and Restart", self)
        self.btnReboot.setGeometry(125, 440, 170, 30)
        self.btnReboot.clicked.connect(self.onRestart)

        # Window geometry
        self.setGeometry(self.geometry().x(), self.geometry().y(), 400,480)
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
    
    def onStartStopRecord(self):
        if self.btnRecordStartStop.text() == 'Stop':
            # Transition to start
            self.btnRecordStartStop.setText('Start')
            self.recordInterfaceRunning.setEnabled(True)
        else:
            if len(self.recordInterfaceRunning.text()) == 0:
                QMessageBox.question(self, 'Error',"Please provide a valid wireless interface name.", QMessageBox.Ok)
                return

            # transition to stop
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


# -------  Main Routine For Debugging-------------------------

if __name__ == '__main__':
    app = QApplication([])
    #dbSettings, ok = DBSettingsDialog.getSettings()
    #mapSettings, ok = MapSettingsDialog.getSettings()
    # mapSettings, ok = TelemetryMapSettingsDialog.getSettings()
    # agentIP, port, accepted = AgentListenerDialog.getAgent()
    # testWin = GPSCoordDIalog(mainWin=None)
    
    from sparrowwifiagent import AgentConfigSettings
    startupCfg = AgentConfigSettings()
    runningCfg = AgentConfigSettings()
    testWin = AgentConfigDialog(startupCfg, runningCfg)
    testWin.exec()
    
    app.exec_()
