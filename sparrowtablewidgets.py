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

from PyQt5.QtWidgets import QTableWidgetItem
from PyQt5.QtCore import Qt
from dateutil import parser

# ------------------  Table Sorting by Number Class  ------------------------------
class IntTableWidgetItem(QTableWidgetItem):
    def __init__(self, defaultStr):
        super().__init__(defaultStr)

            # See http://doc.qt.io/qt-5/qt.html for alignment
        self.setTextAlignment( Qt.AlignRight + Qt.AlignVCenter )        

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

class FloatTableWidgetItem(QTableWidgetItem):
    def __init__(self, defaultStr):
        super().__init__(defaultStr)
        
        self.setTextAlignment( Qt.AlignRight + Qt.AlignVCenter)        
        
    def __lt__(self, other):
        if ( isinstance(other, QTableWidgetItem) ):
            try:
                my_value = float(self.data(Qt.EditRole))
            except:
                my_value = 0.0
                
            try:
                other_value = float(other.data(Qt.EditRole))
            except:
                other_value = 0.0

            return my_value < other_value

        return super(FloatTableWidgetItem, self).__lt__(other)

# ------------------  Table Sorting by Timestamp Class  ------------------------------
class DateTableWidgetItem(QTableWidgetItem):
    def __init__(self, defaultStr):
        super().__init__(defaultStr)
        
        self.setTextAlignment( Qt.AlignHCenter  + Qt.AlignVCenter)
        
    def __lt__(self, other):
        if ( isinstance(other, QTableWidgetItem) ):
            try:
                my_value = parser.parse(self.data(Qt.EditRole))
            except:
                pass
                
            try:
                other_value = parser.parse(other.data(Qt.EditRole))
            except:
                pass
                
            return my_value < other_value

        return super(DateTableWidgetItem, self).__lt__(other)

