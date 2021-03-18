#!/usr/bin/python3

import argparse
from elasticsearch import Elasticsearch,  helpers
import json
from time import sleep
import requests
import platform
import datetime
import pytz
from datetime import timezone
import os

from sparrowcommon import stringtobool
from sparrowgps import GPSStatus
from wirelessengine import WirelessEngine,  WirelessNetwork
from sparrowbluetooth import BluetoothDevice

try:
    from manuf import manuf
    hasOUILookup = True
except:
    hasOUILookup = False
    print("WARNING: Can't find import manuf.  Mac address vendors will not be resolved.")

ouiLookupEngine = None

hostname = platform.node()
ecs_agent = {"hostname":hostname, "version":"1.0",  "type":"sparrow"}
ecs_host = { "hostname": hostname,  "host": hostname}

# ------------------  Global functions for agent HTTP requests ------------------------------
def makeGetRequest(url, waitTimeout=6):
    try:
        # Not using a timeout can cause the request to hang indefinitely
        response = requests.get(url, timeout=waitTimeout)
    except:
        return -1, ""
        
    if response.status_code != 200:
        return response.status_code, ""
        
    htmlResponse=response.text
    return response.status_code, htmlResponse

# ------------------  GPS requests ------------------------------
def requestRemoteGPS(remoteIP, remotePort):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/gps/status"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            gpsjson = json.loads(responsestr)
            gpsStatus = GPSStatus()
            
            gpsStatus.gpsInstalled = stringtobool(gpsjson['gpsinstalled'])
            gpsStatus.gpsRunning = stringtobool(gpsjson['gpsrunning'])
            gpsStatus.isValid = stringtobool(gpsjson['gpssynch'])
            
            if gpsStatus.isValid:
                # These won't be there if it's not synchronized
                gpsStatus.latitude = float(gpsjson['gpspos']['latitude'])
                gpsStatus.longitude = float(gpsjson['gpspos']['longitude'])
                gpsStatus.altitude = float(gpsjson['gpspos']['altitude'])
                gpsStatus.speed = float(gpsjson['gpspos']['speed'])
                
            return 0, "", gpsStatus
        except:
            return -2, "Error parsing remote agent response", None
    else:
        return -1, "Error connecting to remote agent", None

# ------------------  WiFi scan requests ------------------------------
def requestRemoteInterfaces(remoteAgentIP, remoteAgentPort):
    url = "http://" + remoteAgentIP + ":" + str(remoteAgentPort) + "/wireless/interfaces"
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

def requestRemoteNetworks(remoteIP, remotePort, remoteInterface, channelList=None):
    url = "http://" + remoteIP + ":" + str(remotePort) + "/wireless/networks/" + remoteInterface
    
    if (channelList is not None) and (len(channelList) > 0):
        url += "?frequencies="
        for curChannel in channelList:
            url += str(curChannel) + ','
            
    if url.endswith(','):
        url = url[:-1]
        
    # Pass a higher timeout since the scan may take a bit
    statusCode, responsestr = makeGetRequest(url, 20)
    
    if statusCode == 200:
        try:
            networkjson = json.loads(responsestr)
            wirelessNetworks = {}
            
            for curNetDict in networkjson['networks']:
                newNet = WirelessNetwork.createFromJsonDict(curNetDict)
                wirelessNetworks[newNet.getKey()] = newNet
                
            return networkjson['errCode'], networkjson['errString'], wirelessNetworks
        except:
            return -2, "Error parsing remote agent response", None
    else:
        return -1, "Error connecting to remote agent", None

