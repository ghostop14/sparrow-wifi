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

import os
import subprocess

class SparrowRPi(object):
    LIGHT_STATE_ON = 255
    LIGHT_STATE_OFF = 0
    LIGHT_STATE_HEARTBEAT = 1
    GREEN_LED = 'led0'
    RED_LED = 'led1'

    sentModprobe = False
    
    def __init__(self):
        super().__init__()

    def disableKernelControl(led):
        ledPath = '/sys/class/leds/' + led + '/trigger'
        
        if os.path.isfile(ledPath):
            try:
                kfile=open(ledPath, 'w')
                kfile.write('none\n')
                kfile.close()
                
                return True
            except:
                return False
        else:
            return False
        
    def hasLights():
        # This can be a basic RPi test, however it's possible other devices may have these LEDs too.
        return os.path.exists('/sys/class/leds/led0')
        
    def greenLED(newState):
        SparrowRPi.LEDState(SparrowRPi.GREEN_LED, newState)
        
    def redLED(newState):
        SparrowRPi.LEDState(SparrowRPi.RED_LED, newState)
        
    def LEDState(led, newState):
        SparrowRPi.disableKernelControl(led)
        
        if newState != SparrowRPi.LIGHT_STATE_HEARTBEAT:
            # LED on or off
            ledPath = '/sys/class/leds/' + led + '/brightness'
            
            if os.path.isfile(ledPath):
                try:
                    kfile=open(ledPath, 'w')
                    kfile.write(str(newState)+'\n')
                    kfile.close()
                    
                    return True
                except:
                    return False
        else:
            # Heartbeat
            
            if not SparrowRPi.sentModprobe:
                subprocess.run(['modprobe', 'ledtrig_heartbeat'] , stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                SparrowRPi.sentModprobe = True
                
            ledPath = '/sys/class/leds/' + led + '/trigger'
            
            if os.path.isfile(ledPath):
                try:
                    kfile=open(ledPath, 'w')
                    kfile.write('heartbeat\n')
                    kfile.close()
                    
                    return True
                except:
                    return False

        return False
