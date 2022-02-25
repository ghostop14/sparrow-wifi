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
import webbrowser

# ------------------  Map Markers  ------------------------------
class MapMarker(object):
    def __init__(self):
        self.gpsValid = False
        self.latitude = 0.0
        self.longitude = 0.0
        self.label = ""
        self.labels = []
        self.barCount = 4
        
    def __str__(self):
        retVal = 'GPS Valid: ' + str(self.gpsValid) + '\n'
        retVal += 'latitude: ' + str(self.latitude) + '\n'
        retVal += 'longitude: ' + str(self.longitude) + '\n'
        retVal += 'Label: ' + self.label + '\n'
        retVal += 'Bar Count: ' + str(self.barCount) + '\n'
        return retVal
        
    def getKey(self):
        return str(self.latitude) + ',' + str(self.longitude)
        
    def addLabel(self, newLabel):
        self.labels.append(newLabel)
        
    def getLabel(self, as_html=False):
        if len(self.labels) == 0:
            return self.label
        else:
            retVal = ""
            for curLabel in self.labels:
                if len(retVal) == 0:
                    if as_html:
                        retVal = curLabel.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;')
                    else:
                        retVal = curLabel
                else:
                    if as_html:
                        retVal += "<br>" + curLabel.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;')
                    else:
                        retVal += "," + curLabel
                        
            return retVal
        
    def atCoordinates(self, latitude, longitude):
        if self.latitude == latitude and self.longitude == longitude:
            return True
        else:
            return False

def GetCenterCoordFromMarkers(markers):
    minlat = None
    maxlat = None
    minlong = None
    maxlong = None
    
    for curMarker in markers:
        if minlat == None:
            minlat = curMarker.latitude
        if maxlat == None:
            maxlat = curMarker.latitude
        if minlong == None:
            minlong = curMarker.longitude
        if maxlong == None:
            maxlong = curMarker.longitude
        
        if (curMarker.latitude < minlat):
            minlat = curMarker.latitude
            
        if (curMarker.latitude > maxlat):
            maxlat = curMarker.latitude
            
        if (curMarker.longitude < minlong):
            minlong = curMarker.longitude
            
        if (curMarker.longitude > maxlong):
            maxlong = curMarker.longitude

    meanLat = minlat + (maxlat - minlat)/2
    meanLong = minlong + (maxlong - minlong)/2
    
    return meanLat, meanLong
        
        
# ------------------  Google Map Generator  ------------------------------
class MapEngineBase(object):
    MAP_TYPE_DEFAULT = 1
    MAP_TYPE_HYBRID = 2
    MAP_TYPE_SATELLITE_ONLY = 3
    MAP_TYPE_TERRAIN = 4
    
    def init(self):
        pass
        