# ---- Elastic Functions ---------------------
def create_wifi_index(es, index_name = "sparrowwifi"):
    my_mapping = {
        "mappings": {
            "properties": {
                "@timestamp":                { "type":"date" },
                "agent.hostname":        { "type": "keyword" },
                "agent.version":          { "type": "keyword" },
                "agent.type":                { "type": "keyword" },
                "ecs.version":              { "type": "keyword" },
                "event.dataset":          { "type": "keyword" },
                "event.kind":                { "type": "keyword" },
                "event.type":                { "type": "keyword" },
                "event.module":            { "type": "keyword" },
                "host.hostname":          { "type": "keyword" },
                "host.name":                  { "type": "keyword" },
                "host.geo.location":   { "type": "geo_point" },
                "process.name":             { "type": "keyword" },
                "wifi.ssid":               { "type": "keyword" },
                "wifi.mac_addr":       { "type": "keyword" },
                "wifi.mac_vendor":       { "type": "keyword" },
                "wifi.mode":               { "type": "keyword" },
                "wifi.security":       { "type": "keyword" },
                "wifi.privacy":         { "type": "keyword" },
                "wifi.cipher":           { "type": "keyword" },
                "wifi.channel_key":           { "type": "keyword" },
                "wifi.frequency":  { "type": "double" },
                "wifi.center_frequency_hz":  { "type": "double" },
                "wifi.bandwidth":               { "type": "double" },
                "wifi.geo.location":         { "type": "geo_point" },
                "wifi.strongest_signal.location":        { "type": "geo_point" }
            }
        }
    }
    
    create_index = es.indices.create(index = index_name, body = my_mapping,  ignore=400)
    # mapping_index = es.indices.put_mapping(index = index_name, doc_type = "en", body = my_mapping)
    if 'status' in create_index.keys() and create_index['status'] == 400:
        if create_index['error']['root_cause'][0]['type'] != 'resource_already_exists_exception' and not ('already exists as alias' in create_index['error']['root_cause'][0]['reason']):
            errString = create_index['error']['root_cause'][0]['type'] + ": " + create_index['error']['root_cause'][0]['reason']
            print(errString)
            raise Exception(errString)
        
def create_bluetooth_index(es, index_name = "sparrowbt"):
    my_mapping = {
        "mappings": {
            "properties": {
                "@timestamp":                { "type":"date" },
                "agent.hostname":        { "type": "keyword" },
                "agent.version":          { "type": "keyword" },
                "agent.type":                { "type": "keyword" },
                "ecs.version":              { "type": "keyword" },
                "event.dataset":          { "type": "keyword" },
                "event.kind":                { "type": "keyword" },
                "event.type":                { "type": "keyword" },
                "event.module":            { "type": "keyword" },
                "host.hostname":          { "type": "keyword" },
                "host.name":                  { "type": "keyword" },
                "host.geo.location":   { "type": "geo_point" },
                "process.name":             { "type": "keyword" },
                "bluetooth.uuid":               { "type": "keyword" },
                "bluetooth.company":       { "type": "keyword" },
                "bluetooth.manufacturer":               { "type": "keyword" },
                "bluetooth.type":       { "type": "keyword" },
                "bluetooth.geo.location":         { "type": "geo_point" },
                "bluetooth.strongest_signal.location":        { "type": "geo_point" }
            }
        }
    }
    
    create_index = es.indices.create(index = index_name, body = my_mapping,  ignore=400)
    # mapping_index = es.indices.put_mapping(index = index_name, doc_type = "en", body = my_mapping)
    if 'status' in create_index.keys() and create_index['status'] == 400:
        if create_index['error']['root_cause'][0]['type'] != 'resource_already_exists_exception' and not ('already exists as alias' in create_index['error']['root_cause'][0]['reason']):
            errString = create_index['error']['root_cause'][0]['type'] + ": " + create_index['error']['root_cause'][0]['reason']
            print(errString)
            raise Exception(errString)
        
def writeDataToIndex(es,  es_index, entries, es_doc_type='_doc'):
    es_entries = []
    for doc in entries:
        entry = {"_index": es_index,
                 "_type": es_doc_type, 
                 "_source": doc }

        es_entries.append(entry)    

    try:
        helpers.bulk(es, es_entries, refresh=True, request_timeout=60) 
    except Exception as e:
        # This can happen if the server is restarted or the connection becomes unavailable
        print(str(e))

# ------------------- Bluetooth routines ------------------------------------
def startRemoteBluetoothDiscoveryScan(agentIP, agentPort, ubertooth):
    if ubertooth:
        # Promiscuous
        url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystartp"
    else:
        # Advertisements only
        url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystarta"
        
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'
        
