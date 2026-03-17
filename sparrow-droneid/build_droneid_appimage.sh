#!/bin/bash
#
# Build AppImage for Sparrow DroneID
#
# Creates a portable AppImage that can run on most Linux distributions.
# Requires: Python 3.10+, pip, appimagetool (auto-downloaded if missing)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/sparrow_droneid"
APP_NAME="SparrowDroneID"
APPIMAGE_VERSION="1.0.0"
PYTHON_VERSION="3.10"
APPIMAGE_NAME="${APP_NAME}-${APPIMAGE_VERSION}-x86_64.AppImage"
TOOLS_DIR="$SCRIPT_DIR/tools"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --help, -h    Show this help message"
            echo ""
            echo "Builds an AppImage for Sparrow DroneID."
            echo "The resulting AppImage can be run on most Linux distributions."
            echo "Requires root/sudo to run (monitor mode capture)."
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

echo "Building $APP_NAME AppImage v$APPIMAGE_VERSION"
echo "=========================================="

# Create build directory
BUILD_DIR="$SCRIPT_DIR/build"
APPDIR="$BUILD_DIR/$APP_NAME.AppDir"

rm -rf "$BUILD_DIR"
mkdir -p "$APPDIR"

# Create directory structure
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib/python$PYTHON_VERSION"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy application files
echo "Copying application files..."
cp -r "$APP_DIR/backend" "$APPDIR/usr/lib/python$PYTHON_VERSION/"
cp -r "$APP_DIR/frontend" "$APPDIR/usr/lib/python$PYTHON_VERSION/"
cp "$APP_DIR/app.py" "$APPDIR/usr/lib/python$PYTHON_VERSION/"
cp "$APP_DIR/__init__.py" "$APPDIR/usr/lib/python$PYTHON_VERSION/"
cp "$APP_DIR/requirements.txt" "$APPDIR/usr/lib/python$PYTHON_VERSION/"

# Create launcher script
cat > "$APPDIR/usr/bin/$APP_NAME" << 'LAUNCHER_EOF'
#!/bin/bash
APPDIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
PYLIB="$APPDIR/usr/lib/python3.10"
export PYTHONPATH="$PYLIB:$PYTHONPATH"
# Run from current working directory so relative paths (like --data) work correctly
exec python3 "$PYLIB/app.py" --html-dir "$PYLIB/frontend" "$@"
LAUNCHER_EOF
chmod +x "$APPDIR/usr/bin/$APP_NAME"

# Create AppRun
cat > "$APPDIR/AppRun" << APPRUN_EOF
#!/bin/bash
APPDIR="\$(dirname "\$(readlink -f "\$0")")"
exec "\$APPDIR/usr/bin/$APP_NAME" "\$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

# Create desktop file
cat > "$APPDIR/usr/share/applications/$APP_NAME.desktop" << DESKTOP_EOF
[Desktop Entry]
Name=Sparrow DroneID
Comment=FAA Remote ID drone detection
Exec=$APP_NAME
Icon=$APP_NAME
Type=Application
Categories=Utility;Network;Security;
DESKTOP_EOF
cp "$APPDIR/usr/share/applications/$APP_NAME.desktop" "$APPDIR/"

# Create SVG icon
cat > "$APPDIR/$APP_NAME.svg" << 'ICON_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect width="256" height="256" fill="#0F172A" rx="32"/>
  <!-- Drone body -->
  <circle cx="128" cy="120" r="24" fill="none" stroke="#F59E0B" stroke-width="6"/>
  <!-- Rotors -->
  <line x1="80" y1="80" x2="108" y2="100" stroke="#F59E0B" stroke-width="4" stroke-linecap="round"/>
  <line x1="176" y1="80" x2="148" y2="100" stroke="#F59E0B" stroke-width="4" stroke-linecap="round"/>
  <line x1="80" y1="160" x2="108" y2="140" stroke="#F59E0B" stroke-width="4" stroke-linecap="round"/>
  <line x1="176" y1="160" x2="148" y2="140" stroke="#F59E0B" stroke-width="4" stroke-linecap="round"/>
  <circle cx="80" cy="80" r="14" fill="none" stroke="#94A3B8" stroke-width="3"/>
  <circle cx="176" cy="80" r="14" fill="none" stroke="#94A3B8" stroke-width="3"/>
  <circle cx="80" cy="160" r="14" fill="none" stroke="#94A3B8" stroke-width="3"/>
  <circle cx="176" cy="160" r="14" fill="none" stroke="#94A3B8" stroke-width="3"/>
  <!-- Signal waves -->
  <path d="M128 160 Q128 180 128 200" stroke="#14B8A6" stroke-width="3" stroke-linecap="round" fill="none"/>
  <path d="M112 190 Q128 210 144 190" stroke="#14B8A6" stroke-width="2.5" stroke-linecap="round" fill="none"/>
  <path d="M100 200 Q128 225 156 200" stroke="#14B8A6" stroke-width="2" stroke-linecap="round" fill="none"/>
</svg>
ICON_EOF
cp "$APPDIR/$APP_NAME.svg" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"

# Install Python dependencies into AppDir
echo "Installing Python dependencies..."
pip install --target="$APPDIR/usr/lib/python$PYTHON_VERSION" \
    -r "$APP_DIR/requirements.txt" \
    --quiet --no-warn-script-location

# Find or download appimagetool
echo "Locating appimagetool..."
APPIMAGETOOL="${APPIMAGETOOL:-}"

# Check known locations in priority order
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL="$(command -v appimagetool 2>/dev/null || true)"
fi

if [ -z "$APPIMAGETOOL" ] || [ ! -e "$APPIMAGETOOL" ]; then
    if [ -e /usr/local/bin/appimagetool ]; then
        APPIMAGETOOL="/usr/local/bin/appimagetool"
    elif [ -e /opt/appimagetoolkit/appimagetool-x86_64.AppImage ]; then
        APPIMAGETOOL="/opt/appimagetoolkit/appimagetool-x86_64.AppImage"
    elif [ -e "$TOOLS_DIR/appimagetool-x86_64.AppImage" ]; then
        APPIMAGETOOL="$TOOLS_DIR/appimagetool-x86_64.AppImage"
    fi
fi

# Auto-download if not found (cache in tools/ directory)
if [ -z "$APPIMAGETOOL" ] || [ ! -e "$APPIMAGETOOL" ]; then
    echo "appimagetool not found. Downloading..."
    mkdir -p "$TOOLS_DIR"
    APPIMAGETOOL="$TOOLS_DIR/appimagetool-x86_64.AppImage"
    curl -fSL --progress-bar \
        -o "$APPIMAGETOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
    echo "Downloaded and cached at: $APPIMAGETOOL"
fi

echo "Using: $APPIMAGETOOL"

# Package the AppImage (gzip compression — zstd not supported by latest appimagetool)
echo "Packaging AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" --comp gzip "$APPDIR" "$SCRIPT_DIR/$APPIMAGE_NAME"

# Clean up build directory
echo "Cleaning up build directory..."
rm -rf "$BUILD_DIR"

echo ""
echo "Done! AppImage created: $SCRIPT_DIR/$APPIMAGE_NAME"
echo ""
echo "Usage: sudo ./$APPIMAGE_NAME [--port 8097] [--interface wlan0] [--data ./data]"
