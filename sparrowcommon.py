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
import socket
import platform
import subprocess
import io
import gzip

# ------------------  Global functions ------------------------------
def stringtobool(instr):
    if (instr == 'True' or instr == 'true'):
        return True
    else:
        return False
        
def portOpen(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1) 
    try:
        result = sock.connect_ex((host,port))
        if result == 0:
                return True
        else:
                return False
    except:
        return False
        
def ping(host):
    # Returns True if host responds to a ping request

    # Ping parameters as function of OS
    ping_str = "-n 1" if  platform.system().lower()=="windows" else "-c 1"
    args = "ping " + " " + ping_str + " " + host
    need_sh = False if  platform.system().lower()=="windows" else True

    # Ping
    retVal = False
    
    # Note: Python automatically calls close at the end of the block.
    try:
        with open("/dev/null","a") as f:
            retVal = subprocess.call(args, shell=need_sh, stdout=f) == 0
    except:
        pass

    return retVal
    
def gzipCompress(inputString):
    out = io.BytesIO()
    
    with gzip.GzipFile(fileobj=out, mode='w') as ofile:
        ofile.write(inputString.encode())
        
    compressedBytes = out.getvalue()
    
    return compressedBytes
    
def gzipUncompress(inputBytes):
    inp = io.BytesIO()
    inp.write(inputBytes)
    inp.seek(0)
    
    with gzip.GzipFile(fileobj=inp, mode='rb') as ofile:
        gunzippedString = ofile.read()
        
    return gunzippedString.decode('ASCII')
    
# ------------------  Class Base Thread ----------------------------------
class BaseThreadClass(Thread):
    def __init__(self):
        super().__init__()
        self.signalStop = False
        self.threadRunning = False
        
        # Indicate to the base thread class that we can be stopped by the main thread
        self.daemon = True


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
        
    def waitTillFinished(self, maxWaitTime=2):
        maxIterations = maxWaitTime / 0.1
        i = 0
        while self.threadRunning and i < maxIterations:
            sleep(0.1)
            i += 1

if __name__ == '__main__':
    pass
    if portOpen('127.0.0.1', 80):
        print('Port open.')
    else:
        print('Port closed.')
