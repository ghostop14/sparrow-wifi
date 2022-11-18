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

from PyQt5.QtWidgets import QDialog, QApplication,QDesktopWidget
from PyQt5.QtWidgets import QTableWidget, QHeaderView,QTableWidgetItem, QMessageBox, QFileDialog, QMenu, QAction
# from PyQt5.QtWidgets import QLabel, QComboBox, QLineEdit, QPushButton, QFileDialog
#from PyQt5.QtCore import Qt
from PyQt5 import QtWidgets
from PyQt5 import QtCore
from PyQt5.QtCore import Qt
from PyQt5.QtChart import QChart, QChartView, QLineSeries, QValueAxis
from PyQt5.QtGui import QPen, QFont, QBrush, QColor, QPainter
from PyQt5.QtWidgets import QPushButton

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from sparrowtablewidgets import IntTableWidgetItem, FloatTableWidgetItem, DateTableWidgetItem

from threading import Lock

# from wirelessengine import WirelessNetwork

# https://matplotlib.org/examples/user_interfaces/embedding_in_qt5.html

class RadarWidget(FigureCanvas):
    def __init__(self, parent=None, useBlackoutColors=True, width=4, height=4, dpi=100):
        # fig = Figure(figsize=(width, height), dpi=dpi)
        # self.axes = fig.add_subplot(111)
        # -----------------------------------------------------------
        # fig = plt.figure()
        # useBlackoutColors = False
        self.useBlackoutColors = useBlackoutColors
        if self.useBlackoutColors:
            self.fontColor = 'white'
            self.backgroundColor = 'black'
        else:
            self.fontColor = 'black'
            self.backgroundColor = 'white'
            
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.fig.patch.set_facecolor(self.backgroundColor)
        # "axisbg was deprecated, use facecolor instead"
        # self.axes = self.fig.add_subplot(111, polar=True, axisbg=self.backgroundColor)
        self.axes = self.fig.add_subplot(111, polar=True, facecolor=self.backgroundColor)
        # Angle: np.linspace(0, 2*np.pi, 100)
        # Radius: np.ones(100)*5
        # ax.plot(np.linspace(0, 2*np.pi, 100), np.ones(100)*5, color='r', linestyle='-')
        # Each of these use 100 points.  linespace creates the angles 0-2 PI with 100 points
        # np.ones creates a 100 point array filled with 1's then multiplies that by the scalar 5

        # Create an "invisible" line at 100 to set the max for the plot
        self.axes.plot(np.linspace(0, 2*np.pi, 100), np.ones(100)*100, color=self.fontColor, linestyle='')

        # Plot line: Initialize out to 100 and blank
        radius = 100
        self.blackline = self.axes.plot(np.linspace(0, 2*np.pi, 100), np.ones(100)*radius, color=self.fontColor, linestyle='-')
        self.redline = None

        # Plot a filled circle
        # http://nullege.com/codes/search/matplotlib.pyplot.Circle
        # Params are: Cartesian coord of center, radius, etc...
        # circle = plt.Circle((0.0, 0.0), radius, transform=self.axes.transData._b, color="red", alpha=0.4)
        # self.filledcircle = self.axes.add_artist(circle)
        self.filledcircle = None
        # Create bullseye
        circle = plt.Circle((0.0, 0.0), 20, transform=self.axes.transData._b, color=self.fontColor, alpha=0.4)
        self.bullseye = self.axes.add_artist(circle)

        # Rotate zero up
        self.axes.set_theta_zero_location("N")

        self.axes.set_yticklabels(['-20', '-40', '-60', '-80', '-100'], color=self.fontColor)
        # plt.show()
        # -----------------------------------------------------------
        FigureCanvas.__init__(self, self.fig)
        self.setParent(parent)


        self.title  = self.fig.suptitle('Tracker', fontsize=8, fontweight='bold', color=self.fontColor)

        FigureCanvas.setSizePolicy(self,
                                   QtWidgets.QSizePolicy.Expanding,
                                   QtWidgets.QSizePolicy.Expanding)
        FigureCanvas.updateGeometry(self)

    def updateData(self, radius):
        if self.redline is not None:
            self.redline.pop(0).remove()
        self.redline = self.axes.plot(np.linspace(0, 2*np.pi, 100), np.ones(100)*radius, color='r', linestyle='-')

        if self.filledcircle:
            self.filledcircle.remove()
            
        self.bullseye.remove()
        circle = plt.Circle((0.0, 0.0), radius, transform=self.axes.transData._b, color="red", alpha=0.4)
        self.filledcircle = self.axes.add_artist(circle)
        # Create bullseye
        circle = plt.Circle((0.0, 0.0), 20, transform=self.axes.transData._b, color=self.fontColor, alpha=0.4)
        self.bullseye = self.axes.add_artist(circle)

        
