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

import sys
import csv
import os
import datetime
from time import sleep
from threading import Thread

from PyQt5.QtWidgets import QApplication, QMainWindow,  QDesktopWidget
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from PyQt5.QtWidgets import QAction, QComboBox, QLabel, QPushButton, QCheckBox, QTableWidget,QTableWidgetItem, QHeaderView
#from PyQt5.QtWidgets import QTabWidget, QWidget, QVBoxLayout
from PyQt5.QtChart import QChart, QChartView, QLineSeries, QValueAxis
from PyQt5.QtGui import QPen, QFont, QBrush, QColor, QPainter
# Qt for global colors.  See http://doc.qt.io/qt-5/qt.html#GlobalColor-enum
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5 import QtCore

# from PyQt5.QtCore import QCoreApplication # programatic quit
from wirelessengine import WirelessEngine, WirelessNetwork

class IntTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        if ( isinstance(other, QTableWidgetItem) ):
            try:
                my_value = int(self.data(Qt.EditRole))
            except:
                # This will throw an exception if a channel is say "3+5" for multiple channels
                # Break it down and sort it on the first channel #
                cellData = str(self.data(Qt.EditRole))
                firstChannel = cellData.split('+')[0]
                my_value = int(firstChannel)
                
            try:
                other_value = int(other.data(Qt.EditRole))
            except:
                # This will throw an exception if a channel is say "3+5" for multiple channels
                # Break it down and sort it on the first channel #
                cellData = str(self.data(Qt.EditRole))
                firstChannel = cellData.split('+')[0]
                other_value = int(firstChannel)

            return my_value < other_value

        return super(IntTableWidgetItem, self).__lt__(other)

class ScanThread(Thread):
    def run(self):
        self.threadRunning = True
        
        while (not self.signalStop):
            retCode, errString, wirelessNetworks = WirelessEngine.scanForNetworks(self.interface)
            if (retCode == 0):
                # self.statusBar().showMessage('Scan complete.  Found ' + str(len(wirelessNetworks)) + ' networks')
                if len(wirelessNetworks) > 0:
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
        
    def __init__(self, interface, mainWin):
        super(ScanThread, self).__init__()
        self.interface = interface
        self.mainWin = mainWin
        self.signalStop = False
        self.scanDelay = 0.5  # seconds
        self.threadRunning = False
        
colors = [Qt.black, Qt.red, Qt.darkRed, Qt.green, Qt.darkGreen, Qt.blue, Qt.darkBlue, Qt.cyan, Qt.darkCyan, Qt.magenta, Qt.darkMagenta, Qt.darkGray]

