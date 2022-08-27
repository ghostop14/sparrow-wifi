#!/usr/bin/python3

# 
###################################################################
#
# Application: Sparrow-WiFi
# Module: falconwifidialogs.py
# Author: ghostop14
# Copyright 2017 ghostop14, All Rights Reserved
#
##################################################################
#

from PyQt5.QtWidgets import QDialog, QLabel, QTextEdit, QComboBox, QTableWidget,QTableWidgetItem, QPushButton, QHeaderView
from PyQt5.QtWidgets import QDesktopWidget, QApplication, QMessageBox, QMenu, QAction, QFileDialog, QAbstractItemView
from PyQt5.QtGui import QColor,QPalette, QFont, QIcon
from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtCore import Qt

import datetime
# from time import sleep
import json
import requests

try:
    from manuf import manuf
    hasOUILookup = True
except:
    hasOUILookup = False
    
import os
import sys
from threading import Lock

if '..' not in sys.path:
    sys.path.insert(0, '..')
    
from wirelessengine import WirelessEngine, WirelessNetwork, WirelessClient
from sparrowtablewidgets import DateTableWidgetItem, IntTableWidgetItem
from falconwifi import FalconWirelessEngine, WEPCrack, WPAPSKCrack, FalconDeauth
from telemetry import TelemetryDialog
from sparrowgps import SparrowGPS

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
            ouidb = None
    else:
        ouidb = None
        
    return ouidb
    
# ------------------  Global functions for agent HTTP requests ------------------------------
# -----------  Basic GET/POST Methods --------------------------------------------        
def makeGetRequest(url, waitTimeout=4):
    try:
        # Not using a timeout can cause the request to hang indefinitely
        response = requests.get(url, timeout=waitTimeout)
    except:
        return -1, ""
        
    if response.status_code != 200:
        return response.status_code, ""
        
    htmlResponse=response.text
    return response.status_code, htmlResponse

def makePostRequest(url, jsonstr, waitTimeout=4):
        # use something like jsonstr = json.dumps(somestring) to get the right format
        try:
            response = requests.post(url, data=jsonstr, timeout=waitTimeout)
        except:
            return -1, ""
        
        htmlResponse=response.text
        return response.status_code, htmlResponse
        
# -----------  Crack Methods --------------------------------------------        
def execRemoteCrack(remoteIP, remotePort, type, curInterface, channel, ssid, apMacAddr, hasClient=False):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/falcon/startcrack"

    jsondict = {}
    jsondict['cracktype']  = type # This will be wep or wpapsk
    jsondict['interface'] = curInterface
    jsondict['channel'] = channel
    jsondict['ssid'] = ssid
    jsondict['apmacaddr'] = apMacAddr
    jsondict['hasclient'] = hasClient
    
    jsonstr = json.dumps(jsondict)
    statusCode, responsestr = makePostRequest(url, jsonstr)

    errmsg = ""
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, "Unknown error parsing json response"
    elif statusCode == 400:
        # 400 is a bad request and contains a JSON response
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

