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

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QApplication, QLabel, QComboBox, QLineEdit, QPushButton, QFileDialog
from PyQt5.QtCore import Qt

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

# -------  Main Routine For Debugging-------------------------

if __name__ == '__main__':
    app = QApplication([])
    # date, time, ok = DB2Dialog.getDateTime()
    dbSettings, ok = DBSettingsDialog.getSettings()
    #print("{} {} {}".format(date, time, ok))
    app.exec_()