class MapEngineGoogle(MapEngineBase):
    def init(self):
        super().__init__()
        
    def createMap(fileName,title,markers, connectMarkers=False, openWhenDone=True, mapType=MapEngineBase.MAP_TYPE_DEFAULT):
        centerLat, centerLong = GetCenterCoordFromMarkers(markers)
        
        # For satellite and hybrid maps, need to change the font color to white or it won't show up well
        labelColor = 'black'
        
        htmlString = '<html><head><meta name="viewport" content="initial-scale=1.0, user-scalable=no" />\n'
        htmlString += '<meta http-equiv="content-type" content="text/html; charset=UTF-8"/>\n'
        htmlString += '<title>' + title + '</title>\n'
        htmlString += '<script type="text/javascript" src="https://maps.googleapis.com/maps/api/js?libraries=visualization&sensor=true_or_false"></script>\n'
        htmlString += '<script type="text/javascript">\n'
        htmlString += '	function initialize() {\n'
        htmlString +='		var centerlatlng = new google.maps.LatLng('+str(centerLat) + ',' + str(centerLong) + ');\n'
        htmlString +='		var myOptions = {\n'
        htmlString +='			zoom: 16,\n'
        htmlString +='			center: centerlatlng,\n'
        if mapType == MapEngineBase.MAP_TYPE_HYBRID:
            htmlString +='			mapTypeId: google.maps.MapTypeId.HYBRID\n'
            labelColor = 'white'
        elif mapType == MapEngineBase.MAP_TYPE_SATELLITE_ONLY:
            htmlString +='			mapTypeId: google.maps.MapTypeId.SATELLITE_ONLY\n'
            labelColor = 'white'
        elif mapType == MapEngineBase.MAP_TYPE_TERRAIN:
            htmlString +='			mapTypeId: google.maps.MapTypeId.TERRAIN\n'
        else:
            htmlString +='			mapTypeId: google.maps.MapTypeId.ROADMAP\n'
        htmlString +='		};\n'
        
        # Map Canvas
        htmlString +='		var map = new google.maps.Map(document.getElementById("map_canvas"), myOptions);\n\n'
        
        markerPath = os.path.dirname(os.path.abspath(__file__)) + '/images'
        # Reusable markers
        htmlString +='		var iconSize = 24;\n'
        htmlString +='		var midPoint = iconSize / 2;\n'
        htmlString +='		var markerIcon4 = {\n'
        htmlString +="		  url: '" + markerPath + "/4_bars2.png',\n"
        htmlString +='		  scaledSize: new google.maps.Size(iconSize, iconSize),\n'
        htmlString +='		  origin: new google.maps.Point(0, 0),\n'
        htmlString +='		  anchor: new google.maps.Point(midPoint,iconSize-5),\n'
        htmlString +='		  labelOrigin: new google.maps.Point(15,33)\n'
        htmlString +='		};\n'
        htmlString +='		\n'
        htmlString +='		var markerIcon3 = {\n'
        htmlString +="		  url: '" + markerPath + "/3_bars2.png',\n"
        htmlString +='		  scaledSize: new google.maps.Size(iconSize, iconSize),\n'
        htmlString +='		  origin: new google.maps.Point(0, 0),\n'
        htmlString +='		  anchor: new google.maps.Point(midPoint,iconSize-5),\n'
        htmlString +='		  labelOrigin: new google.maps.Point(15,33)\n'
        htmlString +='		};\n'
        htmlString +='		\n'
        htmlString +='		var markerIcon2 = {\n'
        htmlString +="		  url: '" + markerPath + "/2_bars2.png',\n"
        htmlString +='		  scaledSize: new google.maps.Size(iconSize, iconSize),\n'
        htmlString +='		  origin: new google.maps.Point(0, 0),\n'
        htmlString +='		  anchor: new google.maps.Point(midPoint,iconSize-5),\n'
        htmlString +='		  labelOrigin: new google.maps.Point(15,33)\n'
        htmlString +='		};\n'
        htmlString +='		\n'
        htmlString +='		var markerIcon1 = {\n'
        htmlString +="		  url: '" + markerPath + "/1_bar2.png',\n"
        htmlString +='		  scaledSize: new google.maps.Size(iconSize, iconSize),\n'
        htmlString +='		  origin: new google.maps.Point(0, 0),\n'
        htmlString +='		  anchor: new google.maps.Point(midPoint,iconSize-5),\n'
        htmlString +='		  labelOrigin: new google.maps.Point(15,33)\n'
        htmlString +='		};\n'
        
        # create markers
        for curMarker in markers:
            if not curMarker.gpsValid or (curMarker.latitude == 0.0 and curMarker.longitude == 0.0):
                # Skip invalid GPS
                continue
                
            htmlString +='		var latlng = new google.maps.LatLng(' + str(curMarker.latitude) + ', ' + str(curMarker.longitude) + ');\n'
            # htmlString +="		var img = new google.maps.MarkerImage('/usr/local/lib/python3.5/dist-packages/gmplot/markers/32CD32.png');\n"
            htmlString +='		var marker = new google.maps.Marker({\n'
            htmlString +='		title: "' + curMarker.getLabel() + '",\n'
            if (len(curMarker.getLabel()) > 0):
                htmlString +='		label: {\n'
                htmlString +='		        text: "' + curMarker.getLabel() + '",\n'
                htmlString +="		        color: '" + labelColor+ "',\n"
                htmlString +='		},\n'
            # htmlString +='		icon: img,\n'
            bc = curMarker.barCount
            if bc < 1:
                bc = 1
            elif bc > 4:
                bc = 4
            htmlString +='		icon: markerIcon' + str(bc )+ ',\n'
            htmlString +='		position: latlng\n'
            htmlString +='		});\n'
            htmlString +='		marker.setMap(map);\n\n'

        # If we're drawing lines between markers, draw the polyline
        if connectMarkers:
            htmlString +='		var PolylineCoordinates = [\n'
            for curMarker in markers:
                if not curMarker.gpsValid or (curMarker.latitude == 0.0 and curMarker.longitude == 0.0):
                    # Skip invalid GPS
                    continue
                htmlString +='		new google.maps.LatLng(' + str(curMarker.latitude) + ', ' + str(curMarker.longitude) + '),\n'
                
            htmlString +='		];\n'

            htmlString +='		var Path = new google.maps.Polyline({\n'
            htmlString +='		clickable: false,\n'
            htmlString +='		geodesic: true,\n'
            htmlString +='		path: PolylineCoordinates,\n'
            htmlString +='		strokeColor: "#6495ED",\n'
            htmlString +='		strokeOpacity: 1.000000,\n'
            htmlString +='		strokeWeight: 5\n'
            htmlString +='		});\n'
            htmlString +='		Path.setMap(map);\n'

        htmlString +='	}\n'
        htmlString +='</script></head><body style="margin:0px; padding:0px;" onload="initialize()">\n'
        htmlString +='	<div id="map_canvas" style="width: 100%; height: 100%;"></div>\n'
        htmlString +='</body></html>\n'

        # Write the new HTML file
        try:
            outputFile = open(fileName, 'w')
            outputFile.write(htmlString)
            outputFile.close()
            
            if openWhenDone:
                webbrowser.open(fileName)
                
            return True
        except:
            return False