def stopRemoteCrack(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/stopcrack/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return retVal, errmsg
        except:
            return -1, "Error parsing response"
    else:
        return -2, "Error in agent's response"
        
def getRemoteWEPCrackStatus(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/crackstatuswep/" + interface
        
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errmsg = responsedict['errmsg']
            
            if retVal == 0:
                isRunning = responsedict['isrunning']
                ivcount = responsedict['ivcount']
                crackedPasswords = responsedict['crackedpasswords']
            else:
                isRunning = False
                ivcount = 0
                crackedPasswords = []
                
            return retVal, errmsg, isRunning, ivcount, crackedPasswords
        except:
            return -1, "Error parsing response", 0, False, None
    else:
        return -2, "Error in agent's response", 0, False, None

def getRemoteWPAPSKCrackStatus(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/crackstatuswpapsk/" + interface
        
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errmsg = responsedict['errmsg']
            
            if retVal == 0:
                isRunning = responsedict['isrunning']
                hasHandshake = responsedict['hashandshake']
                captureFilename = responsedict['capturefile']
            else:
                isRunning = False
                hasHandshake = False
                captureFilename = ""
            return retVal, errmsg, isRunning, hasHandshake, captureFilename
        except:
            return -1, "Error parsing response", False, False, ""
    else:
        return -2, "Error in agent's response", False, False, ""

# -----------  Deauth Methods --------------------------------------------        
def execRemoteDeauth(remoteIP, remotePort, newDeauth, continuous):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/falcon/deauth"

    jsondict = newDeauth.toJsondict()
    jsondict['continuous'] = continuous
    
    jsonstr = json.dumps(jsondict)
    statusCode, responsestr = makePostRequest(url, jsonstr)

    errmsg = ""
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, "Unknown error parsing json response"
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

def stopRemoteDeauth(remoteIP, remotePort, newDeauth):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/falcon/stopdeauth"

    # NOTE: There isn't a risk of killing incorrect processes here since the processid isn't trusted from the
    # GUI.  The correct deauth object is looked up by a key and the processid stored for that key is used,
    # so sending an incorrect processid won't have any effect.
    jsondict = newDeauth.toJsondict()
    
    jsonstr = json.dumps(jsondict)
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
        
def requestRemoteMonitoringInterfaces(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/wireless/moninterfaces"
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
        
def stopAllRemoteDeauths(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/stopalldeauths/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errMsg = responsedict['errmsg']
            return retVal, errMsg
        except:
            return statusCode, responsestr
    else:
        return statusCode,responsestr
        
def startRemoteMonitoringInterface(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/startmonmode/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errMsg = responsedict['errmsg']
            return retVal, errMsg
        except:
            return statusCode, responsestr
    else:
        return statusCode,responsestr
        
def stopRemoteMonitoringInterface(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/stopmonmode/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errMsg = responsedict['errmsg']
            return retVal, errMsg
        except:
            return statusCode, responsestr
    else:
        return statusCode,responsestr
        
def remoteScanRunning(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/scanrunning/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errMsg = responsedict['errmsg']
            return retVal, errMsg
        except:
            return statusCode, responsestr
    else:
        return statusCode,responsestr
        
def startRemoteScan(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/startscan/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errMsg = responsedict['errmsg']
            return retVal, errMsg
        except:
            return statusCode, responsestr
    else:
        return statusCode,responsestr
        
def stopRemoteScan(agentIP, agentPort, interface):
    url = "http://" + agentIP + ":" + str(agentPort) + "/falcon/stopscan/" + interface
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            
            retVal = responsedict['errcode']
            errMsg = responsedict['errmsg']
            return retVal, errMsg
        except:
            return statusCode, responsestr
    else:
        return statusCode,responsestr
        
def requestAdvancedRemoteNetworks(remoteIP, remotePort):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/falcon/getscanresults"
    
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            networkjson = json.loads(responsestr)
            wirelessNetworks = {}
            wirelessClients = {}

            if 'networks' in networkjson:
                for curNetDict in networkjson['networks']:
                    newNet = WirelessNetwork.createFromJsonDict(curNetDict)
                    wirelessNetworks[newNet.getKey()] = newNet

            if 'clients' in networkjson:
                for curClientDict in networkjson['clients']:
                    newClient = WirelessClient.createFromJsonDict(curClientDict)
                    wirelessClients[newClient.getKey()] = newClient
                
            return networkjson['errCode'], networkjson['errString'], wirelessNetworks, wirelessClients
        except:
            return -2, "Error parsing remote agent response", None, None
    else:
        return -1, "Error connecting to remote agent", None, None

# ------------------  AdvancedScanDialog functions ------------------------------

class AdvancedScanDialog(QDialog):
    resized = QtCore.pyqtSignal()
    visibility = QtCore.pyqtSignal(bool)
    
    def __init__(self, useRemoteAgent=False, remoteAgentIP="",  remoteAgentPort=8020, mainWin=None,  parent = None):
        super(AdvancedScanDialog, self).__init__(parent)
        self.setWindowIcon(QIcon('../wifi_icon.png'))        

        self.visibility.connect(self.onVisibilityChanged)

        self.mainWin = mainWin
        
        self.clientTelemetryWindows = {}
        self.networkTelemetryWindows = {}
        self.updateLock = Lock()

        self.scanProc = None

        self.ouiLookupEngine = getOUIDB()

        self.usingBlackoutColors = False
        
        self.usingRemoteAgent = useRemoteAgent
        self.remoteAgentIP = remoteAgentIP
        self.remoteAgentPort = remoteAgentPort
        
        self.updateWindowTitle()
            
        self.startedMonMode = False
        self.airodumpxmlfile = '/dev/shm/falconairodump-01.kismet.netxml'
        self.airodumpcsvfile = '/dev/shm/falconairodump-01.csv'

        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        self.center()

        self.createControls()
        
        if os.geteuid() != 0:
            self.runningAsRoot = False
            self.statBar.setText('You need to have root privileges to run local scans.  Please exit and rerun it as root')
            print("You need to have root privileges to run this script.\nPlease try again, this time using 'sudo'. Exiting.")
            QMessageBox.question(self, 'Warning',"You need to have root privileges to run local scans.", QMessageBox.Ok)
            
        self.runningAsRoot = False

        if  (not os.path.isfile('/usr/sbin/airodump-ng')) and (not os.path.isfile('/usr/local/sbin/airodump-ng')):
            QMessageBox.question(self, 'Error',"Unable to locate the airodump-ng tool.  Please check your system or install the 'aircrack-ng' toolset.", QMessageBox.Ok)
            return

        if  (not os.path.isfile('/usr/bin/aircrack-ng')) and (not os.path.isfile('/usr/local/bin/aircrack-ng')):
            QMessageBox.question(self, 'Error',"Unable to locate the aircrack-ng tool.  Please check your system or install the 'aircrack-ng' toolset.", QMessageBox.Ok)
            return

        if (os.path.isfile('/usr/local/bin/wpapcap2john') == False) and (os.path.isfile('/usr/bin/wpapcap2john') == False):
            QMessageBox.question(self, 'Warning',"wpapcap2john is better at finding WPA hashes on captures than aircrack, but it does not appear to be in /usr/local/bin or /usr/bin.  If you have john on this system, please copy or create a symlink to wpapcap2john in /usr/local/bin for best results.", QMessageBox.Ok)
            
        # Timer to incrementally read tmp file
        self.updateTimer = QTimer()
        self.updateTimer.timeout.connect(self.onUpdateTimer)
        self.updateTimer.setSingleShot(True)
        
        self.updateTimerTimeout = 4000
        
        self.initCrackEngines()
        
        self.checkScanAlreadyRunning()

    def setLocal(self):
        # self.clearTables()
        self.resetTableColors()

        # Going local, we don't have to worry so much about it, but going remote, we'd need to stop any running scans.
        self.wepCrackRunning = False
        self.wpaCrackRunning = False
        
        if self.scanRunning():
            self.stopScan(True)
        
        self.usingRemoteAgent = False
        
        self.fillComboBoxes()
        
        self.updateWindowTitle()
        self.checkScanAlreadyRunning()
        
    def setRemoteAgent(self, agentIP, agentPort):
        self.wepCrackRunning = False
        if self.wepCrack.running():
            self.wepCrack.stopCrack()
            
        self.wpaCrackRunning = False
        if self.wpaPSKCrack.running():
            self.wpaPSKCrack.stopCrack()
        
        if self.scanRunning():
            self.stopScan(True)
            
        self.stopAllDeauths()
        
        self.usingRemoteAgent = True
        self.remoteAgentIP = agentIP
        self.remoteAgentPort = agentPort

        self.fillComboBoxes()
        
        # self.clearTables()
        self.resetTableColors()
        self.updateWindowTitle()
        self.checkScanAlreadyRunning()

        
    def updateWindowTitle(self):
        title = 'Falcon Attack Tools'
        
        if self.usingRemoteAgent:
            title += " - " + self.remoteAgentIP + ":" + str(self.remoteAgentPort)
            
        self.setWindowTitle(title)
        
    def clearTables(self):
        self.updateLock.acquire()

        self.networkTable.setRowCount(0)
        self.clientTable.setRowCount(0)

        self.updateLock.release()
        
    def onVisibilityChanged(self, visible):
        try:
            if not visible:
                # Transitioned to hidden, if we have a scan running, stop it.
                if self.scanRunning():
                    self.stopScan()
        except:
            pass
            
    def hideEvent(self, event):
        self.visibility.emit(False)
        
    def initCrackEngines(self):
        # WPA PSK key monitoring
        self.wpaTimer = QTimer()
        self.wpaTimer.timeout.connect(self.onWPATimer)
        self.wpaTimer.setSingleShot(True)
        self.wpaTimerTimeout = 5000
        self.wpaTimer.setInterval(self.wpaTimerTimeout)
        
        self.wpaPSKCrack = WPAPSKCrack()
        self.wpaCrackRunning = False
        self.wpaSSID = ""

        # WEP key monitoring
        self.wepTimer = QTimer()
        self.wepTimer.timeout.connect(self.onWEPTimer)
        self.wepTimer.setSingleShot(True)        
        self.wepTimerTimeout = 5000
        self.wepTimer.setInterval(self.wpaTimerTimeout)
        
        self.wepCrack = WEPCrack()
        self.wepCrackRunning = False
        self.wepUsingClient = False
        self.wepSSID = ""

    def fillComboBoxes(self):
        # clear any current entries on a refresh
        self.loadingComboBoxes = True
        self.comboInterfaces.clear()
        self.comboMonInterfaces.clear()
        
        # Get regular interfaces
        if self.usingRemoteAgent:
            statusCode, interfaces = requestRemoteInterfaces(self.remoteAgentIP, self.remoteAgentPort)
            if statusCode != 200:
                interfaces = []
                QMessageBox.question(self, 'Error',"An error occurred getting remote interfaces from the agent.", QMessageBox.Ok)
        else:
            interfaces=WirelessEngine.getInterfaces()

        if (interfaces is not None) and (len(interfaces) > 0):
            for curInterface in interfaces:
                self.comboInterfaces.addItem(curInterface)
        else:
            self.statBar.setText('No wireless interfaces found.')
            self.btnMonMode.setEnabled(False)
            
        # get monitoring interfaces
        if self.usingRemoteAgent:
            statusCode, interfaces = requestRemoteMonitoringInterfaces(self.remoteAgentIP, self.remoteAgentPort)
            if statusCode != 200:
                interfaces = []
                QMessageBox.question(self, 'Error',"An error occurred getting remote monitoring interfaces from the agent.", QMessageBox.Ok)
        else:
            interfaces = WirelessEngine.getMonitoringModeInterfaces()

        if (interfaces is not None) and (len(interfaces) > 0):
            for curInterface in interfaces:
                self.comboMonInterfaces.addItem(curInterface)
        else:
            self.statBar.setText('No Monitoring Mode wireless interfaces found.')
        
        self.loadingComboBoxes = False
        
    def createComboBoxes(self):
        # Wireless Interfaces
        self.lblInterface = QLabel("Local Wireless Interface", self)
        self.lblInterface.setGeometry(10, 10, 220, 30)
        
        self.comboInterfaces = QComboBox(self)
        self.comboInterfaces.setGeometry(180, 10, 100, 28)

        # Create Mon Mode Button
        self.btnMonMode = QPushButton("Create Monitoring Interface", self)
        # self.btnMonMode.setStyleSheet("background-color: rgba(0,128,192,255);")
        self.btnMonMode.setGeometry(300, 10, 200, 30)
        self.btnMonMode.clicked.connect(self.onCreateMonClicked)

        # Monitoring Mode Wireless Interfaces
        self.lblMonInterface = QLabel("Local Monitoring Interface", self)
        self.lblMonInterface.setGeometry(10, 50, 220, 30)
        
        self.comboMonInterfaces = QComboBox(self)
        self.comboMonInterfaces.setGeometry(180, 50, 100, 28)
        # Want to recheck if a scan is already running
        self.loadingComboBoxes = False
        self.comboMonInterfaces.currentIndexChanged.connect(self.onMonInterfaceChanged)


        # Stop Mon Mode Button
        self.btnStopMonMode = QPushButton("Stop Monitoring Interface", self)
        # self.btnMonMode.setStyleSheet("background-color: rgba(0,128,192,255);")
        self.btnStopMonMode.setGeometry(300, 50, 200, 30)
        self.btnStopMonMode.clicked.connect(self.onStopMonClicked)
        
        self.fillComboBoxes()
        
    def setBlackoutColors(self):
        self.usingBlackoutColors = True

        self.networkTable.setStyleSheet("QTableView {background-color: black;gridline-color: white;color: white} QTableCornerButton::section{background-color: white;}")
        headerStyle = "QHeaderView::section{background-color: white;border: 1px solid black;color: black;} QHeaderView::down-arrow,QHeaderView::up-arrow {background: none;}"
        self.networkTable.horizontalHeader().setStyleSheet(headerStyle)
        self.networkTable.verticalHeader().setStyleSheet(headerStyle)
        
        self.clientTable.setStyleSheet("QTableView {background-color: black;gridline-color: white;color: white} QTableCornerButton::section{background-color: white;}")
        self.clientTable.horizontalHeader().setStyleSheet(headerStyle)
        self.clientTable.verticalHeader().setStyleSheet(headerStyle)
        
    def createControls(self):
        self.statBar = QLabel(self)
        self.statBar.setStyleSheet("QLabel{background:rgba(192,192,192,255);color:black;border: 1px solid blue; border-radius: 1px;}")
        self.statBar.setText('Ready')
           
        self.createComboBoxes()
   
        # Scan Button
        self.btnScan = QPushButton("&Scan", self)
        self.btnScan.setCheckable(True)
        self.btnScan.setShortcut('Ctrl+S')
        self.btnScan.setStyleSheet("background-color: rgba(0,128,192,255);")
        self.btnScan.clicked[bool].connect(self.onScanClicked)
        self.btnScan.setGeometry(300, 90, 100, 30)
        self.btnScan.setChecked(False)
        # Update Main Button
        # This isn't necessary anymore.  It happens automatically now.
        #if (self.mainWin):
        #    self.btnUpdate = QPushButton("Update Hidden SSIDs", self)
        #    self.btnUpdate.setStyleSheet("background-color: rgba(0,128,192,255);")
        #    self.btnUpdate.clicked.connect(self.onUpdateHiddenSSIDs)
        #else:
        #    self.btnUpdate = None
        
        # Network Table
        self.lblNet = QLabel("Networks", self)
        self.lblNet.setGeometry(10, 103, 220, 30)
        self.networkTable = QTableWidget(self)
        self.networkTable.setColumnCount(12)
        # self.networkTable.setGeometry(10, 100, self.mainWidth-60, self.mainHeight/2-105)
        self.networkTable.setShowGrid(True)
        self.networkTable.setHorizontalHeaderLabels(['macAddr', 'vendor','SSID', 'Security', 'Privacy', 'Channel', 'Frequency', 'Signal Strength', 'Bandwidth', 'Last Seen', 'First Seen', 'GPS'])
        self.networkTable.resizeColumnsToContents()
        self.networkTable.setRowCount(0)
        self.networkTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        self.networkTable.horizontalHeader().sectionClicked.connect(self.onNetTableHeadingClicked)

        self.networkTable.setSelectionMode( QAbstractItemView.SingleSelection )
        
        self.networkTableSortOrder = Qt.DescendingOrder
        self.networkTableSortIndex = -1
        self.clientTableSortOrder = Qt.DescendingOrder
        self.clientTableSortIndex = -1
        
        # Network Table right-click menu
        self.networkRightClickMenu = QMenu(self)

        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopyNet)
        self.networkRightClickMenu.addAction(newAct)

        self.networkRightClickMenu.addSeparator()
        
        newAct = QAction('Telemetry', self)        
        newAct.setStatusTip('View network telemetry data')
        newAct.triggered.connect(self.onShowNetworkTelemetry)
        self.networkRightClickMenu.addAction(newAct)
        
        self.networkRightClickMenu.addSeparator()
        
        self.deauthSingleNet = QAction('Deauth Broadcast - Single', self)        
        self.deauthSingleNet.setStatusTip('Kick a client off its associated AP')
        self.deauthSingleNet.triggered.connect(self.onDeauthSingleNet)
        self.networkRightClickMenu.addAction(self.deauthSingleNet)
        
        self.deauthContinuousNet = QAction('Deauth Broadcast - Continuous', self)        
        self.deauthContinuousNet.setStatusTip('Kick a client off its associated AP')
        self.deauthContinuousNet.triggered.connect(self.onDeauthContinuousNet)
        self.networkRightClickMenu.addAction(self.deauthContinuousNet)
        
        self.deauthStopNet = QAction('Stop deauth', self)        
        self.deauthStopNet.setStatusTip('Kick a client off its associated AP')
        self.deauthStopNet.triggered.connect(self.onDeauthStopNet)
        self.networkRightClickMenu.addAction(self.deauthStopNet)
        
        self.netCaptureWEP = QAction('Capture WEP Keys', self)        
        self.netCaptureWEP.setStatusTip('Capture WEP Keys')
        self.netCaptureWEP.triggered.connect(self.onNetCaptureWEPKeys)
        self.networkRightClickMenu.addAction(self.netCaptureWEP)
        
        self.netStopWEP = QAction('Stop WEP Capture', self)        
        self.netStopWEP.setStatusTip('Stop WEP Capture')
        self.netStopWEP.triggered.connect(self.onNetStopWEP)
        self.networkRightClickMenu.addAction(self.netStopWEP)
        
        self.netCaptureWPA = QAction('Capture WPA Keys', self)        
        self.netCaptureWPA.setStatusTip('Capture WPA Keys')
        self.netCaptureWPA.triggered.connect(self.onNetCaptureWPAKeys)
        self.networkRightClickMenu.addAction(self.netCaptureWPA)
        
        self.netStopWPA = QAction('Stop WPA Capture', self)        
        self.netStopWPA.setStatusTip('Stop WPA key capture')
        self.netStopWPA.triggered.connect(self.onNetStopWPA)
        self.networkRightClickMenu.addAction(self.netStopWPA)
        
        #newAct = QAction('Clone', self)        
        #newAct.setStatusTip('Clone an access point')
        #newAct.triggered.connect(self.onCloneAP)
        #self.networkRightClickMenu.addAction(newAct)
        
        # Attach it to the table
        self.networkTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.networkTable.customContextMenuRequested.connect(self.showNetworkContextMenu)
        
        # Export Button
        self.btnExport = QPushButton("&Export Clients", self)
        self.btnExport.setStyleSheet("background-color: rgba(0,128,192,255);")
        self.btnExport.clicked.connect(self.onExportClicked)
        
        # Client statiion Table
        self.lblClients = QLabel("Client Stations", self)
        self.clientTable = QTableWidget(self)
        self.clientTable.setColumnCount(10)
        self.clientTable.setShowGrid(True)
        self.clientTable.setHorizontalHeaderLabels(['Station Mac', 'Vendor','Associated AP','Assoc SSID','Channel','Signal Strength','Last Seen', 'First Seen', 'GPS','Probed SSIDs'])
        self.clientTable.resizeColumnsToContents()
        self.clientTable.setRowCount(0)
        self.clientTable.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.clientTable.horizontalHeader().sectionClicked.connect(self.onClientTableHeadingClicked)
        
        self.clientTable.setSelectionMode( QAbstractItemView.SingleSelection )
        
        # Client Table right-click menu
        self.clientRightClickMenu = QMenu(self)
        
        newAct = QAction('Copy', self)        
        newAct.setStatusTip('Copy data to clipboard')
        newAct.triggered.connect(self.onCopyClient)
        self.clientRightClickMenu.addAction(newAct)

        self.clientRightClickMenu.addSeparator()
        
        newAct = QAction('Telemetry', self)        
        newAct.setStatusTip('View network telemetry data')
        newAct.triggered.connect(self.onShowClientTelemetry)
        self.clientRightClickMenu.addAction(newAct)
        
        self.clientRightClickMenu.addSeparator()
        
        self.deauthSingle = QAction('Deauth Client - Single', self)        
        self.deauthSingle.setStatusTip('Kick a client off its associated AP')
        self.deauthSingle.triggered.connect(self.onDeauthClientSingle)
        self.clientRightClickMenu.addAction(self.deauthSingle)
        
        self.deauthContinuous = QAction('Deauth Client - Continuous', self)        
        self.deauthContinuous.setStatusTip('Kick a client off its associated AP')
        self.deauthContinuous.triggered.connect(self.onDeauthClientContinuous)
        self.clientRightClickMenu.addAction(self.deauthContinuous)
        
        self.deauthStop = QAction('Stop deauth', self)        
        self.deauthStop.setStatusTip('Kick a client off its associated AP')
        self.deauthStop.triggered.connect(self.onDeauthStop)
        self.clientRightClickMenu.addAction(self.deauthStop)
        
        self.clientCaptureWEP = QAction('Capture WEP Keys', self)        
        self.clientCaptureWEP.setStatusTip('Capture WEP Keys')
        self.clientCaptureWEP.triggered.connect(self.onClientCaptureWEPKeys)
        self.clientRightClickMenu.addAction(self.clientCaptureWEP)
        
        self.clientStopWEP = QAction('Stop WEP Capture', self)        
        self.clientStopWEP.setStatusTip('Stop WEP Capture')
        self.clientStopWEP.triggered.connect(self.onClientStopWEP)
        self.clientRightClickMenu.addAction(self.clientStopWEP)
        
        # Attach it to the table
        self.clientTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.clientTable.customContextMenuRequested.connect(self.showClientContextMenu)
        
        self.setBlackoutColors()
        
    def resizeEvent(self, event):
        # self.resized.emit()
        # self.statusBar().showMessage('Window resized.')
        # return super(mainWin, self).resizeEvent(event)
        size = self.geometry()
        self.statBar.setGeometry(0, size.height()-30, size.width(), 30)
        self.networkTable.setGeometry(10, 132, size.width()-20, size.height()//2-105)
       
        self.lblClients.move(10, size.height()//2+45)
        self.clientTable.setGeometry(10, size.height()//2+70, size.width()-20, size.height()//2-110)
        # self.console.setGeometry(10, 150, size.width()-20, size.height()-150)
        # self.btnScan.setGeometry(size.width()-110,30, 100, 30)
        
        #if (self.mainWin):
        #    self.btnUpdate.setGeometry(size.width()-160, 90, 150, 30)
            
        self.btnExport.setGeometry(size.width()-130, size.height()//2+33, 120, 30)
        
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
        try:
            # Stop active AP/Client scans if active
            if self.scanRunning():
                curInterface = str(self.comboMonInterfaces.currentText())
                
                if self.usingRemoteAgent:
                    stopRemoteScan(self.remoteAgentIP, self.remoteAgentPort, curInterface)
                else:
                    FalconWirelessEngine.airodumpStop(curInterface)
                    
            # stop any deauth processes
            self.stopAllDeauths()
            
            # stop any WEP cracking processes
            if self.wepCrackRunning:
                if self.usingRemoteAgent:
                    curInterface = self.getMonitoringInterface()
                    if len(curInterface) > 0:
                        stopRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, curInterface)
                else:
                    self.wepCrack.stopCrack()
                
                self.wepCrackRunning = False
                
            # stop any WPA PSK cracking processes
            if self.wpaCrackRunning:
                if self.usingRemoteAgent:
                    curInterface = self.getMonitoringInterface()
                    if len(curInterface) > 0:
                        stopRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, curInterface)
                else:
                    self.wpaPSKCrack.stopCrack()
            
                self.wpaCrackRunning = False
                
            # Close any open telemetry windows
            keysToRemove = []
            
            for curKey in self.networkTelemetryWindows.keys():
                curWindow = self.networkTelemetryWindows[curKey]
                try:
                    curWindow.close()
                    self.networkTelemetryWindows[curKey] = None
                    keysToRemove.append(curKey)
                except:
                    pass
                    
            # Have to separate the iteration and the removal or you'll get a "list changed during iteration" exception
            for curKey in keysToRemove:
                del self.networkTelemetryWindows[curKey]
            
            keysToRemove.clear()
            
            for curKey in self.clientTelemetryWindows.keys():
                curWindow = self.clientTelemetryWindows[curKey]
                try:
                    curWindow.close()
                    self.clientTelemetryWindows[curKey] = None
                    keysToRemove.append(curKey)
                except:
                    pass
                    
            for curKey in keysToRemove:
                del self.clientTelemetryWindows[curKey]
                
            # signal that we closed
            if self.mainWin:
                self.mainWin.advScanClosed.emit()
                
            # accept the event
            event.accept()
        except OSError as e:
            print("Caught error:" + e.strerror)
        except:
            print("Caught error.")
            
    
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
            curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
            curText = 'Last Recorded GPS Coordinates:\n' + str(curNet.gps)
            curText += 'Strongest Signal Coordinates:\n'
            curText += 'Strongest Signal: ' + str(curNet.strongestsignal) + '\n'
            curText += str(curNet.strongestgps)
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)
        
        self.updateLock.release()
        
    def onCopyClient(self):
        self.updateLock.acquire()
        
        curRow = self.clientTable.currentRow()
        curCol = self.clientTable.currentColumn()
        
        if curRow == -1 or curCol == -1:
            self.updateLock.release()
            return
        
        if curCol != 8:
            curText = self.clientTable.item(curRow, curCol).text()
        else:
            curNet = self.clientTable.item(curRow, 0).data(Qt.UserRole)
            curText = 'Last Recorded GPS Coordinates:\n' + str(curNet.gps)
            curText += 'Strongest Signal Coordinates:\n'
            curText += 'Strongest Signal: ' + str(curNet.strongestsignal) + '\n'
            curText += str(curNet.strongestgps)
            
        clipboard = QApplication.clipboard()
        clipboard.setText(curText)
        
        self.updateLock.release()
        
    def netCaptureWPA(self):
        FalconWirelessEngine.testWPACapture('D8:EB:97:2F:DD:CE', '/tmp/falconcap-01.cap')
        
    def onExportClicked(self):
        fileName = self.saveFileDialog()

        if not fileName:
            return
            
        try:
            outputFile = open(fileName, 'w')
        except:
            QMessageBox.question(self, 'Error',"Unable to write to " + fileName, QMessageBox.Ok)
            return
            
        outputFile.write('Station Mac,Vendor,Associated AP,Associated SSID,Channel,Signal Strength,Last Seen,First Seen,Probed SSIDs,GPS Valid,Latitude,Longitude,Altitude,Speed,Strongest GPS Valid,Strongest Latitude,Strongest Longitude,Strongest Altitude,Strongest Speed\n')

        numItems = self.clientTable.rowCount()
        
        if numItems == 0:
            outputFile.close()
            return
           
        for i in range(0, numItems):
            curData = self.clientTable.item(i, 0).data(Qt.UserRole)

            ssidList = ""
            for curSSID in curData.probedSSIDs:
                if len(ssidList) == 0:
                    ssidList = curSSID
                else:
                    ssidList += " " + curSSID
                    
            outputFile.write(self.clientTable.item(i, 0).text() + ',' + self.clientTable.item(i, 1).text() + ',' + self.clientTable.item(i, 2).text() + ',' + self.clientTable.item(i, 3).text() + ',' + self.clientTable.item(i, 4).text() + ',' + self.clientTable.item(i, 5).text())
            outputFile.write(','+curData.lastSeen.strftime("%m/%d/%Y %H:%M:%S") + ',' + curData.firstSeen.strftime("%m/%d/%Y %H:%M:%S") + ',"' + ssidList + '",' +
                                    str(curData.gps.isValid) + ',' + str(curData.gps.latitude) + ',' + str(curData.gps.longitude) + ',' + str(curData.gps.altitude) + ',' + str(curData.gps.speed) + ',' + 
                                    str(curData.strongestgps.isValid) + ',' + str(curData.strongestgps.latitude) + ',' + str(curData.strongestgps.longitude) + ',' + str(curData.strongestgps.altitude) + ',' + str(curData.strongestgps.speed) + '\n')
            
        outputFile.close()
        
    def saveFileDialog(self, fileSpec="CSV Files (*.csv);;All Files (*)"):    
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self,"QFileDialog.getSaveFileName()","",fileSpec, options=options)
        if fileName:
            return fileName
        else:
            return None

    def showNetworkContextMenu(self, pos):
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
            
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
        
        if curNet.security != "WEP":
            self.netStopWEP.setVisible(False)
            self.netStopWEP.setVisible(False)
            self.netCaptureWEP.setVisible(False)
        else:
            if self.wepCrackRunning:
                self.netStopWEP.setVisible(True)
                self.netCaptureWEP.setVisible(False)
            else:
                self.netStopWEP.setVisible(False)
                self.netCaptureWEP.setVisible(True)
            
        if curNet.security != "PSK":
            self.netCaptureWPA.setVisible(False)
            self.netStopWPA.setVisible(False)
        else:
            if self.wpaCrackRunning:
                self.netStopWPA.setVisible(True)
                self.netCaptureWPA.setVisible(False)
            else:
                self.netStopWPA.setVisible(False)
                self.netCaptureWPA.setVisible(True)
            
        if self.networkTable.item(curRow, 0).data(Qt.UserRole+1):
            # deauth
            self.deauthSingleNet.setVisible(False)
            self.deauthContinuousNet.setVisible(False)
            self.deauthStopNet.setVisible(True)
        else:
            self.deauthSingleNet.setVisible(True)
            self.deauthContinuousNet.setVisible(True)
            self.deauthStopNet.setVisible(False)
            
        if self.hasCrackRunning():
            # disable any "start" choices
                self.netCaptureWPA.setVisible(False)
                self.netCaptureWEP.setVisible(False)
            
        self.networkRightClickMenu.exec_(self.networkTable.mapToGlobal(pos))
        
    def deauthRunning(self):
        rowCount = self.clientTable.rowCount()
        
        if rowCount <= 0:
            return False
            
        for curRow in range(0, rowCount):
            # Deauth data is in UserRole + 1.  It'll be none if one isn't running
            if self.clientTable.item(curRow, 0).data(Qt.UserRole+1):
                return True
        
        rowCount = self.networkTable.rowCount()
        
        if rowCount <= 0:
            return False
            
        for curRow in range(0, rowCount):
            # Deauth data is in UserRole + 1.  It'll be none if one isn't running
            if self.networkTable.item(curRow, 0).data(Qt.UserRole+1):
                return True
                
        return False

    def runningDeauthChannel(self):
        # return of 0 indicates no deauth in progress
        rowCount = self.clientTable.rowCount()
        
        if rowCount <= 0:
            return 0
            
        for curRow in range(0, rowCount):
            # Deauth data is in UserRole + 1.  It'll be none if one isn't running
            curDeauth = self.clientTable.item(curRow, 0).data(Qt.UserRole+1)
            if curDeauth:
                return curDeauth.channel   # If there's one running they should all be on the same channel.
        
        return 0
        
    def stopAllDeauths(self):
        self.updateLock.acquire()
        
        # Kill any network deauths
        rowCount = self.networkTable.rowCount()
        
        if rowCount > 0:
            for curRow in range(0, rowCount):
                curDeauth = self.networkTable.item(curRow, 0).data(Qt.UserRole+1)
                if curDeauth:
                    if self.usingRemoteAgent:
                        retVal, errmsg = stopRemoteDeauth(self.remoteAgentIP, self.remoteAgentPort, curDeauth)
                        
                        if retVal != 0:
                            QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                    else:
                        curDeauth.kill()

                    self.networkTable.item(curRow, 0).setData(Qt.UserRole+1, None)
        
        if self.usingRemoteAgent:
            # send catch-all kill deauths for this interface in case we got disconnected.
            if (self.comboMonInterfaces.count() > 0):
                curInterface = str(self.comboMonInterfaces.currentText())
                retVal, errmsg = stopAllRemoteDeauths(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            
        # kill any client deauths
        rowCount = self.clientTable.rowCount()
        
        if rowCount > 0:
            for curRow in range(0, rowCount):
                curDeauth = self.clientTable.item(curRow, 0).data(Qt.UserRole+1)
                if curDeauth:
                    if self.usingRemoteAgent:
                        retVal, errmsg = stopRemoteDeauth(self.remoteAgentIP, self.remoteAgentPort, curDeauth)
                        
                        if retVal != 0:
                            QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                    else:
                        curDeauth.kill()
                    self.clientTable.item(curRow, 0).setData(Qt.UserRole+1, None)
        
        self.updateLock.release()
        
        return True

        
    def onShowClientTelemetry(self):
        self.updateLock.acquire()
        
        curRow = self.clientTable.currentRow()
        
        if curRow == -1:
            self.updateLock.release()
            return
        
        curClient = self.clientTable.item(curRow, 0).data(Qt.UserRole)
        
        if curClient == None:
            self.updateLock.release()
            return
       
        if curClient.getKey() not in self.clientTelemetryWindows.keys():
            telemetryWindow = TelemetryDialog(parent=None, winTitle="Client Telemetry")
            self.clientTelemetryWindows[curClient.getKey()] = telemetryWindow
        else:
            telemetryWindow = self.clientTelemetryWindows[curClient.getKey()]
        
        # Can also key off of self.telemetryWindow.isVisible()
        telemetryWindow.show()
        telemetryWindow.activateWindow()
        
        # Can do telemetry window updates after release
        self.updateLock.release()
        
        # User could have selected a different network.
        telemetryWindow.updateNetworkData(curClient)            
        
    def onShowNetworkTelemetry(self):
        self.updateLock.acquire()
        
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            self.updateLock.release()
            return
        
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
        
        if curNet == None:
            self.updateLock.release()
            return
       
        if curNet.getKey() not in self.networkTelemetryWindows.keys():
            telemetryWindow = TelemetryDialog(parent=None)
            self.networkTelemetryWindows[curNet.getKey()] = telemetryWindow
        else:
            telemetryWindow = self.networkTelemetryWindows[curNet.getKey()]
        
        # Can also key off of self.telemetryWindow.isVisible()
        telemetryWindow.show()
        telemetryWindow.activateWindow()
        
        # Can do telemetry window updates after release
        self.updateLock.release()
        
        # User could have selected a different network.
        telemetryWindow.updateNetworkData(curNet)            
        
    def hasCrackRunning(self):
        if self.wepCrack.isRunning() or self.wpaPSKCrack.isRunning():
            return True
        else:
            return False
            
    def showClientContextMenu(self, pos):
        curRow = self.clientTable.currentRow()
        
        if curRow == -1:
            return
            
        curClient = self.clientTable.item(curRow, 0).data(Qt.UserRole)
        
        if self.clientTable.item(curRow, 0).data(Qt.UserRole+1):
            # Has a deauth in progress
            self.deauthSingle.setVisible(False)
            self.deauthContinuous.setVisible(False)
            self.deauthStop.setVisible(True)
        else:
            # No deauth in progress, but check that we know the channel
            if (curClient.channel > 0):
                self.deauthSingle.setVisible(True)
                self.deauthContinuous.setVisible(True)
                self.deauthStop.setVisible(False)
            else:
                self.deauthSingle.setVisible(False)
                self.deauthContinuous.setVisible(False)
                self.deauthStop.setVisible(False)
        
        # Check if net is WEP for WEP menu item:
        isWEP = self.netIsWEP(curClient.apMacAddr)
        
        if isWEP:
            if self.wepCrackRunning:
                self.clientStopWEP.setVisible(True)
                self.clientCaptureWEP.setVisible(False)
            else:
                self.clientStopWEP.setVisible(False)
                self.clientCaptureWEP.setVisible(True)
        else:
            self.clientCaptureWEP.setVisible(False)
            self.clientStopWEP.setVisible(False)
            
        if self.hasCrackRunning():
            # disable any "start" choices
                self.clientCaptureWEP.setVisible(False)
            
        # if  not ('associated' in curClient.apMacAddr):
            # Not associated.  Nothing to disassociate
        self.clientRightClickMenu.exec_(self.clientTable.mapToGlobal(pos))
    
    def netIsWEP(self, apMacAddr):
        numRows = self.networkTable.rowCount()
        
        if numRows == -1:
            return False
        
        for curRow in range(0, numRows):
            curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
            
            if curNet.macAddr == apMacAddr:
                if curNet.security == "WEP":
                    return True
                else:
                    return False
            
        return False
        
    # These should be such that we can clone the right-click onto the main window too (deauth may be deauth all)
    def onCloneAP(self):
        pass
        
    def resetTableColors(self):
        if self.usingBlackoutColors:
            backColor = Qt.black
        else:
            backColor = Qt.white
        if self.networkTable.rowCount() > 0:
            for curRow in range(0, self.networkTable.rowCount()):
                for curCol in range(0, self.networkTable.columnCount()):
                    self.networkTable.item(curRow,curCol).setBackground(backColor)
                    
        if self.clientTable.rowCount() > 0:
            for curRow in range(0, self.clientTable.rowCount()):
                for curCol in range(0, self.clientTable.columnCount()):
                    self.clientTable.item(curRow,curCol).setBackground(backColor)
        
    def onClientCaptureWEPKeys(self):
        # Need to know if it's a wep or wpa AP
        if self.scanRunning():
                QMessageBox.question(self, 'Error',"Please stop the running scan first (deauth won't work while trying to hop channels)", QMessageBox.Ok)
                return
                
        curRow = self.clientTable.currentRow()
        
        if curRow == -1:
            return
        
        curInterface=""
        if (self.comboMonInterfaces.count() > 0):
            curInterface = str(self.comboMonInterfaces.currentText())
        else:
            if (self.comboInterfaces.count() > 0):
                curInterface = str(self.comboInterfaces.currentText())
                
        if len(curInterface) == 0:
            QMessageBox.question(self, 'Error',"No interface available to use.", QMessageBox.Ok)
            return
            
        curClient = self.clientTable.item(curRow, 0).data(Qt.UserRole)
        
        if (curClient.channel < 1):
                QMessageBox.question(self, 'Error',"Association doesn't have an identified channel yet.", QMessageBox.Ok)
                return
            
        if (len(curClient.ssid) == 0) or (curClient.ssid.startswith('<Unknown')):
                QMessageBox.question(self, 'Error',"No SSID identified yet.", QMessageBox.Ok)
                return
            
        self.wepCrack.cleanupTempFiles()
        
        self.wepCrack.wepApMacAddr = curClient.apMacAddr
        self.wepCrack.SSID = curClient.ssid
        self.wepSSID = curClient.ssid
        retVal, errMsg = self.wepCrack.startCrack(curInterface, curClient.channel, curClient.ssid, curClient.apMacAddr, True)
        
        if not retVal:
            QMessageBox.question(self, 'Error',errMsg, QMessageBox.Ok)
            return
            
        self.wepCrackRunning = True
        self.wepUsingClient = True
        
        if not self.clientTable.item(curRow, 0).data(Qt.UserRole+1):
            # Color yellow if we're not also deauthing, when it'll be red
            if self.usingBlackoutColors:
                backColor = Qt.darkCyan
            else:
                backColor = Qt.yellow
            for curCol in range(0, self.clientTable.columnCount()):
                self.clientTable.item(curRow,curCol).setBackground(backColor)
        
        if len(curClient.ssid) > 0:
            self.statBar.setText('Monitoring for WEP IVs for network ' + curClient.ssid + '...')
        else:
            self.statBar.setText('Monitoring for WEP IVs for access point ' + curClient.macAddr + '...')
        
        self.wepUsingClient = True
        # Mark +2 as indicator of WEP
        self.clientTable.item(curRow, 0).setData(Qt.UserRole+2, True)
        self.wepTimer.start()
                
    def onNetCaptureWEPKeys(self):
        # Need to know if it's a wep or wpa AP
        if self.scanRunning():
                QMessageBox.question(self, 'Error',"Please stop the running scan first (deauth won't work while trying to hop channels)", QMessageBox.Ok)
                return
        
        if self.wepCrackRunning:
            QMessageBox.question(self, 'Error',"Please stop the running WEP capture first", QMessageBox.Ok)
            return
                
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
        
        curInterface=""
        if (self.comboMonInterfaces.count() > 0):
            curInterface = str(self.comboMonInterfaces.currentText())
        else:
            if (self.comboInterfaces.count() > 0):
                curInterface = str(self.comboInterfaces.currentText())
                
        if len(curInterface) == 0:
            QMessageBox.question(self, 'Error',"No interface available to use.", QMessageBox.Ok)
            return
            
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
        
        if (curNet.channel < 1):
                QMessageBox.question(self, 'Error',"Association doesn't have an identified channel yet.", QMessageBox.Ok)
                return
        
        if (len(curNet.ssid) == 0) or (curNet.ssid.startswith('<Unknown')):
                QMessageBox.question(self, 'Error',"No SSID identified yet.", QMessageBox.Ok)
                return

        if self.usingRemoteAgent:
            retVal, errMsg = execRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, 'wep', curInterface, curNet.channel, curNet.ssid, curNet.macAddr)
        else:
            self.wepCrack.cleanupTempFiles()
            
            self.wepCrack.wepApMacAddr = curNet.macAddr
            self.wepCrack.SSID = curNet.ssid
            self.wepSSID = curNet.ssid
            retVal, errMsg = self.wepCrack.startCrack(curInterface, curNet.channel, curNet.ssid, curNet.macAddr, False)
        
        if not retVal:
            QMessageBox.question(self, 'Error',errMsg, QMessageBox.Ok)
            return
            
        self.wepCrackRunning = True
        
        if not self.networkTable.item(curRow, 0).data(Qt.UserRole+1):
            # Color yellow if we're not also deauthing, when it'll be red
            if self.usingBlackoutColors:
                backColor = Qt.darkCyan
            else:
                backColor = Qt.yellow
            for curCol in range(0, self.networkTable.columnCount()):
                self.networkTable.item(curRow,curCol).setBackground(backColor)
        
        if len(curNet.ssid) > 0:
            self.statBar.setText('Monitoring for WEP IVs for network ' + curNet.ssid + '...')
        else:
            self.statBar.setText('Monitoring for WEP IVs for access point ' + curNet.macAddr + '...')
        
        self.wepUsingClient = False
        # Mark +2 as indicator of WEP
        self.networkTable.item(curRow, 0).setData(Qt.UserRole+2, True)
        
        self.wepTimer.start()
        
    def onWEPTimer(self):
        if not self.wepCrackRunning:
            return
            
        displayedError = False
        
        if self.usingRemoteAgent:
            curInterface = self.getMonitoringInterface()
            retVal,errMsg,isRunning,ivcount, passwords=getRemoteWEPCrackStatus(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            
            if retVal != 0:
                displayedError = True
                self.statBar.setText('ERROR: ' + errMsg)
                
            if passwords is None:
                passwords=[]
        else:
            if not self.wepCrack.isRunning():
                return
            
            # See this overview: https://www.aircrack-ng.org/doku.php?id=simple_wep_crack
            passwords = self.wepCrack.getCrackedPasswords()
            ivcount = self.wepCrack.getIVCount()
        
        if len(passwords) > 0:
            # Stop any running crack
            if self.usingRemoteAgent:
                # I'll have curInterface for remote agent here from above...
                stopRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            else:
                self.wepCrack.stopCrack()
            
            self.wepCrackRunning = False
            
            # Update the UI
            self.updateLock.acquire()
            
            if self.wepUsingClient:
                table = self.clientTable
            else:
                table = self.networkTable

            numRows = table.rowCount()
            
            for curRow in range(0, numRows):
                isWEP = table.item(curRow, 0).data(Qt.UserRole+2)
                if isWEP:
                    # Only recolor if we're not also deauthing
                    if self.usingBlackoutColors:
                        backColor = Qt.black
                    else:
                        backColor = Qt.white
                        
                    for curCol in range(0, table.columnCount()):
                        table.item(curRow,curCol).setBackground(backColor)
                        
                    break
                    table.item(curRow, 0).setData(Qt.UserRole+2, False)
                
            self.updateLock.release()

            # Display the results to the user
            wepKeyString = ""
            if len(passwords) > 0:
                for curPass in passwords:
                    if len(wepKeyString) > 0:
                        wepKeyString += " " + curPass
                    else:
                        wepKeyString = curPass
                        
            QMessageBox.question(self, 'Success',"WEP passwords for " + self.wepSSID + " have been captured: " + wepKeyString, QMessageBox.Ok)
            self.statBar.setText('Ready')

            # If we're local, clean up.
            if not self.usingRemoteAgent:
                self.wepCrack.cleanupTempFiles()
        else:
            if not displayedError:
                self.statBar.setText('Monitoring and cracking WEP for ' + self.wepSSID + '.... have ' + str(ivcount) + ' IVs so far (may take about 80,000)')
                
            self.wepTimer.start(self.wepTimerTimeout)

    def onWPATimer(self):
        if not self.wpaCrackRunning:
            return
            
        fileName = ""
        hasHandshake = False
        
        if self.usingRemoteAgent:
            curInterface = self.getMonitoringInterface()
            retVal,errMsg,isRunning,hasHandshake,fileName=getRemoteWPAPSKCrackStatus(self.remoteAgentIP, self.remoteAgentPort, curInterface)

            if retVal != 0:
                self.statBar.setText('ERROR: ' + errMsg)
                
        else:
            if not self.wpaPSKCrack.isRunning():
                return

            hasHandshake =  self.wpaPSKCrack.hasHandshake()
            
        if hasHandshake:
            # We found a hash that we'll need to crack
            if not self.usingRemoteAgent:
                self.wpaPSKCrack.stopCrack()
                
            self.wpaCrackRunning = False
            
            # Update GUI
            self.updateLock.acquire()
            numRows = self.networkTable.rowCount()
            
            for curRow in range(0, numRows):
                curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
                if (curNet.getKey().startswith(self.wpaPSKCrack.apMacAddr)) and (self.networkTable.item(curRow, 0).data(Qt.UserRole+1) is None):
                    # Only recolor if we're not also deauthing
                    if self.usingBlackoutColors:
                        backColor = Qt.black
                    else:
                        backColor = Qt.white
                        
                    for curCol in range(0, self.networkTable.columnCount()):
                        self.networkTable.item(curRow,curCol).setBackground(backColor)
                        
                    break
                
            self.updateLock.release()

            self.statBar.setText('Ready')
            
            # Now work with the user to save the capture for cracking
            if not self.usingRemoteAgent:
                # Get the save location for the cap file from the user
                QMessageBox.question(self, 'Success',"A key has been captured for cracking for " + self.wpaSSID + ".  Please select a location to save the capture to.", QMessageBox.Ok)
                fileName = self.saveFileDialog("Capture Files (*.cap);;All Files (*)")

                # If they don't provide one just clean up the temporary files and exit
                if not fileName:
                    tmpDir = '/tmp'
                    try:
                        for f in os.listdir(tmpDir):
                            if f.startswith('falconwpacap'):
                                try:
                                    os.remove(tmpDir + '/' + f)
                                except:
                                    pass
                    except:
                        pass
                        
                    return

                # If we have a good filename, we need to move it before we clean up
                os.system('mv /tmp/falconwpacap-01.cap ' + fileName)

                # now clean up temp files
                self.wpaPSKCrack.cleanupTempFiles()
                
                displayMsg = "Use john's wpapcap2john tool to extract the hash, then john --format=wpapsk-opencl or wpapsk to try to crack the key.\n\nFor example with aircrack: aircrack-ng -a2 -b <ap mac address> -e <ssid> -w <dictionary> " + fileName
                QMessageBox.question(self, 'Message',displayMsg, QMessageBox.Ok)
            else:
                # Remote agent version
                displayMsg = "A key has been captured for cracking for " + self.wpaSSID + " and saved to " + fileName + '\n'
                displayMsg += "Use john's wpapcap2john tool to extract the hash, then john --format=wpapsk-opencl or wpapsk to try to crack the key.\n\nFor example with aircrack: aircrack-ng -a2 -b <ap mac address> -e <ssid> -w <dictionary> " + fileName
                QMessageBox.question(self, 'Success',displayMsg, QMessageBox.Ok)
        else:
            # still cracking.  Go another timer cycle
            self.wpaTimer.start(self.wpaTimerTimeout)

    def onNetStopWEP(self):
        if self.wepCrackRunning:
            if self.usingRemoteAgent:
                curInterface = self.getMonitoringInterface()
                if len(curInterface) > 0:
                    stopRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            else:
                self.wepCrack.stopCrack()
                
        self.wepCrackRunning = False
        
        self.statBar.setText('Ready')
        
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
            
        if not self.networkTable.item(curRow, 0).data(Qt.UserRole+1):
            # Color yellow if we're not also deauthing, when it'll be red
            if self.usingBlackoutColors:
                backColor = Qt.black
            else:
                backColor = Qt.white
                
            for curCol in range(0, self.networkTable.columnCount()):
                self.networkTable.item(curRow,curCol).setBackground(backColor)
            
    def onClientStopWEP(self):
        if self.wepCrackRunning:
            if self.usingRemoteAgent:
                curInterface = self.getMonitoringInterface()
                if len(curInterface) > 0:
                    stopRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            else:
                self.wepCrack.stopCrack()
                
        self.wepCrackRunning = False
        
        self.statBar.setText('Ready')
        
        curRow = self.clientTable.currentRow()
        
        if curRow == -1:
            return
            
        if not self.clientTable.item(curRow, 0).data(Qt.UserRole+1):
            if self.usingBlackoutColors:
                backColor = Qt.black
            else:
                backColor = Qt.white
            for curCol in range(0, self.clientTable.columnCount()):
                self.clientTable.item(curRow,curCol).setBackground(backColor)

    def getMonitoringInterface(self):
        if (self.comboMonInterfaces.count() > 0):
            curInterface = str(self.comboMonInterfaces.currentText())
        else:
            curInterface = ""
        
        return curInterface
        
    def onNetStopWPA(self):
        if self.wpaCrackRunning:
            if self.usingRemoteAgent:
                curInterface = self.getMonitoringInterface()
                if len(curInterface) > 0:
                    stopRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            else:
                self.wpaPSKCrack.stopCrack()
                
            self.wpaCrackRunning = False
            
        self.statBar.setText('Ready')
            
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
            
        if not self.networkTable.item(curRow, 0).data(Qt.UserRole+1):
            if self.usingBlackoutColors:
                backColor = Qt.black
            else:
                backColor = Qt.white
            for curCol in range(0, self.networkTable.columnCount()):
                self.networkTable.item(curRow,curCol).setBackground(backColor)
                
    def onNetCaptureWPAKeys(self):
        if self.scanRunning():
                QMessageBox.question(self, 'Error',"Please stop the running scan first (deauth won't work while trying to hop channels)", QMessageBox.Ok)
                return
        
        if self.wpaCrackRunning:
                QMessageBox.question(self, 'Error',"Please stop the running WPA capture first", QMessageBox.Ok)
                return
                
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
        
        curInterface=""
        if (self.comboMonInterfaces.count() > 0):
            curInterface = str(self.comboMonInterfaces.currentText())
        else:
            if (self.comboInterfaces.count() > 0):
                curInterface = str(self.comboInterfaces.currentText())
                
        if len(curInterface) == 0:
            QMessageBox.question(self, 'Error',"No interface available to use.", QMessageBox.Ok)
            return
            
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
        
        if (curNet.channel < 1):
                QMessageBox.question(self, 'Error',"Association doesn't have an identified channel yet.", QMessageBox.Ok)
                return
            
        if (len(curNet.ssid) == 0) or (curNet.ssid.startswith('<Unknown')):
                QMessageBox.question(self, 'Error',"No SSID identified yet.", QMessageBox.Ok)
                return
                
        # Okay now we can scan.
        # Start a capture with the provided network info
        # Monitor the cap file
        # Notify the user when we're done
        # OR: User cancels.
        
        self.wpaSSID = curNet.ssid
        
        if self.usingRemoteAgent:
            retVal, errMsg = execRemoteCrack(self.remoteAgentIP, self.remoteAgentPort, 'wpapsk', curInterface, curNet.channel, curNet.ssid, curNet.macAddr)
        else:
            self.wpaPSKCrack.cleanupTempFiles()
            
            retVal, errMsg = self.wpaPSKCrack.startCrack(curInterface, curNet.channel, curNet.ssid, curNet.macAddr)
        
        if not retVal:
            QMessageBox.question(self, 'Error',errMsg, QMessageBox.Ok)
            return
            
        self.wpaCrackRunning = True
        
        if not self.networkTable.item(curRow, 0).data(Qt.UserRole+1):
            # Color if we're not also deauthing, when it'll be red
            if self.usingBlackoutColors:
                backColor = Qt.darkCyan
            else:
                backColor = Qt.yellow
            for curCol in range(0, self.networkTable.columnCount()):
                self.networkTable.item(curRow,curCol).setBackground(backColor)
        
        if len(curNet.ssid) > 0:
            self.statBar.setText('Monitoring for WPA key hash for network ' + curNet.ssid + '...')
        else:
            self.statBar.setText('Monitoring for WPA key hash for access point ' + curNet.macAddr + '...')
        
        self.wpaTimer.start()
    
    def onDeauthStop(self):
        curRow = self.clientTable.currentRow()
        
        if curRow == -1:
            return
        
        curDeauth = self.clientTable.item(curRow, 0).data(Qt.UserRole+1)
        
        if curDeauth:
            if self.usingRemoteAgent:
                retVal, errmsg = stopRemoteDeauth(self.remoteAgentIP, self.remoteAgentPort, curDeauth)
                
                if retVal != 0:
                    QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
            else:
                curDeauth.kill()
                
            self.clientTable.item(curRow, 0).setData(Qt.UserRole+1, None)

        if self.usingBlackoutColors:
            backColor = Qt.black
        else:
            backColor = Qt.white
        for curCol in range(0, self.clientTable.columnCount()):
            self.clientTable.item(curRow,curCol).setBackground(backColor)
                    
    def deauth(self, continuous):
        if self.scanRunning():
            QMessageBox.question(self, 'Error',"Please stop the running scan first (deauth won't work while trying to hop channels)", QMessageBox.Ok)
            return

        curRow = self.clientTable.currentRow()
        
        if curRow == -1:
            return

        if not self.usingRemoteAgent:
            reply = QMessageBox.question(self, 'Warning',"Deauths don't always work.  Would you like debug output printed to the console to watch progress?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if reply == QMessageBox.No:
                printdebug=False
            else:
                printdebug=True
        else:
            printdebug = False
            
        curInterface=""
        if (self.comboMonInterfaces.count() > 0):
            curInterface = str(self.comboMonInterfaces.currentText())
        else:
            if (self.comboInterfaces.count() > 0):
                curInterface = str(self.comboInterfaces.currentText())
                
        if len(curInterface) == 0:
            QMessageBox.question(self, 'Error',"No interface available to use.", QMessageBox.Ok)
            return
            
        curClient = self.clientTable.item(curRow, 0).data(Qt.UserRole)
        
        if curClient == None:
            return
            
        curDeauth = self.clientTable.item(curRow, 0).data(Qt.UserRole+1)
        
        # Shouldn't be here if (not curDeauth), but just a safety check to not fire it off twice
        if not curDeauth:
            runningDeauthChannel = self.runningDeauthChannel()
            
            if (runningDeauthChannel > 0) and (runningDeauthChannel != curClient.channel):
                QMessageBox.question(self, 'Error',"An active deauth is running on another channel. Stop the other deauth first.", QMessageBox.Ok)
                return
                
            if self.usingRemoteAgent:
                newDeauth = FalconDeauth()
                
                newDeauth.channel = curClient.channel
                newDeauth.stationMacAddr = curClient.macAddr
                newDeauth.apMacAddr = curClient.apMacAddr
                newDeauth.interface = curInterface

                retVal, errmsg = execRemoteDeauth(self.remoteAgentIP, self.remoteAgentPort, newDeauth, continuous)
                
                if retVal != 0:
                    QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                    newDeauth = None
            else:
                newDeauth = FalconWirelessEngine.deauthClient(curClient, curInterface, curClient.channel, continuous, printdebug)
                
            if newDeauth and continuous:
                # For single-shot deauths, this will return none since the process will die anyway,
                self.clientTable.item(curRow, 0).setData(Qt.UserRole+1, newDeauth)
                # Color row
                for curCol in range(0, self.clientTable.columnCount()):
                    self.clientTable.item(curRow,curCol).setBackground(Qt.red)
                
    def onDeauthClientSingle(self):
        self.deauth(False)
        
    def onDeauthClientContinuous(self):
        self.deauth(True)
        
    # -------------  Network broadcast deauths ----------------
    def onDeauthStopNet(self):
        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return
        
        curDeauth = self.networkTable.item(curRow, 0).data(Qt.UserRole+1)
        
        if curDeauth:
            if self.usingRemoteAgent:
                retVal, errmsg = stopRemoteDeauth(self.remoteAgentIP, self.remoteAgentPort, curDeauth)
                
                if retVal != 0:
                    QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
            else:
                curDeauth.kill()
                
            self.networkTable.item(curRow, 0).setData(Qt.UserRole+1, None)

        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
        
        if self.wpaCrackRunning and self.wpaSSID == curNet.ssid:
            if self.usingBlackoutColors:
                backColor = Qt.darkCyan
            else:
                backColor = Qt.yellow
        else:
            if self.usingBlackoutColors:
                backColor = Qt.black
            else:
                backColor = Qt.white
                
        for curCol in range(0, self.networkTable.columnCount()):
            self.networkTable.item(curRow,curCol).setBackground(backColor)
                    
    def deauthNet(self, continuous):
        if self.scanRunning():
            QMessageBox.question(self, 'Error',"Please stop the running scan first (deauth won't work while trying to hop channels)", QMessageBox.Ok)
            return

        curRow = self.networkTable.currentRow()
        
        if curRow == -1:
            return

        if not self.usingRemoteAgent:
            reply = QMessageBox.question(self, 'Warning',"Not all clients will respond to a broadcast deauth (if it doesn't work try a directed client deauth).  Would you like debug output printed to the console to watch progress?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if reply == QMessageBox.No:
                printdebug=False
            else:
                printdebug=True
        else:
            printdebug = False
            
        curInterface=""
        if (self.comboMonInterfaces.count() > 0):
            curInterface = str(self.comboMonInterfaces.currentText())
        else:
            if (self.comboInterfaces.count() > 0):
                curInterface = str(self.comboInterfaces.currentText())
                
        if len(curInterface) == 0:
            QMessageBox.question(self, 'Error',"No interface available to use.", QMessageBox.Ok)
            return
            
        curNet = self.networkTable.item(curRow, 2).data(Qt.UserRole)
        
        if curNet == None:
            return
            
        curDeauth = self.networkTable.item(curRow, 0).data(Qt.UserRole+1)
        
        # Shouldn't be here if (not curDeauth), but just a safety check to not fire it off twice
        if not curDeauth:
            runningDeauthChannel = self.runningDeauthChannel()
            
            if (runningDeauthChannel > 0) and (runningDeauthChannel != curNet.channel):
                QMessageBox.question(self, 'Error',"An active deauth is running on another channel. Stop the other deauth first.", QMessageBox.Ok)
                return
                
            if self.usingRemoteAgent:
                newDeauth = FalconDeauth()
                
                newDeauth.channel = curNet.channel
                newDeauth.stationMacAddr = ""
                newDeauth.apMacAddr = curNet.macAddr
                newDeauth.interface = curInterface

                retVal, errmsg = execRemoteDeauth(self.remoteAgentIP, self.remoteAgentPort, newDeauth, continuous)
                
                if retVal != 0:
                    QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                    newDeauth = None
            else:
                newDeauth = FalconWirelessEngine.deauthClient(curNet, curInterface, curNet.channel, continuous, printdebug)
            
            # if newDeauth and continuous:
            if newDeauth:
                if continuous:
                    # For single-shot deauths, this will return none since the process will die anyway,
                    self.networkTable.item(curRow, 0).setData(Qt.UserRole+1, newDeauth)
                    # Color row
                    for curCol in range(0, self.networkTable.columnCount()):
                        self.networkTable.item(curRow,curCol).setBackground(Qt.red)
                else:
                    self.networkTable.item(curRow, 0).setData(Qt.UserRole+1, None)
                
    def onDeauthSingleNet(self):
        self.deauthNet(False)
        
    def onDeauthContinuousNet(self):
        self.deauthNet(True)
        
    def scanRunning(self):
        return 'Stop' in self.btnScan.text()

    def onUpdateHiddenSSIDs(self):
        if not self.mainWin:
            return
            
        netlist = {}
                    
        try:
            # Technically if a scan's running networktable could update, which may throw
            # an exception.
            rowPosition = self.networkTable.rowCount()
            
            if rowPosition > 0:
                # Range goes to last # - 1
                for curRow in range(0, rowPosition):
                    try:
                        curData = self.networkTable.item(curRow, 2).data(Qt.UserRole)
                    except:
                        curData = None
                        
                    if curData and (not curData.ssid.startswith('<Unknown')):
                        netlist[curData.getKey()] = curData
        except:
            pass
            
        if len(netlist) > 0:
            self.mainWin.advScanUpdateSSIDs.emit(netlist)

    def stopScan(self, agentDisconnected=False):
        self.updateTimer.stop()
        
        self.comboInterfaces.setEnabled(True)
        self.comboMonInterfaces.setEnabled(True)
        self.btnMonMode.setEnabled(True)
        self.btnStopMonMode.setEnabled(True)
        
        curInterface = self.getMonitoringInterface()
        if self.usingRemoteAgent:
            if not agentDisconnected:
                retVal, errmsg = stopRemoteScan(self.remoteAgentIP, self.remoteAgentPort, curInterface)
                
                if retVal != 0:
                    QMessageBox.question(self, 'Error',"An error occurred stopping remote scan: " + errmsg, QMessageBox.Ok)
        else:
            FalconWirelessEngine.airodumpStop(curInterface)

        self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
        self.btnScan.setText('&Scan')
        self.btnMonMode.setEnabled(True)
        self.btnStopMonMode.setEnabled(True)
            
        # Need to reset the shortcut after changing the text
        self.btnScan.setShortcut('Ctrl+S')
        
        self.statBar.setText('Scan stopped')

    def onMonInterfaceChanged(self):
        if not self.loadingComboBoxes:
            self.checkScanAlreadyRunning()
        
    def checkScanAlreadyRunning(self):
        curInterface = self.getMonitoringInterface()
        
        if self.usingRemoteAgent:
            retVal, errmsg = remoteScanRunning(self.remoteAgentIP, self.remoteAgentPort, curInterface)
            if retVal == 0:
                isRunning = True
            else:
                isRunning = False
        else:
            isRunning = FalconWirelessEngine.isAirodumpRunning(curInterface)
        
        if isRunning:
            self.comboMonInterfaces.setEnabled(False)
            self.comboInterfaces.setEnabled(False)
            self.updateTimer.start(self.updateTimerTimeout)

            self.btnScan.setStyleSheet("background-color: rgba(255,0,0,255); border: none;")
            self.btnScan.setText('&Stop scanning')

            self.btnMonMode.setEnabled(False)
            self.btnStopMonMode.setEnabled(False)
        else:
            self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
            self.btnScan.setText('&Scan')
            self.btnMonMode.setEnabled(True)
            self.btnStopMonMode.setEnabled(True)
            
        # Need to reset the shortcut after changing the text
        self.btnScan.setShortcut('Ctrl+S')
            
    def onScanClicked(self, pressed):
        # self.scanRunning returns self.btnScan.isChecked().  When pressed it'll come in as checked which will
        # be the button state.  Therefore, not self.scanRunning() means the button wants to come out of pressed mode.
        
        scanrunning = self.scanRunning()
        
        if scanrunning:
            # Want to stop a running scan (self.scanRunning represents the NEW pressed state)
            self.stopScan()
        else:
            # Want to start a new scan
            if self.deauthRunning():
                QMessageBox.question(self, 'Error',"A deauth is running.  Please stop all deauths first.", QMessageBox.Ok)
                self.btnScan.setChecked(False)
                return
                
            if self.wpaCrackRunning:
                QMessageBox.question(self, 'Error',"Please stop the WPA capture first.", QMessageBox.Ok)
                self.btnScan.setChecked(False)
                return
                
            if self.wepCrackRunning:
                QMessageBox.question(self, 'Error',"Please stop the WEP crack first.", QMessageBox.Ok)
                self.btnScan.setChecked(False)
                return
                
            if (self.comboMonInterfaces.count() > 0):
                # Get the selected interface
                curInterface = str(self.comboMonInterfaces.currentText())

                if self.usingRemoteAgent:
                    retVal, errmsg = startRemoteScan(self.remoteAgentIP, self.remoteAgentPort, curInterface)
                    if retVal != 0:
                        QMessageBox.question(self, 'Error',"An error occurred starting the remote scan: " + errmsg, QMessageBox.Ok)
                        self.btnScan.setChecked(False)
                        return
                else:
                    # Make sure there's no other airodump running on this interface first
                    FalconWirelessEngine.airodumpStop(curInterface)
                    # Note: start cleans up any pre-existing temp files before starting.
                    # Now start a new one
                    FalconWirelessEngine.airodumpStart(curInterface)
                
                # Configure UI updates
                self.comboMonInterfaces.setEnabled(False)
                self.comboInterfaces.setEnabled(False)
                self.updateTimer.start(self.updateTimerTimeout)
                self.statBar.setText('Scan started')
            else:
                self.statBar.setText('Error: No monitoring interfaces')
                scanrunning = True # Let it transition false afterwards

        if not scanrunning:
            # Scanning was turned on.  Turn red to indicate click would stop
            self.btnScan.setStyleSheet("background-color: rgba(255,0,0,255); border: none;")
            self.btnScan.setText('&Stop scanning')

            self.comboMonInterfaces.setEnabled(False)
            self.comboInterfaces.setEnabled(False)
            self.btnMonMode.setEnabled(False)
            self.btnStopMonMode.setEnabled(False)
            
            #if self.btnUpdate:
            #    self.btnUpdate.setEnabled(False)
        else:
            self.btnScan.setStyleSheet("background-color: rgba(2,128,192,255); border: none;")
            self.btnScan.setText('&Scan')
            #if self.btnUpdate:
            #    self.btnUpdate.setEnabled(True)
            
            self.comboMonInterfaces.setEnabled(True)
            self.comboInterfaces.setEnabled(True)
            self.btnMonMode.setEnabled(True)
            self.btnStopMonMode.setEnabled(True)
            
        # Need to reset the shortcut after changing the text
        self.btnScan.setShortcut('Ctrl+S')
        
    def onNetTableHeadingClicked(self, logical_index):
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
        
    def onClientTableHeadingClicked(self, logical_index):
        header = self.clientTable.horizontalHeader()
        order = Qt.DescendingOrder
        # order = Qt.DescendingOrder
        if not header.isSortIndicatorShown():
            header.setSortIndicatorShown( True )
        elif header.sortIndicatorSection()==logical_index:
            # apparently, the sort order on the header is already switched
            # when the section was clicked, so there is no need to reverse it
            order = header.sortIndicatorOrder()
        header.setSortIndicator( logical_index, order )
        self.clientTableSortOrder = order
        self.clientTableSortIndex = logical_index
        self.clientTable.sortItems(logical_index, order )
        
    def onStopMonClicked(self):
        try:
            interface = self.comboMonInterfaces.currentText()
        except:
            interface = ""
        
        if len(interface) == 0:
            return
            
        if self.usingRemoteAgent:
            retVal, errmsg = stopRemoteMonitoringInterface(self.remoteAgentIP, self.remoteAgentPort, interface)
        else:
            retVal = FalconWirelessEngine.airmonStop(interface)
            errmsg = "Error code " + str(retVal) + " stopping " + interface +  " monitor mode.  You can try it manually from a command-line with 'airmon-ng stop " + interface + "'"

        if (retVal != 0):
                QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                return
                    
        self.fillComboBoxes()
        
        self.mainWin.rescanInterfaces.emit()
            
    def onCreateMonClicked(self):
        try:
            interface = self.comboInterfaces.currentText()
        except:
            interface = ""
        
        if len(interface) == 0:
            return

        reply = QMessageBox.question(self, 'Warning','Creating the monitoring interface will disconnect any wireless networks connected to ' + interface + '.  Are you sure you want to create it?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.No:
            return
            
        if self.usingRemoteAgent:
            retVal, errmsg = startRemoteMonitoringInterface(self.remoteAgentIP, self.remoteAgentPort, interface)
        else:
            retVal = FalconWirelessEngine.airmonStart(interface)
            errmsg = "Error code " + str(retVal) + " switching " + interface +  " to monitoring mode.  You can try it manually from a command-line with 'airmon-ng start " + interface + "'"

        if (retVal != 0):
                QMessageBox.question(self, 'Error',errmsg, QMessageBox.Ok)
                self.statBar.setText(errmsg)
                return
            
        self.statBar.setText('')
        
        self.fillComboBoxes()
        
        self.mainWin.rescanInterfaces.emit()
            
    def oldControls(self):
        # Plot strongest or last
        self.lblConsole = QLabel("Scan Output", self)
        self.lblConsole.setGeometry(30, 84, 100, 30)
        
        self.console = QTextEdit(self)
        self.console.setGeometry(30, 150, 600, 600)
        pal = QPalette()
        bgc = QColor(0, 0, 0)
        pal.setColor(QPalette.Base, bgc)
        textc = QColor(255, 255, 255)
        pal.setColor(QPalette.Text, textc)
        self.console.setPalette(pal)
        font = QFont()
        font.setFamily("Courier New")
        self.console.setFont(font)
        
    def onUpdateTimer(self):
        if self.usingRemoteAgent:
            retVal, errmsg, networks, clients = requestAdvancedRemoteNetworks(self.remoteAgentIP, self.remoteAgentPort)
        else:
            networks, clients = FalconWirelessEngine.parseAiroDumpCSV(self.airodumpcsvfile)
        
        if (networks is not None) and (len(networks) > 0 or len(clients) > 0):
            # parse will only return None on an error.  otherwise it'll return an empty dictionary {}
            self.populateTables(networks, clients)
            
        if self.scanRunning():
            self.updateTimer.start(self.updateTimerTimeout)

    def populateTables(self, networks, clients):
        self.updateLock.acquire()
        
        gpsData = None
        
        if self.mainWin:
            # Check for GPS Data
            if self.mainWin.gpsSynchronized and (self.mainWin.gpsEngine.lastCoord is not None) and (self.mainWin.gpsEngine.lastCoord.isValid):
                gpsData = SparrowGPS()
                gpsData.copy(self.mainWin.gpsEngine.lastCoord)
            
        if gpsData is not None:
            for curKey in networks.keys():
                curNet = networks[curKey]
                curNet.gps.copy(gpsData)
                curNet.strongestgps.copy(gpsData)
            
            for curKey in clients.keys():
                curClient = clients[curKey]
                curClient.gps.copy(gpsData)
                curClient.strongestgps.copy(gpsData)
                
        self.populateNetworks(networks)
        self.populateClients(networks, clients)

        # Check if we have a telemetry window
        for curKey in self.networkTelemetryWindows.keys():
            if curKey in networks.keys():
                curNet = networks[curKey]
                telemetryWindow = self.networkTelemetryWindows[curNet.getKey()]
                telemetryWindow.updateNetworkData(curNet)
                
        for curKey in self.clientTelemetryWindows.keys():
            if curKey in clients.keys():
                curClient = clients[curKey]
                telemetryWindow = self.clientTelemetryWindows[curClient.getKey()]
                telemetryWindow.updateNetworkData(curClient)
                                
        if self.mainWin:
            self.mainWin.scanresultsfromadvanced.emit(networks)
            
        self.updateLock.release()

    def ouiLookup(self, macAddr):
        clientVendor = ""
        
        try:
            if self.ouiLookupEngine:
                clientVendor = self.ouiLookupEngine.get_manuf(macAddr)
        except:
            pass
            
        return clientVendor
        
    def getAPChannelAndSSIDFromMac(self, networks, apMacAddr):
        if 'associated' in apMacAddr:
            # Not associated.  Return channel 0 and don't bother looping
            return 0, ""
            
        for curKey in networks.keys():
            curNet = networks[curKey]
            if curNet.macAddr == apMacAddr:
                return curNet.channel, curNet.ssid
                
        return 0, ""
        
    def populateClients(self, networks, clients):
        if clients is None or len(clients) == 0:
            return
        
        rowPosition = self.clientTable.rowCount()
        
        # ['Station Mac', 'Associated AP','Assoc SSID','Channel','Signal Strength','Last Seen', 'First Seen', 'Probed SSIDs']
        if rowPosition > 0:
            # Range goes to last # - 1
            for curRow in range(0, rowPosition):
                try:
                    curData = self.clientTable.item(curRow, 0).data(Qt.UserRole)
                except:
                    curData = None
                    
                if (curData):
                    # We already have the network.  just update it
                    for curKey in clients.keys():
                        curClient = clients[curKey]
                        if curData.macAddr == curClient.macAddr:
                            # Update the existing one
                            clientVendor = self.ouiLookup(curClient.macAddr)
                            self.clientTable.item(curRow, 1).setText(clientVendor)
                            self.clientTable.item(curRow, 2).setText(curClient.apMacAddr)
                            
                            curClient.channel, curClient.ssid = self.getAPChannelAndSSIDFromMac(networks, curClient.apMacAddr)
                            self.clientTable.item(curRow, 3).setText(curClient.ssid)
                            self.clientTable.item(curRow, 4).setText(str(curClient.channel))
                            
                            self.clientTable.item(curRow, 5).setText(str(curClient.signal))
                            self.clientTable.item(curRow, 6).setText(curClient.lastSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            
                            # Carry forward firstSeen
                            curClient.firstSeen = curData.firstSeen # This is one field to carry forward
                            
                            self.clientTable.item(curRow, 7).setText(curClient.firstSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            
                            if clients[curKey].gps.isValid:
                                self.clientTable.item(curRow, 8).setText('Yes')
                            else:
                                self.clientTable.item(curRow, 8).setText('No')
                                
                            ssidList = ""
                            for curSSID in curClient.probedSSIDs:
                                if len(ssidList) == 0:
                                    ssidList = curSSID
                                else:
                                    ssidList += " " + curSSID
                            self.clientTable.item(curRow, 9).setText(ssidList)
                            
                            # Check strongest signal
                            if curData.strongestsignal > curClient.signal or (curData.strongestsignal > (curClient.signal*0.9) and curData.gps.isValid and (not curClient.strongestgps.isValid)):
                                curClient.strongestsignal = curData.signal
                                curClient.strongestgps.latitude = curData.gps.latitude
                                curClient.strongestgps.longitude = curData.gps.longitude
                                curClient.strongestgps.altitude = curData.gps.altitude
                                curClient.strongestgps.speed = curData.gps.speed
                                curClient.strongestgps.isValid = curData.gps.isValid
                                
                            curClient.foundInList = True
                            self.clientTable.item(curRow, 0).setData(Qt.UserRole, curClient)
        
        addedClients = 0
        
        # Now let's add whatever we didn't find:
        for curKey in clients.keys():
            curClient = clients[curKey]
        
            if (not curClient.foundInList):
                rowPosition = self.clientTable.rowCount()
                if rowPosition < 0:
                    addedFirstRow = True
                    rowPosition = 0
                else:
                    addedFirstRow = False
                    
                addedClients += 1
                self.clientTable.insertRow(rowPosition)
                
                if (addedFirstRow):
                    self.clientTable.setRowCount(1)

                curClient.channel, curClient.ssid = self.getAPChannelAndSSIDFromMac(networks, curClient.apMacAddr)
                newMac = QTableWidgetItem(curClient.macAddr)
                # You can bind more than one data.  See this: 
                # https://stackoverflow.com/questions/2579579/qt-how-to-associate-data-with-qtablewidgetitem
                newMac.setData(Qt.UserRole, curClient)
                newMac.setData(Qt.UserRole+2, False) # Set WEP flag to false

                self.clientTable.setItem(rowPosition, 0, newMac)
                
                # ['Station Mac','vendor', 'Associated AP','Signal Strength','Last Seen', 'First Seen', 'Probed SSIDs']
                clientVendor = self.ouiLookup(curClient.macAddr)
                self.clientTable.setItem(rowPosition, 1, QTableWidgetItem(clientVendor))
                self.clientTable.setItem(rowPosition, 2, QTableWidgetItem(curClient.apMacAddr))
                self.clientTable.setItem(rowPosition, 3, QTableWidgetItem(curClient.ssid))
                self.clientTable.setItem(rowPosition, 4,  IntTableWidgetItem(str(curClient.channel)))
                self.clientTable.setItem(rowPosition, 5,  IntTableWidgetItem(str(curClient.signal)))
                self.clientTable.setItem(rowPosition, 6, DateTableWidgetItem(curClient.lastSeen.strftime("%m/%d/%Y %H:%M:%S")))
                self.clientTable.setItem(rowPosition, 7, DateTableWidgetItem(curClient.firstSeen.strftime("%m/%d/%Y %H:%M:%S")))

                if curClient.gps.isValid:
                    self.clientTable.setItem(rowPosition, 8, QTableWidgetItem('Yes'))
                else:
                    self.clientTable.setItem(rowPosition, 8, QTableWidgetItem('No'))
        
                ssidList = ""
                for curSSID in curClient.probedSSIDs:
                    if len(ssidList) == 0:
                        ssidList = curSSID
                    else:
                        ssidList += " " + curSSID
                                        
                self.clientTable.setItem(rowPosition, 9, QTableWidgetItem(ssidList))

        if addedClients > 0:
            if self.clientTableSortIndex >=0:
                self.clientTable.sortItems(self.clientTableSortIndex, self.clientTableSortOrder )
                
            
    def populateNetworks(self, networks):
        if networks is None or len(networks) == 0:
            return
            
        # Check if we don't have the current entry.  If so, add it,
        # otherwise update it.

        rowPosition = self.networkTable.rowCount()
        
        if rowPosition > 0:
            # Range goes to last # - 1
            for curRow in range(0, rowPosition):
                try:
                    curData = self.networkTable.item(curRow, 2).data(Qt.UserRole)
                except:
                    curData = None
                    
                if (curData):
                    # We already have the network.  just update it
                    for curKey in networks.keys():
                        curNet = networks[curKey]
                        if curData.macAddr == curNet.macAddr:
                            # Update the existing one
                            clientVendor = self.ouiLookup(curNet.macAddr)
                            self.networkTable.item(curRow, 1).setText(clientVendor)
                            self.networkTable.item(curRow, 2).setText(curNet.ssid)
                            self.networkTable.item(curRow, 3).setText(curNet.security)
                            self.networkTable.item(curRow, 4).setText(curNet.privacy)
                            self.networkTable.item(curRow, 5).setText(str(curNet.getChannelString()))
                            self.networkTable.item(curRow, 6).setText(str(curNet.frequency))
                            self.networkTable.item(curRow, 7).setText(str(curNet.signal))
                            self.networkTable.item(curRow, 8).setText(str(curNet.bandwidth))
                            self.networkTable.item(curRow, 9).setText(curNet.lastSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            
                            # Carry forward firstSeen
                            curNet.firstSeen = curData.firstSeen # This is one field to carry forward
                            
                            # Check strongest signal
                            if curData.strongestsignal > curNet.signal or (curData.strongestsignal > (curNet.signal*0.9) and curData.gps.isValid and (not curNet.strongestgps.isValid)):
                                curNet.strongestsignal = curData.signal
                                curNet.strongestgps.latitude = curData.gps.latitude
                                curNet.strongestgps.longitude = curData.gps.longitude
                                curNet.strongestgps.altitude = curData.gps.altitude
                                curNet.strongestgps.speed = curData.gps.speed
                                curNet.strongestgps.isValid = curData.gps.isValid
                            
                            self.networkTable.item(curRow, 10).setText(curNet.firstSeen.strftime("%m/%d/%Y %H:%M:%S"))
                            if curNet.gps.isValid:
                                self.networkTable.item(curRow, 11).setText('Yes')
                            else:
                                self.networkTable.item(curRow, 11).setText('No')
                            curNet.foundInList = True
                            self.networkTable.item(curRow, 2).setData(Qt.UserRole, curNet)
        
        # Now let's add whatever we didn't find:
        addedNetworks = 0
        
        for curKey in networks.keys():
            curNet = networks[curKey]
        
            if (not curNet.foundInList):
                rowPosition = self.networkTable.rowCount()
                if rowPosition < 0:
                    addedFirstRow = True
                    rowPosition = 0
                else:
                    addedFirstRow = False

                addedNetworks += 1
                
                self.networkTable.insertRow(rowPosition)
                
                if (addedFirstRow):
                    self.networkTable.setRowCount(1)

                self.networkTable.setItem(rowPosition, 0, QTableWidgetItem(networks[curKey].macAddr))
                tmpssid = networks[curKey].ssid
                if (len(tmpssid) == 0):
                    tmpssid = '<Unknown>'
                newSSID = QTableWidgetItem(tmpssid)
                # You can bind more than one data.  See this: 
                # https://stackoverflow.com/questions/2579579/qt-how-to-associate-data-with-qtablewidgetitem
                newSSID.setData(Qt.UserRole, networks[curKey])
                newSSID.setData(Qt.UserRole+2, False) # Set WEP flag to false
                
                # ['macAddr','vendor', 'SSID', 'Security', 'Privacy', 'Channel', 'Signal Strength', 'Last Seen', 'First Seen', 'GPS'])
                clientVendor = self.ouiLookup(curNet.macAddr)
                self.networkTable.setItem(rowPosition, 1,  QTableWidgetItem(clientVendor))
                self.networkTable.setItem(rowPosition, 2, newSSID)
                self.networkTable.setItem(rowPosition, 3, QTableWidgetItem(networks[curKey].security))
                self.networkTable.setItem(rowPosition, 4, QTableWidgetItem(networks[curKey].privacy))
                self.networkTable.setItem(rowPosition, 5, IntTableWidgetItem(str(networks[curKey].getChannelString())))
                self.networkTable.setItem(rowPosition, 6, IntTableWidgetItem(str(networks[curKey].frequency)))
                self.networkTable.setItem(rowPosition, 7,  IntTableWidgetItem(str(networks[curKey].signal)))
                self.networkTable.setItem(rowPosition, 8, IntTableWidgetItem(str(networks[curKey].bandwidth)))
                self.networkTable.setItem(rowPosition, 9, DateTableWidgetItem(networks[curKey].lastSeen.strftime("%m/%d/%Y %H:%M:%S")))
                self.networkTable.setItem(rowPosition, 10, DateTableWidgetItem(networks[curKey].firstSeen.strftime("%m/%d/%Y %H:%M:%S")))
                if networks[curKey].gps.isValid:
                    self.networkTable.setItem(rowPosition, 11, QTableWidgetItem('Yes'))
                else:
                    self.networkTable.setItem(rowPosition, 11, QTableWidgetItem('No'))
              
        if addedNetworks > 0:
            if self.networkTableSortIndex >=0:
                self.networkTable.sortItems(self.networkTableSortIndex, self.networkTableSortOrder )
                
        # Automatically update the main window.  This will just return if mainWin isn't set      
        self.onUpdateHiddenSSIDs()

def testCapture():
    pass
    # captureProc = FalconWirelessEngine.startCapture('wlan0mon', str(44), '/tmp/falconcap', 'D8:EB:97:2F:DD:CE', type="WPA")
    
    # retVal = False
    
    #while not retVal:
    #    retVal = FalconWirelessEngine.testWPACapture('D8:EB:97:2F:DD:CE', '','/tmp/falconcap-01.cap')
    #    if not retVal:
    #        sleep(2)

    #retVal, info = FalconWirelessEngine.crackWPACapture('D8:EB:97:2F:DD:CE', '','/tmp/testpass.txt','/tmp/falconcap-01.cap')
    #print(info)
    #FalconWirelessEngine.stopCapture(captureProc)
    
if __name__ == '__main__':
    # testCapture()
    
    app = QApplication([])
    advancedScanDialog = AdvancedScanDialog()
    advancedScanDialog.exec()

    app.exec_()
