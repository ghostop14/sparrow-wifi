#!/usr/bin/python3
#
###################################################################
#
# Application: Sparrow-WiFi
# Module: cellscanner.py
#
##################################################################
#

import datetime
import os
import re
import subprocess
import threading


class CellScanResult(object):
    def __init__(self):
        self.timestamp = datetime.datetime.utcnow()
        self.center_freq_mhz = None
        self.cell_id = None
        self.pss_id = None
        self.rx_power_db = None
        self.residual_freq_offset_hz = None
        self.k_factor = None
        self.duplex_mode = None
        self.try_idx = None
        self.params = {}
        self.gps = None

    def toJsondict(self):
        jsondict = {}
        jsondict['timestamp'] = self.timestamp.isoformat()
        jsondict['centerfreqmhz'] = self.center_freq_mhz
        jsondict['cellid'] = self.cell_id
        jsondict['pssid'] = self.pss_id
        jsondict['rxpowerdb'] = self.rx_power_db
        jsondict['residualfreqoffsethz'] = self.residual_freq_offset_hz
        jsondict['kfactor'] = self.k_factor
        jsondict['duplexmode'] = self.duplex_mode
        jsondict['try'] = self.try_idx
        jsondict['params'] = self.params
        if self.gps is not None:
            jsondict['gps'] = self.gps
        return jsondict


class CellScannerThread(threading.Thread):
    def __init__(self, bin_path, params, result_list, result_lock, gps_func=None, fake_lines=None):
        super().__init__()
        self.bin_path = bin_path
        self.params = params
        self.result_list = result_list
        self.result_lock = result_lock
        self.gps_func = gps_func
        self.fake_lines = fake_lines
        self.process = None
        self.stopEvent = threading.Event()

        self.re_freq = re.compile(r'Examining center frequency ([0-9\\.]+) MHz .*try ([0-9]+)', re.IGNORECASE)
        self.re_detect = re.compile(r'Detected a (FDD|TDD) cell! At freqeuncy ([0-9\\.]+)MHz, try ([0-9]+)', re.IGNORECASE)
        self.re_cellid = re.compile(r'cell ID:\s*([0-9]+)', re.IGNORECASE)
        self.re_pssid = re.compile(r'PSS ID:\s*([0-9]+)', re.IGNORECASE)
        self.re_rxpower = re.compile(r'RX power level:\s*([-0-9\\.]+) dB', re.IGNORECASE)
        self.re_residual = re.compile(r'residual frequency offset:\s*([-0-9\\.]+) Hz', re.IGNORECASE)
        self.re_kfactor = re.compile(r'k_factor:\s*([0-9]+)', re.IGNORECASE)

        self.current_result = None

    def stop(self):
        self.stopEvent.set()
        if self.process is not None:
            try:
                self.process.terminate()
            except:
                pass

    def _finalize_result(self):
        if self.current_result is None:
            return

        if self.gps_func is not None:
            try:
                gpsFix = self.gps_func()
                if gpsFix is not None:
                    self.current_result.gps = gpsFix
            except:
                pass

        self.result_lock.acquire()
        try:
            self.result_list.append(self.current_result.toJsondict())
        finally:
            self.result_lock.release()
        self.current_result = None

    def _handle_line(self, line):
        freqMatch = self.re_freq.search(line)
        if freqMatch:
            return

        detectMatch = self.re_detect.search(line)
        if detectMatch:
            self._finalize_result()
            self.current_result = CellScanResult()
            self.current_result.duplex_mode = detectMatch.group(1).upper()
            self.current_result.center_freq_mhz = float(detectMatch.group(2))
            self.current_result.try_idx = int(detectMatch.group(3))
            self.current_result.params = self.params.copy()
            return

        if self.current_result is None:
            return

        cellidMatch = self.re_cellid.search(line)
        if cellidMatch:
            self.current_result.cell_id = int(cellidMatch.group(1))
            return

        pssidMatch = self.re_pssid.search(line)
        if pssidMatch:
            self.current_result.pss_id = int(pssidMatch.group(1))
            return

        rxMatch = self.re_rxpower.search(line)
        if rxMatch:
            try:
                self.current_result.rx_power_db = float(rxMatch.group(1))
            except:
                pass
            return

        residMatch = self.re_residual.search(line)
        if residMatch:
            try:
                self.current_result.residual_freq_offset_hz = float(residMatch.group(1))
            except:
                pass
            return

        kMatch = self.re_kfactor.search(line)
        if kMatch:
            try:
                self.current_result.k_factor = int(kMatch.group(1))
            except:
                pass
            return

    def run(self):
        try:
            if self.fake_lines is not None:
                for curLine in self.fake_lines:
                    if self.stopEvent.is_set():
                        break
                    self._handle_line(curLine.rstrip())
                self._finalize_result()
                return

            cmd = [self.bin_path, '-s', str(self.params.get('freqstart', '')),
                   '-e', str(self.params.get('freqend', '')),
                   '-g', str(self.params.get('gain', ''))]

            if 'numtry' in self.params:
                cmd.extend(['-n', str(self.params.get('numtry'))])

            if 'ppm' in self.params:
                cmd.extend(['-p', str(self.params.get('ppm'))])

            if 'correction' in self.params:
                cmd.extend(['-c', str(self.params.get('correction'))])

            if self.params.get('brief', False):
                cmd.append('-b')

            if self.params.get('verbose', False):
                cmd.append('-v')

            if 'deviceindex' in self.params:
                cmd.extend(['-i', str(self.params.get('deviceindex'))])

            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

            for line in iter(self.process.stdout.readline, ''):
                if self.stopEvent.is_set():
                    break
                self._handle_line(line.rstrip())

            self._finalize_result()
        finally:
            if self.process is not None:
                try:
                    self.process.stdout.close()
                except:
                    pass
                try:
                    if self.process.poll() is None:
                        self.process.terminate()
                except:
                    pass


