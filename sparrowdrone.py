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

from time import sleep
from dronekit import connect, VehicleMode, LocationGlobalRelative

# Connect to the Vehicle using "connection string" (in this case an address on network)
class SparrowDroneMavlink(object):
    def __init__(self):
        super().__init__()
        self.vehicle = None

    def isConnected(self):
        if self.vehicle:
            return True
        else:
            return False
            
    def connectToSolo(self, wait_ready=True):
        # return self.connect('udpin:0.0.0.0:14550', wait_ready)
        return self.connect('udp:0.0.0.0:14550', wait_ready)
    
    def connectToSimulator(self, wait_ready=True):
        return self.connect('udp:127.0.0.1:14550', False)
    
    def connect(self, connectstring, wait_ready=True):
        try:
        # dronekit.connect(ip, _initialize=True, wait_ready=None, status_printer=<function errprinter>, vehicle_class=None, 
        #                             rate=4, baud=115200, heartbeat_timeout=30, source_system=255, use_native=False)
            self.vehicle = connect(connectstring, wait_ready=wait_ready)
        except:
            self.vehicle = None
            
        if self.vehicle:
            return True
        else:
            return False

    def close(self):
        if (self.vehicle):
            try:
                self.vehicle.close()
            except:
                pass

    def isArmable(self):
        if not self.vehicle:
            return False
            
        return self.vehicle.armed
        
    def arm(self):
        if not self.vehicle.armed and self.vehicle.is_armable:
            self.vehicle.armed = True
            return True
        elif not self.vehicle.armed:
            # Not armed and can't arm it.  Just exit
            return False

    def returnToLaunch(self):
        if self.vehicle:
            self.vehicle.mode = VehicleMode("RTL")
        
    def takeoff(self, mode="STABILIZE", altitude=7):
        # Mode can be GUIDED or STABILIZE
        # altitude is in meters
        if not self.vehicle:
            return False
            
        self.vehicle.mode = VehicleMode(mode)
        
        if not self.vehicle.armed and self.vehicle.is_armable:
            self.vehicle.armed = True
        elif not self.vehicle.armed:
            # Not armed and can't arm it.  Just exit
            return False
        
        while not self.vehicle.armed:
            sleep(1)
            
        self.vehicle.simple_takeoff(altitude)
        
        # self.vehicle.location.global_relative_frame.alt will tell you if it's there yet
        # Say while < 0.95 * altitude
        return True

    def gotoLocation(self, latitude, longitude, altitude, speed):
        if not self.vehicle:
            return False
        
        self.vehicle.mode = VehicleMode("GUIDED")
        self.vehicle.armed = True
        
        while not self.vehicle.armed:
            sleep(1)
            
        self.vehicle.airspeed = speed
        point1 = LocationGlobalRelative(latitude, longitude, altitude)
        self.vehicle.simple_goto(point1)
        
        return True
    
    def relativeAltitude(self):
        # This just returns altitude
        if not self.vehicle:
            return -1
            
        return self.vehicle.location.global_relative_frame.alt
    
    def getRelativePosition(self):
        # This returns a GPSInfo object, as opposed to getLocalGPS() which returns alt,lat,lon
        if not self.vehicle:
            return None
        
        # This will have alt, lat, lon attributes
        return self.vehicle.locatoin.global_relative_frame
        
    def getGlobalGPS(self):
        # See http://python.dronekit.io/guide/vehicle_state_and_parameters.html for basic parameters
        if self.vehicle:
            gpsInfo = self.vehicle.gps_0
        else:
            gpsInfo = None
        # There's a gpsInfo.satellites_available count that could be useful
        
        if gpsInfo is not None:
            # See http://python.dronekit.io/automodule.html
            altitude = self.vehicle.location.global_frame.alt
            latitude = self.vehicle.location.global_frame.lat
            longitude = self.vehicle.location.global_frame.lon
            
            # If it's still in PreArm: Need 3D Fix, is_armable will be false
            # However, I don't know what would happen if GPS fix gets lost.
            # return True, latitude, longitude, altitude
            return self.vehicle.is_armable, latitude, longitude, altitude
        else:
            return False, 0.0, 0.0, 0.0
            
    def getLocalGPS(self):
        # This returns coordinates relative to start location
        if self.vehicle:
            gpsInfo = self.vehicle.gps_0
        else:
            gpsInfo = None
        # There's a gpsInfo.satellites_available count that could be useful
        
        if gpsInfo is not None:
            # See http://python.dronekit.io/automodule.html
            altitude = self.vehicle.location.global_relative_frame.alt
            latitude = self.vehicle.location.global_relative_frame.lat
            longitude = self.vehicle.location.global_relative_frame.lon
            
            return True, latitude, longitude, altitude
        else:
            return False, 0.0, 0.0, 0.0
            
    def land(self):
        if not self.vehicle:
            return
            
        self.vehicle.mode = VehicleMode("LAND")
        
    # See http://python.dronekit.io/guide/vehicle_state_and_parameters.html
    # For basic vehicle attributes
    def getSpeed(self):
        if self.vehicle:
            return self.vehicle.velocity
        else:
            return 0.0
        
    def getHeading(self):
        if self.vehicle:
            return self.vehicle.heading
        else:
            return 0.0
        
    def getGroundSpeed(self):
        if self.vehicle:
            return self.vehicle.groundspeed
        else:
            return 0.0
        
    def getAirSpeed(self):
        if self.vehicle:
            return self.vehicle.airspeed
        else:
            return 0.0
        
    def getSystemStatus(self):
        if self.vehicle:
            return self.vehicle.system_status.state
        else:
            return ""
        
    def getGimbalStatus(self):
        if self.vehicle:
            return self.vehicle.gimbal
        else:
            return ""
        
    def getFirmwareVersion(self):
        if self.vehicle:
            try:
                firmwareversion = self.vehicle.version
            except:
                firmwareversion = ""
                
            return firmwareversion
        else:
            return ""
        
    def getBattery(self):
        if self.vehicle:
            batt = self.vehicle.battery
        else:
            batt = None
        
        if batt:
            return batt.level
        else:
            return 0
            
if __name__ == '__main__':
    drone = SparrowDroneMavlink()
    
    print('\nConnecting to solo at udp:10.1.1.10:14550...')
    drone.connectToSolo()
    
    #print('\nConnecting to local simulator at tcp:127.0.0.1:14550...')
    # drone.connectToSimulator()
    
    if drone.isConnected():
        # print('Firmware Version: ' + str(drone.getFirmwareVersion() ))
        print('System Status: ' + drone.getSystemStatus())
        print('Vehicle Mode: ' + str(drone.vehicle.mode.name))
        print('Gimbal Status: ' + str(drone.getGimbalStatus()))
        
        print('\nGPS:')
        synchronized, latitude, longitude, altitude = drone.getGlobalGPS()
        print('Synchronized: ' + str(synchronized))
        print('Latitude: ' + str(latitude))
        print('Longitude: ' + str(longitude))
        print('Altitude (m): ' + str(altitude))
        print('Heading: ' + str(drone.getHeading()))
        
        print('\nSpeed Readings:')
        print('Velocity (m/s): ' + str(drone.getSpeed()))
        print('Ground Speed (m/s): ' + str(drone.getGroundSpeed()))
        print('Air Speed (m/s): ' + str(drone.getAirSpeed()))
        
        print('\nBattery Level: ' + str(drone.getBattery()) + '%\n')
        
        drone.close()
    else:
        print('Unable to connect.\n')
        