def stopRemoteBluetoothDiscoveryScan(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystop"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'

def getRemoteBluetoothRunningServices(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/running"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            hasBluetooth = responsedict['hasbluetooth']
            hasUbertooth = responsedict['hasubertooth']
            spectrumScanRunning = responsedict['spectrumscanrunning']
            discoveryScanRunning = responsedict['discoveryscanrunning']
            
            return errcode, errmsg, hasBluetooth, hasUbertooth, spectrumScanRunning, discoveryScanRunning
        except:
            return -1, 'Error parsing response', False, False, False, False
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', False, False, False, False
        
def clearRemoteBluetoothDeviceList(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoveryclear"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            return errcode, errmsg
        except:
            return -1, 'Error parsing response'
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']'

def getRemoteBluetoothDiscoveryStatus(agentIP, agentPort):
    url = "http://" + agentIP + ":" + str(agentPort) + "/bluetooth/discoverystatus"
    statusCode, responsestr = makeGetRequest(url)
    
    if statusCode == 200:
        try:
            responsedict = json.loads(responsestr)
            errcode = responsedict['errcode']
            errmsg = responsedict['errmsg']
            tmpDeviceData = responsedict['devices']
            devices = {}
            for curDevice in tmpDeviceData:
                newdevice = BluetoothDevice()
                try:
                    newdevice.fromJsondict(curDevice)
                    devices[newdevice.macAddress] = newdevice
                except:
                    pass
            return errcode, errmsg, devices
        except:
            return -1, 'Error parsing response', None
    else:
            return -2, 'Bad response from agent [' + str(statusCode) + ']', None
        

# ------------------- Object Parsing -----------------------------
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
            if updateflag:
                print("WARNING: Unable to update mac address vendor database.  Continuing with old database.")

            try:
                ouidb = manuf.MacParser(update=False)
            except:
                    print("WARNING: Unable to open the mac address vendor database.  Disabling lookup.")
                    ouidb = None
    else:
        ouidb = None
        
    return ouidb

def get_bluetooth_dict(btDevice):
    dictjson = {}
    dictjson['uuid'] = btDevice.uuid
    
    # Make mac address kibana searchable
    dictjson['address'] = btDevice.macAddress.replace(":", "_")
    if len(btDevice.name) == 0:
        dictjson['name'] = btDevice.macAddress.replace(":", "_")
        dictjson['address_is_name'] = True
    else:
        dictjson['name'] = btDevice.name
        dictjson['address_is_name'] = False
        
    # This is required to search for names with spaces, dashes, or colons since Kibana won't allow them to 
    # be used in wildcard search criteria
    elastic_searchable_name = btDevice.name.replace("-", "^").replace(":", "%").replace(" ", "_")
    dictjson['searchable_name'] = elastic_searchable_name
    if elastic_searchable_name == btDevice.name:
        dictjson['searchable_matches_name'] = True
    else:
        dictjson['searchable_matches_name'] = False
    
    dictjson['company'] = btDevice.company
    dictjson['manufacturer'] = btDevice.manufacturer
    dictjson['bluetooth_description'] = btDevice.bluetoothDescription
    if btDevice.btType == 1:
        dictjson['type'] = "Classic"
    else:
        dictjson['type'] = "BT LE"
        
    dictjson['rssi'] = btDevice.rssi
    dictjson['tx_power'] = btDevice.txPower
    dictjson['tx_power_valid'] = btDevice.txPowerValid
    dictjson['ibeacon_range'] = btDevice.iBeaconRange

    if btDevice.gps.isValid and (btDevice.gps.latitude != 0.0 or btDevice.gps.longitude != 0.0):
        wifi_geo = {}
        wifi_geo_location = {}
        wifi_geo_location ['lat'] = str(btDevice.gps.latitude)
        wifi_geo_location ['lon'] = str(btDevice.gps.longitude)
        wifi_geo['location'] = wifi_geo_location

        wifi_geo['altitude'] = str(btDevice.gps.altitude)
        wifi_geo['speed'] = str(btDevice.gps.speed)

        dictjson['geo'] = wifi_geo
        
    return dictjson
    
def get_wireless_dict(wirelessNetwork):
    wifi_details = {}
    
    # Make mac address kibana searchable
    wifi_details['mac_addr'] = wirelessNetwork.macAddr.replace(":", "_")
    
    if ouiLookupEngine:
        wifi_details['mac_vendor'] = ouiLookupEngine.get_manuf(wirelessNetwork.macAddr)
        
    wifi_details['ssid'] = wirelessNetwork.ssid
    
    # This is required to search for names with spaces, dashes, or colons since Kibana won't allow them to 
    # be used in wildcard search criteria
    elastic_searchable_name = wirelessNetwork.ssid.replace("-", "^").replace(":", "%").replace(" ", "_")
    wifi_details['searchable_ssid'] = elastic_searchable_name
    if elastic_searchable_name == wirelessNetwork.ssid:
        wifi_details['searchable_matches_ssid'] = True
    else:
        wifi_details['searchable_matches_ssid'] = False
        
    wifi_details['mode'] = wirelessNetwork.mode
    wifi_details['security'] = wirelessNetwork.security
    wifi_details['privacy'] = wirelessNetwork.privacy
    wifi_details['cipher'] = wirelessNetwork.cipher
    wifi_details['frequency'] = wirelessNetwork.frequency
    wifi_details['center_frequency_hz'] = float(wirelessNetwork.frequency) * 1e6
    wifi_details['channel'] = wirelessNetwork.channel
    wifi_details['channel_key'] = str(wirelessNetwork.channel)
    wifi_details['secondary_channel'] = wirelessNetwork.secondaryChannel
    wifi_details['secondary_channel_location'] = wirelessNetwork.secondaryChannelLocation
    wifi_details['third_channel'] = wirelessNetwork.thirdChannel
    wifi_details['signal'] = wirelessNetwork.signal
    wifi_details['signal_strength'] = WirelessEngine.getSignalQualityFromDB0To5(wirelessNetwork.signal)
    wifi_details['station_count'] = wirelessNetwork.stationcount
    wifi_details['utilization'] = wirelessNetwork.utilization

    wifi_details['bandwidth'] = wirelessNetwork.bandwidth
    wifi_details['first_seen'] = str(wirelessNetwork.firstSeen)
    wifi_details['last_seen'] = str(wirelessNetwork.lastSeen)

    if wirelessNetwork.gps.isValid and (wirelessNetwork.gps.latitude != 0.0 or wirelessNetwork.gps.longitude != 0.0):
        wifi_geo = {}
        wifi_geo_location = {}
        wifi_geo_location ['lat'] = str(wirelessNetwork.gps.latitude)
        wifi_geo_location ['lon'] = str(wirelessNetwork.gps.longitude)
        wifi_geo['location'] = wifi_geo_location
        
        wifi_geo['altitude'] = str(wirelessNetwork.gps.altitude)
        wifi_geo['speed'] = str(wirelessNetwork.gps.speed)
        
        wifi_details['geo'] = wifi_geo
        

    if wirelessNetwork.strongestgps.isValid:
        wifi_geo = {}
        wifi_geo_location = {}
        wifi_geo_location ['lat'] = str(wirelessNetwork.strongestgps.latitude)
        wifi_geo_location ['lon'] = str(wirelessNetwork.strongestgps.longitude)
        wifi_geo['location'] = wifi_geo_location
        
        wifi_geo['altitude'] = str(wirelessNetwork.strongestgps.altitude)
        wifi_geo['speed'] = str(wirelessNetwork.strongestgps.speed)

        wifi_details['strongest_signal'] = wifi_geo

    return wifi_details
    
def addWirelessData(wirelessArray,  wirelessNetwork,  timestamp,  hour_utc,  day_of_week_utc,  hour_local,  day_of_week_local):
    ecs_event = {"kind":"event",  "module": "sparrow",  "type":"info",  "dataset": "sparrow.wifi"}
    
    ecs = {}
    ecs["@timestamp"] = timestamp
    ecs['tags'] = ['sparrow', 'wifi']
    ecs['ecs'] = { 'version': '1.5' }
    ecs['message'] = wirelessNetwork.ssid + ' access point detected'
    ecs['process'] = {"name": "sparrow-wifi"}
    ecs['host'] = ecs_host
    ecs['agent'] = ecs_agent
    ecs['event'] = ecs_event
    ecs['wifi'] = get_wireless_dict(wirelessNetwork)
    ecs['wifi']['is_primary_channel'] = True
    ecs['wifi']['hour_utc'] = hour_utc
    ecs['wifi']['day_of_week_utc'] = day_of_week_utc
    ecs['wifi']['hour_local'] = hour_local
    ecs['wifi']['day_of_week_local'] = day_of_week_local
    
    if 'geo' in ecs['wifi']:
        ecs['host']['geo'] = ecs['wifi']['geo']
        
    wirelessArray.append(ecs)
    
    # If the wireless network has secondary/third channels, lets add them
    # so that in ES we can actually show SSIDs for every channel they're on.
    if wirelessNetwork.secondaryChannel > 0:
        wirelessNetwork.frequency = WirelessEngine.getFrequencyForChannel(wirelessNetwork.secondaryChannel)
        wirelessNetwork.channel= wirelessNetwork.secondaryChannel
        ecs['wifi'] = get_wireless_dict(wirelessNetwork)
        ecs['wifi']['is_primary_channel'] = False
        ecs['wifi']['hour_utc'] = hour_utc
        ecs['wifi']['day_of_week_utc'] = day_of_week_utc
        ecs['wifi']['hour_local'] = hour_local
        ecs['wifi']['day_of_week_local'] = day_of_week_local
        wirelessArray.append(ecs)

    if wirelessNetwork.thirdChannel > 0:
        wirelessNetwork.frequency = WirelessEngine.getFrequencyForChannel(wirelessNetwork.thirdChannel)
        wirelessNetwork.channel= wirelessNetwork.thirdChannel
        ecs['wifi'] = get_wireless_dict(wirelessNetwork)
        ecs['wifi']['is_primary_channel'] = False
        ecs['wifi']['hour_utc'] = hour_utc
        ecs['wifi']['day_of_week_utc'] = day_of_week_utc
        ecs['wifi']['hour_local'] = hour_local
        ecs['wifi']['day_of_week_local'] = day_of_week_local
        wirelessArray.append(ecs)

    return wirelessArray

def addBluetoothData(bluetoothArray,  btDevice,  timestamp,  hour_utc,  day_of_week_utc,  hour_local,  day_of_week_local):
    ecs_event = {"kind":"event",  "module": "sparrow",  "type":"info",  "dataset": "sparrow.bluetooth"}
    
    ecs = {}
    ecs["@timestamp"] = timestamp
    ecs['tags'] = ['sparrow', 'bluetooth']
    ecs['ecs'] = { 'version': '1.5' }
    if len(btDevice.name) > 0:
        ecs['message'] = btDevice.name + ' (' + btDevice.macAddress + ') bluetooth device detected'
    else:
        ecs['message'] = btDevice.macAddress + ' bluetooth device detected'
        
    ecs['process'] = {"name": "sparrow-wifi"}
    ecs['host'] = ecs_host
    ecs['agent'] = ecs_agent
    ecs['event'] = ecs_event
    ecs['bluetooth'] = get_bluetooth_dict(btDevice)
    ecs['bluetooth']['hour_utc'] = hour_utc
    ecs['bluetooth']['day_of_week_utc'] = day_of_week_utc
    ecs['bluetooth']['hour_local'] = hour_local
    ecs['bluetooth']['day_of_week_local'] = day_of_week_local
    
    if 'geo' in ecs['bluetooth']:
        ecs['host']['geo'] = ecs['bluetooth']['geo']
        
    bluetoothArray.append(ecs)
    
    return bluetoothArray

def getDayOfWeekName(day_of_week_num):
    # Note: The returned number differs from C++.  Here 0=Monday
    # With tm_wday, 0=Sunday
    if day_of_week_num == 0:
        day_of_week = "Monday"
    elif day_of_week_num == 1:
        day_of_week = "Tuesday"
    elif day_of_week_num == 2:
        day_of_week = "Wednesday"
    elif day_of_week_num == 3:
        day_of_week = "Thursday"
    elif day_of_week_num == 4:
        day_of_week = "Friday"
    elif day_of_week_num == 5:
        day_of_week = "Saturday"
    elif day_of_week_num == 6:
        day_of_week = "Sunday"
    else:
        day_of_week = ""
        
    return day_of_week
    
# ----------------- Main -----------------------------
if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='Sparrow-wifi Agent/ElasticSearch Bridge')
    argparser.add_argument('--elasticserver', help="ElasticSearch server URL.  Ex: https://user:secret@mysererver.somedomain.com:9200", default='', required=True)
    argparser.add_argument('--wifiindex', help="ElasticSearch index to write wifi networks to", default='', required=True)
    argparser.add_argument('--scandelay', help='How frequently to rescan for networks.  Default is every 15 seconds.', default=15, required=False)
    argparser.add_argument('--sparrowagent', help="Sparrow agent IP", default='127.0.0.1', required=False)
    argparser.add_argument('--wifiinterface', help="Specific IP interface on agent to use.  Default: Query and use the first one.", default='', required=False)
    argparser.add_argument('--sparrowport', help='Port Sparrow agent server listens on', default=8020, required=False)
    argparser.add_argument('--btindex', help="ElasticSearch index to write bluetooth results to.  Setting this enabled Bluetooth scanning.", default='', required=False)
    args = argparser.parse_args()

    if len(args.btindex) > 0:
        bluetoothEnabled = True
        bluetoothIndex = args.btindex
    else:
        bluetoothEnabled = False
        
    # For now no Ubertooth options.
    ubertooth = False
    
    ouiLookupEngine = getOUIDB()
    
    if ouiLookupEngine is None:
        print("WARNING: Can't find vendor lookup database.  Mac address vendors will not be resolved.")
        
    server = args.elasticserver
    wifi_index = args.wifiindex
    remoteAgentIP = args.sparrowagent
    remoteAgentPort = args.sparrowport
    scanDelay = args.scandelay
    
    try:
        es = Elasticsearch([server])
    except Exception as e:
        print(str(e))
        exit(2)

    # Create indices if needed
    create_wifi_index(es, wifi_index)
    
    if bluetoothEnabled:
        create_bluetooth_index(es, bluetoothIndex)
    
    # Get remote wireless interfaces
    if len(args.wifiinterface) > 0:
        remoteInterface = args.wifiinterface
    else:
        statusCode, retList = requestRemoteInterfaces(remoteAgentIP, remoteAgentPort)
        if retList is None:
            print("ERROR: Unable to retrieve any wireless interfaces on the agent.")
            exit(2)
            
        remoteInterface = retList[0]
    
    print("Running Sparrow/Elastic bridge using remote wireless interface " + remoteInterface + "...")

    if bluetoothEnabled:
       errcode, errmsg =  startRemoteBluetoothDiscoveryScan(remoteAgentIP,  remoteAgentPort, ubertooth)
       if errcode != 0:
           print("Bluetooth ERROR: " + errmsg)
           exit(3)
        
    try:
        while True:
            retCode, errString, wirelessNetworks = requestRemoteNetworks(remoteAgentIP, remoteAgentPort, remoteInterface)
            
            # Get timestamp and UTC info
            dt_utc = datetime.datetime.now(pytz.timezone("UTC"))
            timestamp = str(dt_utc)
            timestamp = timestamp.replace("+00:00", "Z")
            timestamp = timestamp.replace(" ", "T")
            hour_utc = dt_utc.hour
            day_of_week_utc = getDayOfWeekName(dt_utc.weekday())
            # Get local info
            dt_local = dt_utc.replace(tzinfo=timezone.utc).astimezone(tz=None)
            hour_local = dt_local.hour
            day_of_week_local = getDayOfWeekName(dt_local.weekday())
                
            if retCode == 0:
                wirelessArray = []
                for curKey in wirelessNetworks.keys():
                    wirelessArray = addWirelessData(wirelessArray,  wirelessNetworks[curKey],  timestamp,  hour_utc,  day_of_week_utc,  hour_local,  day_of_week_local)
                    
                writeDataToIndex(es, wifi_index,  wirelessArray)
            else:
                print("WIFI WARNING: " + errString)
            
            if bluetoothEnabled:
                # Check that everything's running okay.  There have been instances where bluehydra has died on the agent side
                errcode, errmsg, hasBluetooth, hasUbertooth, spectrumScanRunning, discoveryScanRunning =  getRemoteBluetoothRunningServices(remoteAgentIP, remoteAgentPort)      
                
                if discoveryScanRunning:
                    errcode, errmsg, btDevices = getRemoteBluetoothDiscoveryStatus(remoteAgentIP, remoteAgentPort)
                    
                    if (errcode == 0) and (btDevices is not None) and (len(btDevices) > 0):
                        errcode, errmsg = clearRemoteBluetoothDeviceList(remoteAgentIP,  remoteAgentPort)
                        
                        bluetoothArray = []
                        for curKey in btDevices.keys():
                            bluetoothArray = addBluetoothData(bluetoothArray,  btDevices[curKey],  timestamp, hour_utc,  day_of_week_utc,  hour_local,  day_of_week_local)
                            
                        writeDataToIndex(es, bluetoothIndex,  bluetoothArray)
                    else:
                        if errcode != 0:
                            print("Bluetooth WARNING: " + errmsg)
                else:
                    # Let's reset our remote list
                    errcode, errmsg = clearRemoteBluetoothDeviceList(remoteAgentIP,  remoteAgentPort)
                    # Try to restart it.
                    errcode, errmsg =  startRemoteBluetoothDiscoveryScan(remoteAgentIP,  remoteAgentPort, ubertooth)
                    if errcode != 0:
                       print("Bluetooth is down and we got an error attempting to restart it.  ERROR: " + errmsg)
                
            sleep(scanDelay)    
    except KeyboardInterrupt:
        # Can do cleanup here if necessary
        if bluetoothEnabled:
            stopRemoteBluetoothDiscoveryScan(remoteAgentIP,  remoteAgentPort)
            
        print("Exiting.")
    