class mainWindow(QMainWindow):
    
    resized = QtCore.pyqtSignal()
    scanresults = QtCore.pyqtSignal(dict)
    errmsg = QtCore.pyqtSignal(int, str)
    
    # For help with qt5 GUI's this is a great tutorial:
    # http://zetcode.com/gui/pyqt5/
    
    def __init__(self):
        super().__init__()

        self.scanRunning = False
        self.scanIsBlocking = False
        
        self.nextColor = 0
        self.lastSeries = None

        self.scanThread = None
        self.scanDelay = 0.5
        self.scanresults.connect(self.scanResults)
        self.errmsg.connect(self.onErrMsg)
        
        desktopSize = QApplication.desktop().screenGeometry()
        #self.mainWidth=1024
        #self.mainHeight=768
        self.mainWidth = desktopSize.width() * 3 / 4
        self.mainHeight = desktopSize.height() * 3 / 4
        
        self.initUI()
        
        if os.geteuid() != 0:
            self.statusBar().showMessage('You need to have root privileges to run this tool.  Please exit and rerun it as root')
            print("You need to have root privileges to run this script.\nPlease try again, this time using 'sudo'. Exiting.")
            #self.close()
    
        
    def initUI(self):
        # self.setGeometry(10, 10, 800, 600)
        self.resize(self.mainWidth, self.mainHeight)
        self.center()
        self.setWindowTitle('Sparrow-WiFi Analyzer')
        self.setWindowIcon(QIcon('wifi_icon.png'))        
    
        self.statusBar().showMessage('Ready')

        self.createMenu()
        
        self.createControls()
        
        self.show()

    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()
        self.networkTable.setGeometry(10, 80, size.width()-20, size.height()/2-75)
        # self.tabs.setGeometry(30, self.height()/2+20, self.width()-60, self.height()/2-55)
        self.Plot24.setGeometry(10, size.height()/2+10, size.width()/2-10, size.height()/2-40)
        self.Plot5.setGeometry(size.width()/2+5, size.height()/2+10,size.width()/2-15, size.height()/2-40)
            
    def createMenu(self):
        # Create main menu bar
        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&File')
        
        # Create File Menu Items
        newAct = QAction('&New', self)        
        newAct.setShortcut('Ctrl+N')
        newAct.setStatusTip('Clear List')
        newAct.triggered.connect(self.clearData)
        fileMenu.addAction(newAct)

        # import
        newAct = QAction('&Import from CSV', self)        
        newAct.setStatusTip('Import from saved CSV')
        newAct.triggered.connect(self.importData)
        fileMenu.addAction(newAct)
        
        # export
        newAct = QAction('&Export to CSV', self)        
        newAct.setStatusTip('Export to CSV')
        newAct.triggered.connect(self.exportData)
        fileMenu.addAction(newAct)
        
        # Help Menu Items
        helpMenu = menubar.addMenu('&Help')
        newAct = QAction('About', self)        
        newAct.setStatusTip('About')
        newAct.triggered.connect(self.onAbout)
        helpMenu.addAction(newAct)
        #newMenu = QMenu('New', self)
        #actNewSqlite = QAction('New SQLite', self) 
        #actNewPostgres = QAction('New Postgres', self) 
        #newMenu.addAction(actNewSqlite)
        #newMenu.addAction(actNewpostgres)
        # fileMenu.addMenu(newMenu)
        
        # exitAct = QAction(QIcon('exit.png'), '&Exit', self)        
        exitAct = QAction('&Exit', self)        
        exitAct.setShortcut('Ctrl+X')
        exitAct.setStatusTip('Exit application')
        exitAct.triggered.connect(self.close)
        fileMenu.addAction(exitAct)

    def createControls(self):
        # self.statusBar().setStyleSheet("QStatusBar{background:rgba(204,229,255,255);color:black;border: 1px solid blue; border-radius: 1px;}")
        self.statusBar().setStyleSheet("QStatusBar{background:rgba(192,192,192,255);color:black;border: 1px solid blue; border-radius: 1px;}")
        self.statusBar().showMessage('Ready')
        self.lblInterface = QLabel("Interface", self)
        self.lblInterface.move(5, 30)

        self.combo = QComboBox(self)
        self.combo.move(75, 30)

        interfaces=WirelessEngine.getInterfaces()
        
        if (len(interfaces) > 0):
            for curInterface in interfaces:
                self.combo.addItem(curInterface)
        else:
            self.statusBar().showMessage('No wireless interfaces found.')

        self. combo.activated[str].connect(self.onInterface)        
        
        self.btnScan = QPushButton("&Scan", self)
        self.btnScan.setCheckable(True)
        self.btnScan.setShortcut('Ctrl+S');
        self.btnScan.move(200, 30)
        self.btnScan.clicked[bool].connect(self.scanClicked)
        
        self.networkTable = QTableWidget(self)
        self.networkTable.setColumnCount(8)
        self.networkTable.setGeometry(30, 100, self.mainWidth-60, self.mainHeight/2-75)
        self.networkTable.setShowGrid(True)
        self.networkTable.setHorizontalHeaderLabels(['macAddr', 'SSID', 'Security', 'Privacy', 'Channel', 'Frequency', 'Signal Strength', 'Bandwidth'])
        self.networkTable.resizeColumnsToContents()
        self.networkTable.setRowCount(0)
        self.networkTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        self.networkTable.horizontalHeader().sectionClicked.connect(self.onTableHeadingClicked)
        self.networkTable.cellClicked.connect(self.onTableClicked)

        self.cbAgeOut = QCheckBox(self)
        self.cbAgeOut.move(400, 30)
        self.lblAgeOut = QLabel("Remove networks not seen in the past 3 minutes", self)
        self.lblAgeOut.setGeometry(425, 30, 300, 30)
        #self.tabs = QTabWidget(self)
        #self.tab24Ghz = QWidget()	
        #self.tab5Ghz = QWidget()
        #self.tabs.setGeometry(30, self.mainHeight/2+20, self.mainWidth-60, self.mainHeight/2-75)
 
        # Add tabs
        #self.tabs.addTab(self.tab24Ghz,"2.4 GHz")
       # self.tabs.addTab(self.tab5Ghz,"5 GHz")
       
        self.createCharts()

    def onTableHeadingClicked(self, logical_index):
        header = self.networkTable.horizontalHeader()
        order = Qt.AscendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )
        self.networkTable.sortItems(logical_index, order )

        
    def onTableClicked(self, row, col):
        if (self.lastSeries is not None):
            # Change the old one back
            if (self.lastSeries):
                pen = self.lastSeries.pen()
                pen.setWidth(2)
                self.lastSeries.setPen(pen)
                self.lastSeries.setVisible(False)
                self.lastSeries.setVisible(True)
            
        selectedSeries = self.networkTable.item(row, 1).data(Qt.UserRole)
        
        if (selectedSeries):
            pen = selectedSeries.pen()
            pen.setWidth(6)
            selectedSeries.setPen(pen)
            selectedSeries.setVisible(False)
            selectedSeries.setVisible(True)
            
            self.lastSeries = selectedSeries
        else:
            selectedSeries = None

        
    def createCharts(self):

        # https://doc.qt.io/qt-5/qtcharts-linechart-example.html
        #testseries = QLineSeries()
        #testseries.append(1,0)
        #testseries.append(2, 0)
        #testseries.append(3, 0)
        #testseries.append(4, 0)
        #testseries.append(5, 0)
        #testseries.append(6, 1)
        #testseries.append(7, 1)
        #testseries.append(8, 1)
        #testseries.append(9, 0)
        #testseries.append(10, 0)
        #testseries.append(11, 0)
        #testseries.append(12, 0)
        #testseries.append(13, 0)
        # pen = QPen(QColor(0, 0, 0))
        #pen.setWidth(2)
        # testseries.setPen(pen)
    
        self.chart24 = QChart()
        titleFont = QFont()
        titleFont.setPixelSize(18)
        titleBrush = QBrush(QColor(0, 0, 255))
        self.chart24.setTitleFont(titleFont)
        self.chart24.setTitleBrush(titleBrush)
        self.chart24.setTitle('2.4 GHz')
        # self.chart24.addSeries(testseries)
        # self.chart24.createDefaultAxes()
        self.chart24.legend().hide()
        
        # Axis examples: https://doc.qt.io/qt-5/qtcharts-multiaxis-example.html
        newAxis = QValueAxis()
        newAxis.setMin(0)
        newAxis.setMax(15)
        newAxis.setTickCount(16)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("Channel")
        self.chart24.addAxis(newAxis, Qt.AlignBottom)
        
        newAxis = QValueAxis()
        newAxis.setMin(-100)
        newAxis.setMax(-20)
        newAxis.setTickCount(9)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("dBm")
        self.chart24.addAxis(newAxis, Qt.AlignLeft)
        
        chartBorder = Qt.darkGray
        self.Plot24 = QChartView(self.chart24, self)
        self.Plot24.setBackgroundBrush(chartBorder)
        self.Plot24.setRenderHint(QPainter.Antialiasing)
        # self.Plot24.setGeometry(10, self.mainHeight/2+10, self.mainWidth/2-10, self.mainHeight/2-75)

        self.chart5 = QChart()
        self.chart5.setTitleFont(titleFont)
        self.chart5.setTitleBrush(titleBrush)
        self.chart5.setTitle('5 GHz')
        #self.chart5.addSeries(testseries)
        self.chart5.createDefaultAxes()
        self.chart5.legend().hide()
        
        newAxis = QValueAxis()
        newAxis.setMin(30)
        newAxis.setMax(170)
        newAxis.setTickCount(14)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("Channel")
        self.chart5.addAxis(newAxis, Qt.AlignBottom)
        
        newAxis = QValueAxis()
        newAxis.setMin(-100)
        newAxis.setMax(-20)
        newAxis.setTickCount(9)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("dBm")
        self.chart5.addAxis(newAxis, Qt.AlignLeft)
        
        self.Plot5 = QChartView(self.chart5, self)
        self.Plot5.setBackgroundBrush(chartBorder)
        self.Plot5.setRenderHint(QPainter.Antialiasing)
        # self.Plot5.setGeometry(self.mainWidth/2+10, self.mainHeight/2+10, self.mainWidth/2-10, self.mainHeight/2-75)

    def scanClicked(self, pressed):
        self.scanRunning = pressed
        
        if not self.scanRunning:
            if self.scanThread:
                self.scanThread.signalStop = True
                
                while (self.scanThread.threadRunning):
                    self.statusBar().showMessage('Waiting for active scan to terminate...')
                    sleep(0.2)
                    
                self.scanThread = None
                    
            self.statusBar().showMessage('Ready')
        else:
            if (self.combo.count() > 0):
                curInterface = str(self.combo.currentText())
                self.statusBar().showMessage('Scanning on interface ' + curInterface)
                self.scanThread = ScanThread(curInterface, self)
                self.scanThread.scanDelay = self.scanDelay
                self.scanThread.start()
            else:
                QMessageBox.question(self, 'Error',"No wireless adapters found.", QMessageBox.Ok)
                self.scanRunning = False
                self.btnScan.setChecked(False)
                
        # For now mark it as done.
        #self.scanRunning = False
        #self.btnScan.setChecked(False)
        #self.statusBar().showMessage('Ready')
        
    def scanResults(self, wirelessNetworks):
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
                
        
        
    def populateTable(self, wirelessNetworks):
        rowPosition = self.networkTable.rowCount()
        
        for curRow in range(0, rowPosition):
            try:
                curData = self.networkTable.item(curRow, 1).data(Qt.UserRole+1)
            except:
                curData = None
                
            if (curData):
                # We already have the network.  just update it
                for curKey in wirelessNetworks.keys():
                    curNet = wirelessNetworks[curKey]
                    if curData.getKey() == curNet.getKey():
                        # Match
                        self.networkTable.item(curRow, 2).setText(curNet.security)
                        self.networkTable.item(curRow, 3).setText(curNet.privacy)
                        self.networkTable.item(curRow, 4).setText(str(curNet.getChannelString()))
                        self.networkTable.item(curRow, 5).setText(str(curNet.frequency))
                        self.networkTable.item(curRow, 6).setText(str(curNet.signal))
                        self.networkTable.item(curRow, 7).setText(str(curNet.bandwidth))
                        curNet.firstSeen = curData.firstSeen # This is one field to carry forward
                        curNet.foundInList = True
                        self.networkTable.item(curRow, 1).setData(Qt.UserRole+1, curNet)
                        
                        # Update series
                        curSeries = self.networkTable.item(curRow, 1).data(Qt.UserRole)
                        
                        # 3 scenarios: 
                        # 20 MHz, 1 channel
                        # 40 MHz, 2nd channel above/below or non-contiguous for 5 GHz
                        # 80/160 MHz, Specified differently.  It's allocated as a contiguous block
                        if curNet.channel < 15:
                            # 2.4 GHz
                            for i in range(0, 15):
                                graphPoint = False
                                
                                if (curNet.bandwidth == 20):
                                    if i >= (curNet.channel - 1) and i <=(curNet.channel +1):
                                        graphPoint = True
                                elif (curNet.bandwidth== 40):
                                    if curNet.secondaryChannelLocation == 'above':
                                        if i >= (curNet.channel - 1) and i <=(curNet.channel +5):
                                            graphPoint = True
                                    else:
                                        if i >= (curNet.channel - 5) and i <=(curNet.channel +1):
                                            graphPoint = True
                                elif (curNet.bandwidth == 80):
                                        if i >= (curNet.channel - 1) and i <=(curNet.channel +15):
                                            graphPoint = True
                                elif (curNet.bandwidth == 160):
                                        if i >= (curNet.channel - 1) and i <=(curNet.channel +29):
                                            graphPoint = True
                                        
                                if graphPoint:
                                    if curNet.signal >= -100:
                                        curSeries.replace(i, i, curNet.signal)
                                    else:
                                        curSeries.replace(i, i, -100)
                                else:
                                    curSeries.replace(i, i, -100)
                        else:
                            # 5 GHz
                            for i in range(33, 170):
                                graphPoint = False
                                
                                if (curNet.bandwidth == 20):
                                    if i >= (curNet.channel - 1) and i <=(curNet.channel +1):
                                        graphPoint = True
                                elif (curNet.bandwidth== 40):
                                    if curNet.secondaryChannelLocation == 'above':
                                        if i >= (curNet.channel - 1) and i <=(curNet.channel +5):
                                            graphPoint = True
                                    else:
                                        if i >= (curNet.channel - 5) and i <=(curNet.channel +1):
                                            graphPoint = True
                                elif (curNet.bandwidth == 80):
                                        if i >= (curNet.channel - 1) and i <=(curNet.channel +15):
                                            graphPoint = True
                                elif (curNet.bandwidth == 160):
                                        if i >= (curNet.channel - 1) and i <=(curNet.channel +29):
                                            graphPoint = True
                                        
                                if graphPoint:
                                    if curNet.signal >= -100:
                                        curSeries.replace(i-33, i, curNet.signal)
                                    else:
                                        curSeries.replace(i-33, i, -100)
                                else:
                                    curSeries.replace(i-33, i, -100)
                                    
                        break;
                
        self.networkTable.insertRow(rowPosition)
        
        for curKey in wirelessNetworks.keys():
            # Don't add duplicate
            if (wirelessNetworks[curKey].foundInList):
                continue
                
            nextColor = colors[self.nextColor]
            self.nextColor += 1
            
            if (self.nextColor >= len(colors)):
                self.nextColor = 0
                
            newSeries = QLineSeries()
            pen = QPen(nextColor)
            pen.setWidth(2)
            newSeries.setPen(pen)
            
            curNet = wirelessNetworks[curKey]
            # 3 scenarios: 
            # 20 MHz, 1 channel
            # 40 MHz, 2nd channel above/below or non-contiguous for 5 GHz
            # 80/160 MHz, Specified differently.  It's allocated as a contiguous block
            if curNet.channel < 15:
                # 2.4 GHz
                for i in range(0, 15):
                    # 2.4 GHz channels overlap by 2 in each direction
                    graphPoint = False
                    
                    if (curNet.bandwidth == 20):
                        if i >= (curNet.channel - 1) and i <=(curNet.channel +1):
                            graphPoint = True
                    elif (curNet.bandwidth== 40):
                        if curNet.secondaryChannelLocation == 'above':
                            if i >= (curNet.channel - 1) and i <=(curNet.channel +5):
                                graphPoint = True
                        else:
                            if i >= (curNet.channel - 5) and i <=(curNet.channel +1):
                                graphPoint = True
                    elif (curNet.bandwidth == 80):
                            if i >= (curNet.channel - 1) and i <=(curNet.channel +15):
                                graphPoint = True
                    elif (curNet.bandwidth == 160):
                            if i >= (curNet.channel - 1) and i <=(curNet.channel +29):
                                graphPoint = True
                            
                    if graphPoint:
                        if curNet.signal >= -100:
                            newSeries.append( i, curNet.signal)
                        else:
                            newSeries.append(i, -100)
                    else:
                            newSeries.append(i, -100)
                        
                newSeries.setName(curNet.getKey())
                
                self.chart24.addSeries(newSeries)
                newSeries.attachAxis(self.chart24.axisX())
                newSeries.attachAxis(self.chart24.axisY())
            else:
                # 5 GHz
                for i in range(33, 170):
                    graphPoint = False
                    
                    if (curNet.bandwidth == 20):
                        if i >= (curNet.channel - 1) and i <=(curNet.channel +1):
                            graphPoint = True
                    elif (curNet.bandwidth== 40):
                        if curNet.secondaryChannelLocation == 'above':
                            if i >= (curNet.channel - 1) and i <=(curNet.channel +5):
                                graphPoint = True
                        else:
                            if i >= (curNet.channel - 5) and i <=(curNet.channel +1):
                                graphPoint = True
                    elif (curNet.bandwidth == 80):
                            if i >= (curNet.channel - 1) and i <=(curNet.channel +15):
                                graphPoint = True
                    elif (curNet.bandwidth == 160):
                            if i >= (curNet.channel - 1) and i <=(curNet.channel +29):
                                graphPoint = True
                            
                    if graphPoint:
                        if curNet.signal >= -100:
                            newSeries.append( i, curNet.signal)
                        else:
                            newSeries.append(i, -100)
                    else:
                            newSeries.append(i, -100)
                        
                newSeries.setName(curNet.getKey())
                
                self.chart5.addSeries(newSeries)
                newSeries.attachAxis(self.chart5.axisX())
                newSeries.attachAxis(self.chart5.axisY())
                
            # Do the table second so we can attach the series to it.
            rowPosition = self.networkTable.rowCount()
            rowPosition -= 1
            self.networkTable.insertRow(rowPosition)
            self.networkTable.setItem(rowPosition, 0, QTableWidgetItem(wirelessNetworks[curKey].macAddr))
            tmpssid = wirelessNetworks[curKey].ssid
            if (len(tmpssid) == 0):
                tmpssid = '<Unknown>'
            newSSID = QTableWidgetItem(tmpssid)
            ssidBrush = QBrush(nextColor)
            newSSID.setForeground(ssidBrush)
            # You can bind more than one data.  See this: 
            # https://stackoverflow.com/questions/2579579/qt-how-to-associate-data-with-qtablewidgetitem
            newSSID.setData(Qt.UserRole, newSeries)
            newSSID.setData(Qt.UserRole+1, wirelessNetworks[curKey])
            
            self.networkTable.setItem(rowPosition, 1, newSSID)
            self.networkTable.setItem(rowPosition, 2, QTableWidgetItem(wirelessNetworks[curKey].security))
            self.networkTable.setItem(rowPosition, 3, QTableWidgetItem(wirelessNetworks[curKey].privacy))
            self.networkTable.setItem(rowPosition, 4, IntTableWidgetItem(str(wirelessNetworks[curKey].getChannelString())))
            self.networkTable.setItem(rowPosition, 5, IntTableWidgetItem(str(wirelessNetworks[curKey].frequency)))
            self.networkTable.setItem(rowPosition, 6,  IntTableWidgetItem(str(wirelessNetworks[curKey].signal)))
            self.networkTable.setItem(rowPosition, 7, IntTableWidgetItem(str(wirelessNetworks[curKey].bandwidth)))

        # Clean up any empty rows because of the way QTableWidget is handling row inserts
        rowPosition = self.networkTable.rowCount()

        maxTime = datetime.datetime.now() - datetime.timedelta(minutes=3)
        # maxTime = datetime.datetime.now() - datetime.timedelta(seconds=5)

        for i in range(rowPosition, 0, -1):
            try:
                curData = self.networkTable.item(i, 1).data(Qt.UserRole+1)
                
                # Age out
                if self.cbAgeOut.isChecked():
                    if curData.lastSeen < maxTime:
                        curSeries = self.networkTable.item(i, 1).data(Qt.UserRole)
                        if curData.channel < 20:
                            self.chart24.removeSeries(curSeries)
                        else:
                            self.chart5.removeSeries(curSeries)
                            
                        self.networkTable.removeRow(i)
                    
            except:
                curData = None
                self.networkTable.removeRow(i)
                
        self.networkTable.resizeColumnsToContents()
        # self.networkTable.horizontalHeader().setStretchLastSection(True)
        self.networkTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        #self.networkTable.setRowCount(len(wirelessNetworks))
        
    def onInterface(self):
        pass
            
    def clearData(self):
        self.networkTable.setRowCount(0)
        self.chart24.removeAllSeries()
        self.chart5.removeAllSeries()
        
        
    def openFileDialog(self):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "","CSV Files (*.csv);;All Files (*)", options=options)
        if fileName:
            return fileName
        else:
            return None
 
    def saveFileDialog(self):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self,"QFileDialog.getSaveFileName()","","CSV Files (*.csv);;All Files (*)", options=options)
        if fileName:
            return fileName
        else:
            return None

    def importData(self):
        fileName = self.openFileDialog()

        if not fileName:
            return
            
        linesRead = 0
        wirelessNetworks = {}
        
        with open(fileName, 'r') as f:
            reader = csv.reader(f)
            raw_list = list(reader)
            
            if len(raw_list) > 0:
                # Check header row looks okay
                if raw_list[0][0] != 'macAddr':
                    QMessageBox.question(self, 'Error',"File format doesn't look like an exported scan.", QMessageBox.Ok)
                    return
                        
                # Ignore header row
                for i in range (1, len(raw_list)):
                    if linesRead > 0:
                        newNet = WirelessNetwork()
                        newNet.macAddr=raw_list[i][0]
                        newNet.ssid = raw_list[i][1]
                        newNet.security = raw_list[i][2]
                        newNet.privacy = raw_list[i][3]
                        newNet.channel = int(raw_list[i][4])
                        newNet.frequency = int(raw_list[i][5])
                        newNet.signal = int(raw_list[i][6])
                        newNet.bandwidth = int(raw_list[i][7])
                        
                        wirelessNetworks[newNet.getKey()] = newNet
                    
                    linesRead += 1
                    
        if len(wirelessNetworks) > 0:
            self.clearData()
            self.populateTable(wirelessNetworks)

    def exportData(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
            
        outputFile.write('macAddr,SSID,Security,Privacy,Channel,Frequency,Signal Strength,Bandwidth\n')

        numItems = self.networkTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            return
           
        for i in range(0, numItems):
            outputFile.write(self.networkTable.item(i, 0).text() + ',' + self.networkTable.item(i, 1).text() + ',' + self.networkTable.item(i, 2).text() + ',' + self.networkTable.item(i, 3).text())
            outputFile.write(',' + self.networkTable.item(i, 4).text()+ ',' + self.networkTable.item(i, 5).text()+ ',' + self.networkTable.item(i, 6).text()+ ',' + self.networkTable.item(i, 7).text() + '\n')
            
        outputFile.close()
        
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
            QMessageBox.question(self, 'Error',"Please stop the running scan first.", QMessageBox.Ok)
            event.ignore()
        else:
            event.accept()
            
if __name__ == '__main__':
    
    app = QApplication(sys.argv)
    mainWin = mainWindow()
    sys.exit(app.exec_())
    