class CellScannerRemoteAgent(object):
    def __init__(self, gps_func=None):
        super().__init__()
        self.lock = threading.Lock()
        self.results = []
        self.thread = None
        self.gps_func = gps_func
        self.running_params = {}
        # Default to upstream tool name; caller can override binpath.
        self.default_bin_path = '/usr/local/bin/CellSearch'

    def toolsInstalled(self, bin_path=None):
        if not bin_path:
            bin_path = self.default_bin_path
        return os.path.isfile(bin_path)

    def startScan(self, params, fakeOutput=None):
        self.lock.acquire()
        try:
            if self.thread is not None and self.thread.is_alive():
                return 1, "Cell scan already running."

            bin_path = params.get('binpath', self.default_bin_path)
            if not bin_path:
                bin_path = self.default_bin_path
            # Fallbacks when using default path and no override was provided.
            if not self.toolsInstalled(bin_path) and (fakeOutput is None):
                fallback_paths = ['/usr/local/bin/CellSearch_hackrf',
                                  '/usr/src/LTE-Cell-Scanner/CellSearch_hackrf']
                if bin_path == self.default_bin_path:
                    for altPath in fallback_paths:
                        if self.toolsInstalled(altPath):
                            bin_path = altPath
                            break
                if not self.toolsInstalled(bin_path):
                    return 2, "CellSearch binary not found or not executable at: " + str(bin_path)
            if (fakeOutput is None) and (not os.access(bin_path, os.X_OK)):
                return 3, "CellSearch binary exists but is not executable: " + str(bin_path)

            self.results = []
            self.running_params = params.copy()
            self.thread = CellScannerThread(bin_path, params, self.results, self.lock, gps_func=self.gps_func, fake_lines=fakeOutput)
            self.thread.start()
        finally:
            self.lock.release()

        return 0, ""

    def stopScan(self):
        self.lock.acquire()
        try:
            if self.thread is None:
                return 1, "No scan running."
            self.thread.stop()
        finally:
            self.lock.release()
        return 0, ""

    def status(self):
        self.lock.acquire()
        try:
            isRunning = self.thread is not None and self.thread.is_alive()
            count = len(self.results)
            params = self.running_params.copy()
        finally:
            self.lock.release()

        statusdict = {}
        statusdict['running'] = isRunning
        statusdict['resultcount'] = count
        statusdict['params'] = params
        return statusdict

    def getResultsAsJsonDict(self):
        self.lock.acquire()
        try:
            resultsCopy = list(self.results)
        finally:
            self.lock.release()

        response = {}
        response['errcode'] = 0
        response['errmsg'] = ""
        response['results'] = resultsCopy
        response['status'] = self.status()
        return response
