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
from threading import Thread
from time import sleep

# ------------------  Class Base Thread ----------------------------------
class BaseThreadClass(Thread):
    def __init__(self):
        super().__init__()
        self.signalStop = False
        self.threadRunning = False

    def run(self):
        self.threadRunning = True
        
        # Just a basic sleep for 1 second example loop.
        # Instantiate and call start() to get it going
        while not self.signalStop:
            sleep(1)
            
        self.threadRunning = False

    def stopAndWait(self):
        self.signalStop = True
        
        self.waitTillFinished()
        
    def waitTillFinished(self):
        while self.threadRunning:
            sleep(0.1)
            