class TelemetryDialog(QDialog):
    resized = QtCore.pyqtSignal()
    visibility = QtCore.pyqtSignal(bool)

    def __init__(self, winTitle = "Network Telemetry", parent = None):
        super(TelemetryDialog, self).__init__(parent)
        
        self.visibility.connect(self.onVisibilityChanged)

        self.winTitle = winTitle
        
        self.updateLock = Lock()
        
        # Used to detect network change
        self.lastNetKey = ""
        self.lastSeen = None
        self.maxPoints = 20
        self.maxRowPoints = 60
        
        self.paused = False
        self.streamingSave = False
        self.streamingFile = None
        self.linesBeforeFlush = 10
        self.currentLine = 0
        
        desktopSize = QApplication.desktop().screenGeometry()
        self.setGeometry(self.geometry().x(), self.geometry().y(), desktopSize.width() // 2,desktopSize.height() // 2)
        
        self.setWindowTitle(winTitle)

        self.radar = RadarWidget(self)
        self.radar.setGeometry(self.geometry().width()//2, 10, self.geometry().width()//2-20, self.geometry().width()//2-20)
        
        self.createTable()
       
        self.btnExport = QPushButton("Export Table", self)
        self.btnExport.clicked[bool].connect(self.onExportClicked)
        self.btnExport.setStyleSheet("background-color: rgba(2,128,192,255);")

        self.btnPause = QPushButton("Pause Table", self)
        self.btnPause.setCheckable(True)
        self.btnPause.clicked[bool].connect(self.onPauseClicked)
        self.btnPause.setStyleSheet("background-color: rgba(2,128,192,255);")
        
        self.btnStream = QPushButton("Streaming Save", self)
        self.btnStream.setCheckable(True)
        self.btnStream.clicked[bool].connect(self.onStreamClicked)
        self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
        
        self.createChart()
        
        self.setBlackoutColors()
        
        self.setMinimumWidth(600)
        self.setMinimumHeight(600)
        
        self.center()

    def createTable(self):
        # Set up location table
        self.locationTable = QTableWidget(self)
        self.locationTable.setColumnCount(8)
        self.locationTable.setGeometry(10, 10, self.geometry().width()//2-20, self.geometry().height()//2)
        self.locationTable.setShowGrid(True)
        self.locationTable.setHorizontalHeaderLabels(['macAddr','SSID', 'Strength', 'Timestamp','GPS', 'Latitude', 'Longitude', 'Altitude'])
        self.locationTable.resizeColumnsToContents()
        self.locationTable.setRowCount(0)
        self.locationTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        
        self.ntRightClickMenu = QMenu(self)
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopy)
        self.ntRightClickMenu.addAction(newAct)
        
        self.locationTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.locationTable.customContextMenuRequested.connect(self.showNTContextMenu)
        
    def setBlackoutColors(self):
        self.locationTable.setStyleSheet("QTableView {background-color: black;gridline-color: white;color: white} QTableCornerButton::section{background-color: white;}")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black;} QHeaderView::down-arrow,QHeaderView::up-arrow {background: none;}"
        self.locationTable.horizontalHeader().setStyleSheet(headerStyle)
        self.locationTable.verticalHeader().setStyleSheet(headerStyle)
        
        mainTitleBrush = QBrush(Qt.red)
        self.timeChart.setTitleBrush(mainTitleBrush)
        
        self.timeChart.setBackgroundBrush(QBrush(Qt.black))
        self.timeChart.axisX().setLabelsColor(Qt.white)
        self.timeChart.axisY().setLabelsColor(Qt.white)
        titleBrush = QBrush(Qt.white)
        self.timeChart.axisX().setTitleBrush(titleBrush)
        self.timeChart.axisY().setTitleBrush(titleBrush)
        
    def resizeEvent(self, event):
        wDim = self.geometry().width()/2-20
        hDim = self.geometry().height()/2
        
        smallerDim = wDim
        if hDim < smallerDim:
            smallerDim = hDim

        smallerDim = int(smallerDim)
        # Radar
        self.radar.setGeometry(int(self.geometry().width() - smallerDim - 10), 10, smallerDim, smallerDim)

        # chart
        self.timePlot.setGeometry(10, 10, int(self.geometry().width() - smallerDim - 30), smallerDim)

        # Buttons
        self.btnPause.setGeometry(10, int(self.geometry().height()/2+18), 110, 25)
        self.btnExport.setGeometry(150, int(self.geometry().height()/2+18), 110, 25)
        self.btnStream.setGeometry(290, int(self.geometry().height()/2+18), 110, 25)
        
        # Table
        self.locationTable.setGeometry(10, int(self.geometry().height()/2 + 50), int(self.geometry().width()-20), int(self.geometry().height()/2-60))

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

    def showNTContextMenu(self, pos):
        curRow = self.locationTable.currentRow()
        
        if curRow == -1:
            return
            
        self.ntRightClickMenu.exec_(self.locationTable.mapToGlobal(pos))
        
    def onCopy(self):
        self.updateLock.acquire()
        
        curRow = self.locationTable.currentRow()
        curCol = self.locationTable.currentColumn()
        
        if curRow == -1 or curCol == -1:
            self.updateLock.release()
            return
        
        curText = self.locationTable.item(curRow, curCol).text()
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)
        
        self.updateLock.release()
        
    def onVisibilityChanged(self, visible):
        if not visible:
            self.paused = True
            self.btnPause.setStyleSheet("background-color: rgba(255,0,0,255);")
            # We're coming out of streaming
            self.streamingSave = False
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            self.btnStream.setChecked(False)
            if (self.streamingFile):
                self.streamingFile.close()
                self.streamingFile = None
            return
        else:
            self.paused = False
            self.btnPause.setStyleSheet("background-color: rgba(2,128,192,255);")
            if self.locationTable.rowCount() > 1:
                self.locationTable.scrollToItem(self.locationTable.item(0, 0))
        
    def hideEvent(self, event):
        self.visibility.emit(False)
        
    def showEvent(self, event):
        self.visibility.emit(True)
        
    def onPauseClicked(self, pressed):
        if self.btnPause.isChecked():
            self.paused = True
            self.btnPause.setStyleSheet("background-color: rgba(255,0,0,255);")
        else:
            self.paused = False
            self.btnPause.setStyleSheet("background-color: rgba(2,128,192,255);")
        
    def onStreamClicked(self, pressed):
        if not self.btnStream.isChecked():
            # We're coming out of streaming
            self.streamingSave = False
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            if (self.streamingFile):
                self.streamingFile.close()
                self.streamingFile = None
            return
            
        self.btnStream.setStyleSheet("background-color: rgba(255,0,0,255);")
        self.streamingSave = True
        
        fileName = self.saveFileDialog()

        if not fileName:
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            self.btnStream.setChecked(False)
            return
            
        try:
            self.streamingFile = open(fileName, 'w', 1)  # 1 says use line buffering, otherwise it fully buffers and doesn't write
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            self.streamingFile = None
            self.streamingSave = False
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            self.btnStream.setChecked(False)
            return
            
        self.streamingFile.write('MAC Address,SSID,Strength,Timestamp,GPS,Latitude,Longitude,Altitude\n')
                    
    def onExportClicked(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
            
        outputFile.write('MAC Address,SSID,Strength,Timestamp,GPS,Latitude,Longitude,Altitude\n')

        numItems = self.locationTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            return
           
        self.updateLock.acquire()
        
        for i in range(0, numItems):
            outputFile.write(self.locationTable.item(i, 0).text() + ',"' + self.locationTable.item(i, 1).text() + '",' + self.locationTable.item(i, 2).text() + ',' + self.locationTable.item(i, 3).text())
            outputFile.write(',' + self.locationTable.item(i, 4).text()+ ',' + self.locationTable.item(i, 5).text()+ ',' + self.locationTable.item(i, 6).text()+ ',' + self.locationTable.item(i, 7).text()  + '\n')
            
        self.updateLock.release()
        outputFile.close()
        
    def saveFileDialog(self):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self,"QFileDialog.getSaveFileName()","","CSV Files (*.csv);;All Files (*)", options=options)
        if fileName:
            return fileName
        else:
            return None

    def createChart(self):
        self.timeChart = QChart()
        titleFont = QFont()
        titleFont.setPixelSize(18)
        titleBrush = QBrush(QColor(0, 0, 255))
        self.timeChart.setTitleFont(titleFont)
        self.timeChart.setTitleBrush(titleBrush)
        self.timeChart.setTitle('Signal (Past ' + str(self.maxPoints) + ' Samples)')
        # self.timeChart.addSeries(testseries)
        # self.timeChart.createDefaultAxes()
        self.timeChart.legend().hide()
        
        # Axis examples: https://doc.qt.io/qt-5/qtcharts-multiaxis-example.html
        newAxis = QValueAxis()
        newAxis.setMin(0)
        newAxis.setMax(self.maxPoints)
        newAxis.setTickCount(11)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("Sample")
        self.timeChart.addAxis(newAxis, Qt.AlignBottom)
        
        newAxis = QValueAxis()
        newAxis.setMin(-100)
        newAxis.setMax(-10)
        newAxis.setTickCount(9)
        newAxis.setLabelFormat("%d")
        newAxis.setTitleText("dBm")
        self.timeChart.addAxis(newAxis, Qt.AlignLeft)
        
        chartBorder = Qt.darkGray
        self.timePlot = QChartView(self.timeChart, self)
        self.timePlot.setBackgroundBrush(chartBorder)
        self.timePlot.setRenderHint(QPainter.Antialiasing)
        
        self.timeSeries = QLineSeries()
        pen = QPen(Qt.yellow)
            
        pen.setWidth(2)
        self.timeSeries.setPen(pen)
        self.timeChart.addSeries(self.timeSeries)
        self.timeSeries.attachAxis(self.timeChart.axisX())
        self.timeSeries.attachAxis(self.timeChart.axisY())

    def updateNetworkData(self, curNet):
        if not self.isVisible():
            return
            
        # Signal is -NN dBm.  Need to make it positive for the plot
        self.radar.updateData(curNet.signal*-1)
        
        if self.winTitle == "Client Telemetry":
            self.setWindowTitle(self.winTitle + " - [" + curNet.macAddr + "] " + curNet.ssid)
        else:
            self.setWindowTitle(self.winTitle + " - " + curNet.ssid)
            
        self.radar.draw()
        
        #  Network changed.  Clear our table and time data
        updateChartAndTable = False
        
        self.updateLock.acquire()
        
        if (curNet.getKey() != self.lastNetKey):
            self.lastNetKey = curNet.getKey()
            self.locationTable.setRowCount(0)
            self.timeSeries.clear()
            updateChartAndTable = True

            ssidTitle = curNet.ssid
            if len(ssidTitle) > 28:
                ssidTitle = ssidTitle[:28]
                ssidTitle = ssidTitle + '...'

            self.timeChart.setTitle(ssidTitle + ' Signal (Past ' + str(self.maxPoints) + ' Samples)')
        else:
            if self.lastSeen != curNet.lastSeen:
                updateChartAndTable = True
        
        if updateChartAndTable:
            # Update chart
            numPoints = len(self.timeSeries.pointsVector())
            
            if numPoints >= self.maxPoints:
                self.timeSeries.remove(0)
                # Now we need to reset the x data to pull the series back
                counter = 0
                for curPoint in self.timeSeries.pointsVector():
                    self.timeSeries.replace(counter, counter, curPoint.y())
                    counter += 1
                    
            if curNet.signal >= -100:
                self.timeSeries.append(numPoints,curNet.signal)
            else:
                self.timeSeries.append(numPoints,-100)
                
            # Update Table
            self.addTableData(curNet)
            
            # Limit points in each
            if self.locationTable.rowCount() > self.maxRowPoints:
                self.locationTable.setRowCount(self.maxRowPoints)
            
        self.updateLock.release()
        
    def addTableData(self, curNet):
        if self.paused:
            return

        # rowPosition = self.locationTable.rowCount()
        # Always insert at row(0)
        rowPosition = 0
            
        self.locationTable.insertRow(rowPosition)
        
        #if (addedFirstRow):
        #    self.locationTable.setRowCount(1)
            
        # ['macAddr','SSID', 'Strength', 'Timestamp','GPS', 'Latitude', 'Longitude', 'Altitude']
        self.locationTable.setItem(rowPosition, 0, QTableWidgetItem(curNet.macAddr))
        tmpssid = curNet.ssid
        if (len(tmpssid) == 0):
            tmpssid = '<Unknown>'
        newSSID = QTableWidgetItem(tmpssid)
        
        self.locationTable.setItem(rowPosition, 1, newSSID)
        self.locationTable.setItem(rowPosition, 2,  IntTableWidgetItem(str(curNet.signal)))
        self.locationTable.setItem(rowPosition, 3, DateTableWidgetItem(curNet.lastSeen.strftime("%m/%d/%Y %H:%M:%S")))
        if curNet.gps.isValid:
            self.locationTable.setItem(rowPosition, 4, QTableWidgetItem('Yes'))
        else:
            self.locationTable.setItem(rowPosition, 4, QTableWidgetItem('No'))

        self.locationTable.setItem(rowPosition, 5,  FloatTableWidgetItem(str(curNet.gps.latitude)))
        self.locationTable.setItem(rowPosition, 6,  FloatTableWidgetItem(str(curNet.gps.longitude)))
        self.locationTable.setItem(rowPosition, 7,  FloatTableWidgetItem(str(curNet.gps.altitude)))
        #order = Qt.DescendingOrder
        #self.locationTable.sortItems(3, order )
                    

        # If we're in streaming mode, write the data out to disk as well
        if self.streamingFile:
            self.streamingFile.write(self.locationTable.item(rowPosition, 0).text() + ',"' + self.locationTable.item(rowPosition, 1).text() + '",' + self.locationTable.item(rowPosition, 2).text() + ',' + 
            self.locationTable.item(rowPosition, 3).text() + ',' + self.locationTable.item(rowPosition, 4).text()+ ',' + self.locationTable.item(rowPosition, 5).text()+ ',' + self.locationTable.item(rowPosition, 6).text()+ ',' + self.locationTable.item(rowPosition, 7).text() + '\n')

            if (self.currentLine > self.linesBeforeFlush):
                self.streamingFile.flush()
                self.currentLine += 1
                    
        numRows = self.locationTable.rowCount()
        
        if numRows > 1:
            self.locationTable.scrollToItem(self.locationTable.item(0, 0))

    def onTableHeadingClicked(self, logical_index):
        header = self.locationTable.horizontalHeader()
        order = Qt.DescendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )
        self.locationTable.sortItems(logical_index, order )
        
    def updateData(self, newRadius):
       self.radar.updateData(newRadius)
       
    def showTelemetry(parent = None):
        dialog = TelemetryDialog(parent)
        result = dialog.exec_()
        return (result == QDialog.Accepted)