class MapEngineOSM(MapEngineBase):
    def init(self):
        super().__init__()
        
    def createMap(fileName,title,markers, connectMarkers=False, openWhenDone=True, mapType=MapEngineBase.MAP_TYPE_DEFAULT):
        # Get center coordinates
        centerLat, centerLong = GetCenterCoordFromMarkers(markers)
        
        htmlString = ""

        htmlString += '<!DOCTYPE html>\n'
        htmlString += '<html lang="en">\n'
        htmlString += '\n'
        htmlString += '<head>\n'
        htmlString += '	<meta charset="utf-8" />\n'
        htmlString += '	<title>' + title + '</title>\n'
        htmlString += '	<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/openlayers/openlayers.github.io@master/en/v6.12.0/css/ol.css" type="text/css">\n'
        htmlString += '	<script src="https://cdn.jsdelivr.net/gh/openlayers/openlayers.github.io@master/en/v6.12.0/build/ol.js"></script>\n'
        htmlString += '	<!-- These are required for the popup-->\n'
        htmlString += '	<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.js"></script>\n'
        htmlString += '	<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">\n'
        htmlString += '	<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>\n'
        htmlString += '\n'
        htmlString += '\n'
        htmlString += '	<style>\n'
        htmlString += '		/* Map settings */\n'
        htmlString += '		#map {\n'
        htmlString += '			width: 1024px;\n'
        htmlString += '			height: 768px;\n'
        htmlString += '		}\n'
        htmlString += '		.ol-popup {\n'
        htmlString += '		  margin-left: -253px;\n'
        htmlString += '		  min-width: 500px;\n'
        htmlString += '		}\n'
        htmlString += '	</style>\n'
        htmlString += '  </head>\n'
        htmlString += '\n'
        htmlString += '<body onload="init_page();">\n'
        htmlString += '	<script>\n'
        htmlString += '		var defaultstyle = new ol.style.Style({\n'
        htmlString += '			// Circle Fill\n'
        htmlString += '			fill: new ol.style.Fill({\n'
        htmlString += '				color: "rgba(211,211,211, 0.3)"\n'
        htmlString += '			}),\n'
        htmlString += '			stroke: new ol.style.Stroke({\n'
        htmlString += '				width: 2,\n'
        htmlString += '				color: "rgba(105,105,105, 0.8)"\n'
        htmlString += '			}),\n'
        htmlString += '			// Sensor Dot\n'
        htmlString += '			image: new ol.style.Circle({\n'
        htmlString += '				fill: new ol.style.Fill({\n'
        htmlString += '					color: "rgba(0, 0, 255, 0.7)"\n'
        htmlString += '				}),\n'
        htmlString += '				stroke: new ol.style.Stroke({\n'
        htmlString += '					width: 1,\n'
        htmlString += '					color: "rgba(0, 255, 0, 0.8)"\n'
        htmlString += '				}),\n'
        htmlString += '				radius: 6\n'
        htmlString += '			}),\n'
        htmlString += '		});\n'
        htmlString += '\n'
        htmlString += '		var sensor_dot = new ol.style.Style({\n'
        htmlString += '			image: new ol.style.Circle({\n'
        htmlString += '				fill: new ol.style.Fill({\n'
        htmlString += '					color: "rgba(255, 0, 0, 0.8)"\n'
        htmlString += '				}),\n'
        htmlString += '				stroke: new ol.style.Stroke({\n'
        htmlString += '					width: 1,\n'
        htmlString += '					color: "rgba(255, 0, 0, 0.5)"\n'
        htmlString += '				}),\n'
        htmlString += '				radius: 5\n'
        htmlString += '			})\n'
        htmlString += '		});\n'
        htmlString += '\n'
        htmlString += '		function init_page() {\n'
        htmlString += '			tile_server = "";\n'
        htmlString += '			map_center_lat = ' + str(centerLat) + ';\n'
        htmlString += '			map_center_lon = ' + str(centerLong) + ';\n'
        htmlString += '			zoom_factor = 16;\n'
        htmlString += '			var map_server = null;\n'
        htmlString += ''
        htmlString += '			if (tile_server.length == 0) {\n'
        htmlString += '				map_server = new ol.source.OSM();\n'
        htmlString += '			}\n'
        htmlString += '			else {\n'
        htmlString += '				map_server = new ol.source.XYZ({\n'
        htmlString += '					"url": tile_server,\n'
        htmlString += '					attributions: ["Using data from OpenStreetMap, under ODbL"]\n'
        htmlString += '				});\n'
        htmlString += '			}\n'
        htmlString += '\n'
        htmlString += '			// Sensor dots layer\n'
        htmlString += '			pointSource = new ol.source.Vector({\n'
        htmlString += '				projection: "EPSG:4326",\n'
        htmlString += '				features: []\n'
        htmlString += '			});\n'
        htmlString += '\n'
        htmlString += '			pointLayer = new ol.layer.Vector({\n'
        htmlString += '				source: pointSource,\n'
        htmlString += '				style: defaultstyle\n'
        htmlString += '			});\n'
        htmlString += '\n'
        htmlString += '			// Map\n'
        htmlString += '			var map = new ol.Map({\n'
        htmlString += '				target: "map",\n'
        htmlString += '				renderer: "canvas", // Force the renderer to be used\n'
        htmlString += '				layers: [\n'
        htmlString += '					new ol.layer.Tile({\n'
        htmlString += '						source: map_server\n'
        htmlString += '					}),\n'
        htmlString += '					pointLayer\n'
        htmlString += '				],\n'
        htmlString += '				view: new ol.View({\n'
        htmlString += '					center: ol.proj.fromLonLat([map_center_lon, map_center_lat]),\n'
        htmlString += '					zoom: zoom_factor\n'
        htmlString += '				})\n'
        htmlString += '			});\n'
        htmlString += '\n'
        htmlString += '			// Add SSIDs\n'
        htmlString += '			var point;\n'
        htmlString += '			var pointFeature;\n'
        for curMarker in markers:
            if curMarker.latitude != 0.0 or curMarker.longitude != 0.0:
                htmlString += '			point = new ol.geom.Point(ol.proj.transform([' + str(curMarker.longitude) + ', ' + str(curMarker.latitude) + '], "EPSG:4326", "EPSG:3857"));\n'
                htmlString += '			pointFeature = new ol.Feature({ \n'
                htmlString += '				geometry: point, \n'
                htmlString += '				"name": "' + "<b>lat:</b> " + str(curMarker.latitude) + "<br><b>Lon:</b>" + str(curMarker.longitude) + "<br><b>SSIDs:</b><br>" + curMarker.getLabel(as_html=True) + '" \n'
                htmlString += '			});\n'
                htmlString += '\n'
                htmlString += '			pointSource.addFeature(pointFeature);\n'
                htmlString += '\n'
            
        htmlString += '			// Set up popup bubble layer\n'
        htmlString += '			const popup = new ol.Overlay({\n'
        htmlString += '				element: document.getElementById("popup"),\n'
        htmlString += '			});\n'
        htmlString += '\n'
        htmlString += '			map.addOverlay(popup);\n'
        htmlString += '\n'
        htmlString += '			map.on("click", function (evt) {\n'
        htmlString += '				const feature = map.forEachFeatureAtPixel(evt.pixel, function (feature) {\n'
        htmlString += '					return feature;\n'
        htmlString += '				});\n'
        htmlString += '\n'
        htmlString += '				if (feature) {\n'
        htmlString += '					const element = document.getElementById("popup");\n'
        htmlString += '				\n'
        htmlString += '					popup_content = feature.get("name");\n'
        htmlString += '					if (popup_content == null) {\n'
        htmlString += '						$(element).popover("dispose");\n'
        htmlString += '						return;\n'
        htmlString += '					}\n'
        htmlString += '\n'
        htmlString += '					popup.setPosition(evt.coordinate);\n'
        htmlString += '\n'
        htmlString += '					$(element).popover({\n'
        htmlString += '						container: element,\n'
        htmlString += '						placement: "top",\n'
        htmlString += '						html: true,\n'
        htmlString += '						content: popup_content,\n'
        htmlString += '					});\n'
        htmlString += '					$(element).popover("show");\n'
        htmlString += '				} else {\n'
        htmlString += '					const element = document.getElementById("popup");\n'
        htmlString += '					$(element).popover("dispose");\n'
        htmlString += '				}\n'
        htmlString += '			});\n'
        htmlString += '\n'
        htmlString += '		}\n'
        htmlString += '	</script>\n'
        htmlString += '	<!--map placeholder div: -->\n'
        htmlString += '	<div id="map" style="width:1024px; height:768px;"></div>\n'
        htmlString += '	<div id="popup" class="ol-popup"></div>\n'
        htmlString += '</body>\n'
        htmlString += '\n'
        htmlString += '</html>\n'

        # Write the new HTML file
        try:
            outputFile = open(fileName, 'w')
            outputFile.write(htmlString)
            outputFile.close()
            
            if openWhenDone:
                webbrowser.open(fileName)
                
            return True
        except:
            return False
        
# -------  Main Routine For Debugging-------------------------

if __name__ == '__main__':
    fileName = '/tmp/test.html'
    # centerLat = 37.428
    # centerLong = -122.145
    title = 'Google Map'
    
    markers = []
    newMarker = MapMarker()
    newMarker.latitude = 37.424000
    newMarker.longitude = -122.140000
    newMarker.label = '2 Bars'
    newMarker.barCount = 2
    markers.append(newMarker)
    
    newMarker = MapMarker()
    newMarker.latitude = 37.428000
    newMarker.longitude = -122.145000
    newMarker.label = '3 Bars'
    newMarker.barCount = 3
    markers.append(newMarker)
    
    newMarker = MapMarker()
    newMarker.latitude = 37.428000
    newMarker.longitude = -122.138000
    newMarker.label = '4 Bars'
    newMarker.barCount = 4
    markers.append(newMarker)
    
    MapEngineOSM.createMap(fileName,title,markers, connectMarkers=True, openWhenDone=True, mapType=MapEngineBase.MAP_TYPE_HYBRID)
