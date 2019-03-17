#!/bin/bash

# Thanks to the folks here on this thread: https://github.com/mu-editor/mu/issues/441
# There's a way to get pyqtchart built on a raspberry pi before it works its way into the build

echo "[`date`] Installing preqrequisites..."
apt-get update > /dev/null
sudo apt-get -y install python3-pip gpsd gpsd-clients python3-tk python3-setuptools python3-matplotlib python3-qscintilla python3-pyqt5 qtdeclarative5-dev qt5-default pyqt5-dev pyqt5-dev-tools pyqt5.qsci-dev qt5-qmake
sudo pip3 install gps3 dronekit manuf python-dateutil numpy
# Now to build pyqtchart

echo "[`date`] Building SIP prerequisite package..."
cd /tmp
wget https://downloads.sourceforge.net/project/pyqt/sip/sip-4.18/sip-4.18.tar.gz

if [ $? -gt 0 ]; then
	echo "ERROR: Unable to download sip package."
	exit 1
fi

tar -zxvf sip-4.18.tar.gz
cd sip-4.18
python3 configure.py
make -j 3
sudo make install
# clean up
cd /tmp
rm -rf sip-4.18

echo "[`date`] Building qtcharts..."
cd /tmp
git clone git://code.qt.io/qt/qtcharts.git -b 5.7
cd qtcharts
qmake -r
make -j 3
sudo make install
if [ $? -eq 0 ]; then
	# clean up
	cd /tmp
	rm -rf qtcharts
fi

echo "[`date`] Building pyqtcharts..."
cd /tmp
wget https://datapacket.dl.sourceforge.net/project/pyqt/PyQtChart/PyQtChart-5.7/PyQtChart_gpl-5.7.tar.gz
if [ $? -gt 0 ]; then
	echo "ERROR: Unable to download pyqtchart package."
	exit 1
fi
tar zxvf PyQtChart_gpl-5.7.tar.gz
cd PyQtChart_gpl-5.7
python3 configure.py --qtchart-version=2.0.1 --verbose
make -j 3
sudo make install
if [ $? -eq 0 ]; then
	# clean up
	cd /tmp
	rm -rf PyQtChart_gpl-5.7
fi

echo "[`date`] Done."