class BluetoothTelemetry(TelemetryDialog):
    def __init__(self, winTitle = "Bluetooth Telemetry", parent = None):
        super().__init__(winTitle, parent)

    def createTable(self):
        # Set up location table
        self.locationTable = QTableWidget(self)
        self.locationTable.setColumnCount(10)
        self.locationTable.setGeometry(10, 10, self.geometry().width()/2-20, self.geometry().height()/2)
        self.locationTable.setShowGrid(True)
        self.locationTable.setHorizontalHeaderLabels(['macAddr','Name', 'RSSI', 'TX Power', 'Est Range (m)', 'Timestamp','GPS', 'Latitude', 'Longitude', 'Altitude'])
        self.locationTable.resizeColumnsToContents()
        self.locationTable.setRowCount(0)
        self.locationTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        
        self.ntRightClickMenu = QMenu(self)
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopy)
        self.ntRightClickMenu.addAction(newAct)
        
        self.locationTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.locationTable.customContextMenuRequested.connect(self.showNTContextMenu)
                
    def onStreamClicked(self, pressed):
        if not self.btnStream.isChecked():
            # We're coming out of streaming
            self.streamingSave = False
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            if (self.streamingFile):
                self.streamingFile.close()
                self.streamingFile = None
            return
            
        self.btnStream.setStyleSheet("background-color: rgba(255,0,0,255);")
        self.streamingSave = True
        
        fileName = self.saveFileDialog()

        if not fileName:
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            self.btnStream.setChecked(False)
            return
            
        try:
            self.streamingFile = open(fileName, 'w', 1)  # 1 says use line buffering, otherwise it fully buffers and doesn't write
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            self.streamingFile = None
            self.streamingSave = False
            self.btnStream.setStyleSheet("background-color: rgba(2,128,192,255);")
            self.btnStream.setChecked(False)
            return
            
        self.streamingFile.write('MAC Address,Name,RSSI,TX Power,Est Range (m),Timestamp,GPS,Latitude,Longitude,Altitude\n')
                    
    def onExportClicked(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
            
        outputFile.write('MAC Address,Name,RSSI,TX Power,Est Range (m),Timestamp,GPS,Latitude,Longitude,Altitude\n')

        numItems = self.locationTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            return
           
        self.updateLock.acquire()
        
        for i in range(0, numItems):
            outputFile.write(self.locationTable.item(i, 0).text() + ',"' + self.locationTable.item(i, 1).text() + '",' + self.locationTable.item(i, 2).text() + ',' + self.locationTable.item(i, 3).text())
            outputFile.write(',' + self.locationTable.item(i, 4).text()+ ',' + self.locationTable.item(i, 5).text()+ ',' + self.locationTable.item(i, 6).text()+ ',' + self.locationTable.item(i, 7).text() + 
            ',' + self.locationTable.item(i, 8).text()+ ',' + self.locationTable.item(i, 9).text()  + '\n')
            
        self.updateLock.release()
        outputFile.close()
        
    def updateNetworkData(self, curDevice):
        if not self.isVisible():
            return
            
        # Signal is -NN dBm.  Need to make it positive for the plot
        self.radar.updateData(curDevice.rssi*-1)

        if len(curDevice.name) > 0:
            self.setWindowTitle(self.winTitle + " - " + curDevice.name)
        else:
            self.setWindowTitle(self.winTitle + " - " + curDevice.macAddress)
            
        self.radar.draw()
        
        #  Network changed.  Clear our table and time data
        updateChartAndTable = False
        
        self.updateLock.acquire()
        
        if self.lastSeen != curDevice.lastSeen:
            updateChartAndTable = True
        
        if updateChartAndTable:
            # Update chart
            numPoints = len(self.timeSeries.pointsVector())
            
            if numPoints >= self.maxPoints:
                self.timeSeries.remove(0)
                # Now we need to reset the x data to pull the series back
                counter = 0
                for curPoint in self.timeSeries.pointsVector():
                    self.timeSeries.replace(counter, counter, curPoint.y())
                    counter += 1
                    
            if curDevice.rssi >= -100:
                self.timeSeries.append(numPoints,curDevice.rssi)
            else:
                self.timeSeries.append(numPoints,-100)
                
            # Update Table
            self.addTableData(curDevice)
            
            # Limit points in each
            if self.locationTable.rowCount() > self.maxRowPoints:
                self.locationTable.setRowCount(self.maxRowPoints)
            
        self.updateLock.release()
        
    def addTableData(self, curDevice):
        if self.paused:
            return

        # rowPosition = self.locationTable.rowCount()
        # Always insert at row(0)
        rowPosition = 0
            
        self.locationTable.insertRow(rowPosition)
        
        #if (addedFirstRow):
        #    self.locationTable.setRowCount(1)
            
        # ['macAddr','name', 'rssi','tx power','est range (m)', 'Timestamp','GPS', 'Latitude', 'Longitude', 'Altitude']
        self.locationTable.setItem(rowPosition, 0, QTableWidgetItem(curDevice.macAddress))
        self.locationTable.setItem(rowPosition, 1, QTableWidgetItem(curDevice.name))
        self.locationTable.setItem(rowPosition, 2,  IntTableWidgetItem(str(curDevice.rssi)))
        
        if curDevice.txPowerValid:
            self.locationTable.setItem(rowPosition, 3,  IntTableWidgetItem(str(curDevice.txPower)))
        else:
            self.locationTable.setItem(rowPosition, 3,  IntTableWidgetItem('Unknown'))
            
        if curDevice.iBeaconRange != -1 and curDevice.txPowerValid:
            self.locationTable.setItem(rowPosition, 4,  IntTableWidgetItem(str(curDevice.iBeaconRange)))
        else:
            self.locationTable.setItem(rowPosition, 4,  IntTableWidgetItem(str('Unknown')))
            
        self.locationTable.setItem(rowPosition, 5, DateTableWidgetItem(curDevice.lastSeen.strftime("%m/%d/%Y %H:%M:%S")))
        if curDevice.gps.isValid:
            self.locationTable.setItem(rowPosition, 6, QTableWidgetItem('Yes'))
        else:
            self.locationTable.setItem(rowPosition, 6, QTableWidgetItem('No'))

        self.locationTable.setItem(rowPosition, 7,  FloatTableWidgetItem(str(curDevice.gps.latitude)))
        self.locationTable.setItem(rowPosition, 8,  FloatTableWidgetItem(str(curDevice.gps.longitude)))
        self.locationTable.setItem(rowPosition, 9,  FloatTableWidgetItem(str(curDevice.gps.altitude)))
        #order = Qt.DescendingOrder
        #self.locationTable.sortItems(3, order )
                    

        # If we're in streaming mode, write the data out to disk as well
        if self.streamingFile:
            self.streamingFile.write(self.locationTable.item(rowPosition, 0).text() + ',"' + self.locationTable.item(rowPosition, 1).text() + '",' + self.locationTable.item(rowPosition, 2).text() + ',' + 
            self.locationTable.item(rowPosition, 3).text() + ',' + self.locationTable.item(rowPosition, 4).text()+ ',' + self.locationTable.item(rowPosition, 5).text()+ ',' +
            self.locationTable.item(rowPosition, 6).text()+ ',' + self.locationTable.item(rowPosition, 7).text() + 
            + ',' + self.locationTable.item(rowPosition, 8).text()+ ',' + self.locationTable.item(rowPosition, 9).text() + '\n')

            if (self.currentLine > self.linesBeforeFlush):
                self.streamingFile.flush()
                self.currentLine += 1
                    
        numRows = self.locationTable.rowCount()
        
        if numRows > 1:
            self.locationTable.scrollToItem(self.locationTable.item(0, 0))
            
# -------  Main Routine For Debugging-------------------------

if __name__ == '__main__':
    app = QApplication([])
    # date, time, ok = DB2Dialog.getDateTime()
    # ok = TelemetryDialog.showTelemetry()
    # dialog = TelemetryDialog()
    dialog = BluetoothTelemetry()
    dialog.show()
    dialog.updateData(50)
    #print("{} {} {}".format(date, time, ok))
    app.exec_()

