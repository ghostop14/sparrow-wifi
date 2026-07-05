# Sparrow-WiFi packaging.
#
# Builds installable .deb / .rpm packages with fpm from a single staged tree.
# The payload is pure Python (only the distro Qt stack is arch-specific, and
# that comes in via package dependencies), so the packages are architecture
# independent: Architecture: all (deb) / noarch (rpm).
#
# Usage:
#   make deb            # build the .deb into dist/
#   make rpm            # build the .rpm into dist/ (needs the `rpm` tool)
#   make all            # both
#   make VERSION=1.2.0 deb
#   make clean
#
# Requires: fpm (https://github.com/jordansissel/fpm). `make rpm` additionally
# needs the `rpm` CLI so fpm can emit that format.

PKG         := sparrow-wifi
VERSION     ?= 1.0.0
ITERATION   ?= 1
MAINTAINER  := UltronCORE <syndr@ultroncore.net>
URL         := https://github.com/syndr/sparrow-wifi
LICENSE     := GPL-3.0
DESCRIPTION := WiFi/Bluetooth spectral-awareness, spectrum analysis and GPS wardriving tool (PyQt5).

BUILD    := build
PKGROOT  := $(BUILD)/pkgroot
DIST     := dist

# --- payload: what ships under /usr/share/sparrow-wifi ---------------------
# Explicit list (a `sparrow*.py` glob would wrongly pull in sparrow-elastic.py).
APP_PY := \
	sparrow-wifi.py sparrowwifiagent.py sparrowbluetooth.py sparrowcommon.py \
	sparrowdialogs.py sparrowdrone.py sparrowgps.py sparrowhackrf.py \
	sparrowmap.py sparrowrpi.py sparrowtablewidgets.py \
	wirelessengine.py telemetry.py __init__.py
APP_DATA := wifi_icon.png LICENSE
APP_DIRS := images plugins

APP_DST := $(PKGROOT)/usr/share/$(PKG)

# --- dependency lists (package names differ per ecosystem) -----------------
# python3-dev/-devel + a compiler are needed because the postinstall builds a
# venv and pip-installs dronekit, which compiles pymavlink's C extension.
DEB_DEPS := \
	python3 python3-venv python3-pip python3-dev build-essential \
	python3-pyqt5 python3-pyqt5.qtchart python3-pyqt5.qsci \
	python3-numpy python3-matplotlib python3-requests python3-dateutil \
	gpsd gpsd-clients pkexec
RPM_DEPS := \
	python3 python3-pip python3-devel gcc \
	python3-qt5 python3-qscintilla-qt5 \
	python3-numpy python3-matplotlib python3-requests python3-dateutil \
	gpsd gpsd-clients polkit

DEB_DEPENDS := $(foreach d,$(DEB_DEPS),-d $(d))
RPM_DEPENDS := $(foreach d,$(RPM_DEPS),-d $(d))

# --- fpm invocation shared bits --------------------------------------------
FPM_COMMON := \
	-s dir -a all \
	-n $(PKG) -v $(VERSION) --iteration $(ITERATION) \
	--maintainer "$(MAINTAINER)" \
	--url "$(URL)" \
	--license "$(LICENSE)" \
	--description "$(DESCRIPTION)" \
	--after-install  "$(CURDIR)/packaging/postinst" \
	--after-remove   "$(CURDIR)/packaging/postrm" \
	-C $(PKGROOT) -p $(DIST)

.PHONY: all deb rpm stage clean help check-fpm check-rpm

help:
	@echo "Targets: deb, rpm, all, stage, clean   (override VERSION=x.y.z)"

all: deb rpm

# --- assemble the install tree ---------------------------------------------
stage:
	@echo ">> staging $(PKG) $(VERSION) into $(PKGROOT)"
	rm -rf $(PKGROOT)
	mkdir -p $(APP_DST) \
	         $(PKGROOT)/usr/bin \
	         $(PKGROOT)/usr/share/applications \
	         $(PKGROOT)/usr/share/pixmaps
	# App code + data
	cp -p $(APP_PY) $(APP_DATA) $(APP_DST)/
	cp -rp $(APP_DIRS) $(APP_DST)/
	# Strip build cruft that may sit in the source tree
	find $(APP_DST) -name '__pycache__' -type d -prune -exec rm -rf {} +
	find $(APP_DST) -name '*.pyc' -delete
	# Launcher, desktop entry, icon
	install -m755 packaging/sparrow-wifi.launcher $(PKGROOT)/usr/bin/$(PKG)
	install -m644 packaging/sparrow-wifi.desktop  $(PKGROOT)/usr/share/applications/$(PKG).desktop
	install -m644 wifi_icon.png                   $(PKGROOT)/usr/share/pixmaps/$(PKG).png
	mkdir -p $(DIST)

# --- package builds --------------------------------------------------------
deb: check-fpm stage
	rm -f $(DIST)/$(PKG)_$(VERSION)-$(ITERATION)_all.deb
	fpm -t deb $(DEB_DEPENDS) $(FPM_COMMON) .
	@echo ">> built: $$(ls $(DIST)/*.deb)"

rpm: check-fpm check-rpm stage
	rm -f $(DIST)/$(PKG)-$(VERSION)-$(ITERATION).noarch.rpm
	fpm -t rpm $(RPM_DEPENDS) $(FPM_COMMON) .
	@echo ">> built: $$(ls $(DIST)/*.rpm)"

clean:
	rm -rf $(BUILD) $(DIST)

# --- tooling guards --------------------------------------------------------
check-fpm:
	@command -v fpm >/dev/null 2>&1 || { \
		echo "error: fpm not found. Install with: gem install fpm  (needs Ruby)"; exit 1; }

check-rpm:
	@command -v rpm >/dev/null 2>&1 || command -v rpmbuild >/dev/null 2>&1 || { \
		echo "error: the 'rpm' tool is required to build .rpm (apt install rpm)"; exit 1; }
