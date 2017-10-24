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
from PyQt5.QtWidgets  import QFileDialog, QSpinBox, QDesktopWidget, QMessageBox, QTableWidget, QHeaderView,QTableWidgetItem
from sparrowtablewidgets import IntTableWidgetItem
from PyQt5.QtCore import Qt
from PyQt5 import QtCore

from socket import *
from threading import Thread
from sparrowmap import MapEngine

# Example dialog:
# https://stackoverflow.com/questions/18196799/how-can-i-show-a-pyqt-modal-dialog-and-get-data-out-of-its-controls-once-its-clo

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
class AgentListenerDIalog(QDialog):
    agentAnnounce = QtCore.pyqtSignal(str, int)

    def __init__(self, parent = None):
        super(AgentListenerDIalog, self).__init__(parent)

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
        
    def closeEvent(self, event):
        self.agentListenerThread.signalStop = True
        
        while (self.agentListenerThread.threadRunning):
            sleep(1)
                    
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
        dialog = AgentListenerDIalog(parent)
        result = dialog.exec_()
        # date = dialog.dateTime()
        agentIP, port = dialog.getAgentInfo()
        return (agentIP, port, result == QDialog.Accepted)
# -------  Main Routine For Debugging-------------------------

if __name__ == '__main__':
    app = QApplication([])
    #dbSettings, ok = DBSettingsDialog.getSettings()
    #mapSettings, ok = MapSettingsDialog.getSettings()
    # mapSettings, ok = TelemetryMapSettingsDialog.getSettings()
    agentIP, port, accepted = AgentListenerDIalog.getAgent()
    app.exec_()
