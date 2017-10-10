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
import datetime
import json
import re
import argparse
from http import server as HTTPServer
from wirelessengine import WirelessEngine
from sparrowgps import GPSEngine

gpsEngine = GPSEngine()
curTime = datetime.datetime.now()
if GPSEngine.GPSDRunning():
    gpsEngine.start()
    print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Local gpsd Found.  Providing GPS coordinates when synchronized.")
else:
    print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] No local gpsd running.  No GPS data will be provided.")

# Sample handler: https://wiki.python.org/moin/BaseHttpServer
class SparrowWiFiAgentRequestHandler(HTTPServer.BaseHTTPRequestHandler):
    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_GET(s):
        global gpsEngine
        
        if (s.path != '/wireless/interfaces') and (s.path != '/gps/status') and ('/wireless/networks/' not in s.path):
            s.send_response(404)
            s.send_header("Content-type", "text/html")
            s.end_headers()
            s.wfile.write("<html><body><p>Bad Request</p>".encode("utf-8"))
            s.wfile.write("</body></html>".encode("UTF-8"))
            return
            
        """Respond to a GET request."""
        s.send_response(200)
        #s.send_header("Content-type", "text/html")
        s.send_header("Content-type", "application/jsonrequest")
        s.end_headers()
        # NOTE: In python 3, string is a bit different.  Examples write strings directly for Python2,
        # In python3 you have to convert it to UTF-8 bytes
        # s.wfile.write("<html><head><title>Sparrow-wifi agent</title></head><body>".encode("utf-8"))

        if s.path == '/wireless/interfaces':
            wirelessInterfaces = WirelessEngine.getInterfaces()
            jsondict={}
            jsondict['interfaces']=wirelessInterfaces
            jsonstr = json.dumps(jsondict)
            s.wfile.write(jsonstr.encode("UTF-8"))
        elif s.path == '/gps/status':
            jsondict={}
            jsondict['gpsinstalled'] = str(GPSEngine.GPSDInstalled())
            jsondict['gpsrunning'] = str(GPSEngine.GPSDRunning())
            jsondict['gpssynch'] = str(gpsEngine.gpsValid())
            if gpsEngine.gpsValid():
                gpsPos = {}
                gpsPos['latitude'] = gpsEngine.lastCoord.latitude
                gpsPos['longitude'] = gpsEngine.lastCoord.longitude
                gpsPos['altitude'] = gpsEngine.lastCoord.altitude
                gpsPos['speed'] = gpsEngine.lastCoord.speed
                jsondict['gpspos'] = gpsPos

            jsonstr = json.dumps(jsondict)
            s.wfile.write(jsonstr.encode("UTF-8"))
        elif '/wireless/networks/' in s.path:
            curInterface = s.path.replace('/wireless/networks/', '')
            # Sanitize command-line input here:
            p = re.compile('^([0-9a-zA-Z]+)')
            try:
                fieldValue = p.search(curInterface).group(1)
            except:
                fieldValue = ""

            if len(fieldValue) == 0:
                return
                
            # Get results for the specified interface
            if gpsEngine.gpsValid():
                retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(fieldValue, gpsEngine.lastCoord)
            else:
                retCode, errString, jsonstr=WirelessEngine.getNetworksAsJson(fieldValue, None)
                
            s.wfile.write(jsonstr.encode("UTF-8"))

class SparrowWiFiAgent(object):
    # See https://docs.python.org/3/library/http.server.html
    # For HTTP Server info
    def run(self, port):
        server_address = ('', port)
        httpd = HTTPServer.HTTPServer(server_address, SparrowWiFiAgentRequestHandler)
        
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Starting Sparrow-wifi agent on port " + str(port))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    
        httpd.server_close()
        
        curTime = datetime.datetime.now()
        print('[' +curTime.strftime("%m/%d/%Y %H:%M:%S") + "] Sparrow-wifi agent stopped.")
        
if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='Sparrow-wifi agent')
    argparser.add_argument('--port', help='Port for HTTP server to listen on', default=8020, required=False)
    args = argparser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: You need to have root privileges to run this script.  Please try again, this time using 'sudo'. Exiting.\n")
        exit(2)
    
    server = SparrowWiFiAgent()
    port = args.port
    server.run(port)
