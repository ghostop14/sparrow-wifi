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
# import sys
import signal
from time import sleep
import re
from threading import Lock
import datetime
from dateutil import parser
import json
from sparrowcommon import BaseThreadClass

# ------------------  Ubertooth Specan scanning Thread ----------------------------------
class HackrfSweepThread(BaseThreadClass):
    def __init__(self, parentHackrf):
        super().__init__()
        self.parentHackrf= parentHackrf
        self.minFreq = 2400
        self.maxFreq = 5900
        self.binWidth = 250000  # 250 KHz width
        
        self.gain = 40
        # mirror qspectrumanalyzer
        # In python3 / is a floating point operation whereas // is explicitly integer division.  Result is without remainder
        self.lna_gain = 8 * (self.gain // 18)
        self.vga_gain = 2 * ((self.gain - self.lna_gain) // 2)
        
    def run(self):
        self.threadRunning = True
        
        subprocess.run(['pkill', '-9','hackrf_sweep'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
        # See this for a good example on threading and reading from a streaming proc
        # https://stackoverflow.com/questions/16768290/understanding-popen-communicate
        freqRange = str(self.minFreq) + ":" + str(self.maxFreq)
        params = ['hackrf_sweep', '-f', freqRange, '-l',str(self.lna_gain),'-g',str(self.vga_gain),'-w', str(self.binWidth)]
        #if '/usr/bin' not in sys.path:
        #    sys.path.append('/usr/bin')
        #if '/usr/local/bin' not in sys.path:
        #    sys.path.append('/usr/local/bin')
            
        #cmd = 'hackrf_sweep -f '+ freqRange + ' -l ' + str(self.lna_gain) + ' -g ' + str(self.vga_gain) + ' -w '+ str(self.binWidth)
        hackrfsweepProc = subprocess.Popen(params,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) # , shell=True)

        # hackrf_sweep output:
        #	strftime(time_str, 50, "%Y-%m-%d, %H:%M:%S", fft_time);
        #	fprintf(fd, "%s.%06ld, %" PRIu64 ", %" PRIu64 ", %.2f, %u",
        #			time_str,
        #			(long int)time_stamp.tv_usec,
        #			(uint64_t)(frequency),
        #			(uint64_t)(frequency+DEFAULT_SAMPLE_RATE_HZ/4),
        #			fft_bin_width,
        #			fftSize);
        #	for(i = 0; (fftSize / 4) > i; i++) {
        #		fprintf(fd, ", %.2f", pwr[i + 1 + (fftSize*5)/8]);
        #	}
        #	fprintf(fd, "\n");
        #	fprintf(fd, "%s.%06ld, %" PRIu64 ", %" PRIu64 ", %.2f, %u",
        #			time_str,
        #			(long int)time_stamp.tv_usec,
        #			(uint64_t)(frequency+(DEFAULT_SAMPLE_RATE_HZ/2)),
        #			(uint64_t)(frequency+((DEFAULT_SAMPLE_RATE_HZ*3)/4)),
        #			fft_bin_width,
        #			fftSize);
        #	for(i = 0; (fftSize / 4) > i; i++) {
        #		fprintf(fd, ", %.2f", pwr[i + 1 + (fftSize/8)]);
        #	}
        #	fprintf(fd, "\n");

        iteration = 0
        
        while not hackrfsweepProc.poll() and not self.signalStop:
            dataline = hackrfsweepProc.stdout.readline().decode('ASCII').replace('\n', '')
            
            dataline = dataline.replace(' ', '')
            data = dataline.split(',')
            
            # First 6 fields are setup: date/time, start/end freq, etc.
            if len(data) > 6:
                try:
                    startfreq = int(data[2])
                    
                    # debug:
                    # print('DEBUG: ' + str(startfreq) + "," + data[3])
                    
                    numSamples = len(data) - 6
                    # self.parentHackrf.spectrumLock.acquire()
                    
                    if numSamples > 0:
                        for i in range(0, numSamples):
                            self.parentHackrf.spectrum[startfreq + i * self.binWidth] = float(data[i+6])
                    
                    # self.parentHackrf.spectrumLock.release()
                except:
                    pass
                    
            # Just give the thread a chance to release resources
            iteration += 1
            if iteration > 50000:
                iteration = 0
                sleep(0.01)
                
        try:
            os.kill(hackrfsweepProc.pid, signal.SIGINT)
            
        except:
            pass
        
        self.threadRunning = False

# ------------------  Sparrow HackRF Class ----------------------------------
class SparrowHackrf(object):
    def __init__(self):
        
        self.spectrum = {}
        self.minFreq = 2400
        self.maxFreq = 2500 # 5900
        self.binWidth = 500000
        self.gain = 40

        self.spectrumLock = Lock()
        # This scan thread is for the spectrum
        self.spectrumScanThread = None

        if SparrowHackrf.getNumHackrfDevices() > 0:
            self.hasHackrf = True
        else:
            self.hasHackrf = False

    def resetSpectrum(self):
        self.spectrum.clear()
        
        # + 1 is for the range loop.  range goes 1 less than numNetries
        numEntries = int((self.maxFreq - self.minFreq) * 1000000 / self.binWidth) + 1
        freqHz = self.minFreq * 1000000
        
        for i in range(0, numEntries):
            self.spectrum[freqHz + i * self.binWidth] = -100.0
            
        
    def getNumHackrfDevices():
        result = subprocess.run(['lsusb', '-d', '1d50:6089'], stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            return 0
            
        hciResult = result.stdout.decode('ASCII')
        p = re.compile('^.*(1d50)', re.MULTILINE)
        tmpInterfaces = p.findall(hciResult)
        
        retVal = 0
        
        if (len(tmpInterfaces) > 0):
            for curInterface in tmpInterfaces:
                retVal += 1

        return retVal

    def startScanning24(self):
        if self.scanRunning():
            self.stopScanning()
            
        self.minFreq = 2400
        self.maxFreq = 2500
        self.binWidth = 500000
        self.gain = 40
        
        self.startScanning(16, 24)
        
    def startScanning5(self):
        self.minFreq = 5170
        # self.maxFreq = 5270
        self.maxFreq = 5840
        #self.binWidth = 2000000
        # Settled on this as the most optimal resolution versus update rate setting.  (5 GHz uses 52 streams across 20 MHz for about 0.38 MHz
        # per stream.  Taking into account spacing and such this captures probably about 4 streams per bucket)
        self.binWidth = 1600000
        self.gain = 48

        # 5 GHz needs more gain
        self.startScanning(32, 16)
        
    def startScanning(self, lna_gain = 32, vga_gain = 16):
        if not self.hasHackrf:
            return
            
        if self.spectrumScanThread:
            self.stopScanning()
            
        self.spectrumScanThread = HackrfSweepThread(self)
        self.spectrumScanThread.minFreq = self.minFreq
        self.spectrumScanThread.maxFreq = self.maxFreq
        self.spectrumScanThread.binWidth = self.binWidth  # 250 KHz width
        self.spectrumScanThread.gain = self.gain
        # mirror qspectrumanalyzer
        self.spectrumScanThread.lna_gain = lna_gain
        self.spectrumScanThread.vga_gain = vga_gain

        self.spectrum.clear()
        # self.resetSpectrum()
        
        self.spectrumScanThread.start()
        
    def scanRunning(self):
        if self.spectrumScanThread and self.spectrumScanThread.threadRunning:
            return True
        else:
            return False
            
    def scanRunning24(self):
        if self.scanRunning():
            if self.minFreq == 2400:
                return True
            else:
                return False
        else:
            return False
            
    def scanRunning5(self):
        if self.scanRunning():
            if self.minFreq == 5170:
                return True
            else:
                return False
        else:
            return False
            
    def stopScanning(self):
        if self.spectrumScanThread:
            self.spectrumScanThread.stopAndWait()
            self.spectrumScanThread = None

        subprocess.run(['pkill', '-9','hackrf_sweep'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        
    def spectrum24ToChannels(self):
        retVal = {}

        # if len(self.spectrum) < 14001:
        #    return retVal
            
        # self.spectrumLock.acquire()
        try:
            for curKey in self.spectrum.keys():
                # curKey is frequency
                if curKey >= 2400000000 and curKey < 3000000000:
                    curFreq = float(curKey)/1000000.0
                    channel = SparrowHackrf.fFreqTo24Channel(curFreq)
                    power = self.spectrum[curKey] - 25.0
                    if power <= -100.0:
                        power = -100.0
                    retVal[channel] = power
        except:
            pass
            
        # self.spectrumLock.release()
        
        return retVal
        
        
    def spectrum5ToChannels(self):
        retVal = {}
        
        # self.spectrumLock.acquire()
        try:
            for curKey in self.spectrum.keys():
                # curKey is frequency
                if curKey >= 5180000000 and curKey <= 5835000000:
                    curFreq = float(curKey)/1000000.0
                    channel = SparrowHackrf.fFreqTo5Channel(curFreq)
                    power = self.spectrum[curKey]
                    # 5 Ghz with the gain the noise floor is a bit higher.
                    power = self.spectrum[curKey] - 30.0
                    if power <= -100.0:
                        power = -100.0
                    retVal[channel] = power
        except:
            pass
            
        # self.spectrumLock.release()
        return retVal
        
    def fFreqTo24Channel(frequency):
        # Note: This function returns a float for partial channels

        # ch1 center freq is 2412.  +- 1 channel is 5 MHz
        # 2402 = Ch -1
        
        
        # Map bluetooth frequency to 2.4 GHz wifi channel
        # ch 1 starts at 2401 MHz and ch 14 tops out at 2495
        if frequency < 2402:
            return float(-1.0)
        elif  frequency > 2494:
            return float(16.0)
            
        channel = -1.0 + (float(frequency) - 2402.0)/5.0
        return channel
        
        # Frequency range of 2.4 GHz channels 1 (low end 2402) to 14 (high end 2494)
        #frange = 2494.0 - 2402.0
        # The top end of 14 is 2494 but that would map to 16 on the chart
        #crange = 16.0
        #channel = float((float(frequency) - 2402.0) / frange * crange)
        
        #return channel
        
    def fFreqTo5Channel(frequency):
        # Note: This function returns a float for partial channels

        # ch 36 lower = 5180 MHz
        # ch 144 high = 5730
        # Gap
        # ch 149 low = 5735
        # ch 165 high = 5835
        
        if frequency < 5180:
            return float(36.0)
        elif  frequency > 5835:
            return float(166.0)
            
        channel = 35.0 + (float(frequency) - 5180.0)/5.0
        return channel
        
        # Frequency range of 2.4 GHz channels 1 (low end 2402) to 14 (high end 2494)
        #frange = 2494.0 - 2402.0
        # The top end of 14 is 2494 but that would map to 16 on the chart
        #crange = 16.0
        #channel = float((float(frequency) - 2402.0) / frange * crange)
        
        #return channel
            
if __name__ == '__main__':
    hackrf = SparrowHackrf()
    
    if SparrowHackrf.getNumHackrfDevices() == 0:
        print("ERROR: No HackRF devices found.")
        exit(1)
    
    # Scan 5 GHz
    hackrf.minFreq = 5170
    hackrf.maxFreq = 5840
    hackrf.binWidth = 1000000
    
    hackrf.startScanning(32, 16)
    
    for i in range(0, 30):
        sleep(1)
        
    hackrf.stopScanning()
    
    print('Spectrum Length: ' + str(len(hackrf.spectrum)))
    
    i = 0
    
    for curKey in hackrf.spectrum.keys():
        print('spectrum[' + str(curKey) + '] = ' + str(hackrf.spectrum[curKey]))
        i += 1
        
        if i > 50:
            break
            
    print("Test complete.")
    
