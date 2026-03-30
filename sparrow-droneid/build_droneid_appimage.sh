#!/bin/bash
#
# Build AppImage for Sparrow DroneID
#
# Creates a portable AppImage that can run on most Linux distributions.
# Requires: Python 3.10+, pip, appimagetool (auto-downloaded if missing)
#
# Usage:
#   ./build_droneid_appimage.sh              # Build x86_64 AppImage (default)
#   ./build_droneid_appimage.sh aarch64      # Build aarch64 AppImage
#   ./build_droneid_appimage.sh --arch arm64 # Same, via flag
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/sparrow_droneid"
APP_NAME="SparrowDroneID"
APPIMAGE_VERSION="1.0.0"
PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
TOOLS_DIR="$SCRIPT_DIR/tools"

# Parse arguments
TARGET_ARCH=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            echo "Usage: $0 [options] [ARCH]"
            echo ""
            echo "Arguments:"
            echo "  ARCH              Target architecture: x86_64 (default), aarch64"
            echo ""
            echo "Options:"
            echo "  --arch ARCH       Target architecture (alternative to positional)"
            echo "  --help, -h        Show this help message"
            echo ""
            echo "Builds an AppImage for Sparrow DroneID."
            echo "The resulting AppImage can be run on most Linux distributions."
            echo "Requires root/sudo to run (monitor mode capture)."
            exit 0
            ;;
        --arch)
            TARGET_ARCH="$2"
            shift 2
            ;;
        x86_64|aarch64|arm64)
            TARGET_ARCH="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# Default to x86_64 if not specified
TARGET_ARCH="${TARGET_ARCH:-x86_64}"

case "$TARGET_ARCH" in
    x86_64)
        APPIMAGETOOL_ARCH="x86_64"
        RUNTIME_ARCH="x86_64"
        ARCH_SUFFIX="x86_64"
        ;;
    aarch64|arm64)
        APPIMAGETOOL_ARCH="arm_aarch64"
        RUNTIME_ARCH="aarch64"
        ARCH_SUFFIX="aarch64"
        ;;
    *)
        echo "ERROR: Unsupported architecture: $TARGET_ARCH"
        echo "Supported: x86_64, aarch64"
        exit 1
        ;;
esac

APPIMAGE_NAME="${APP_NAME}-${APPIMAGE_VERSION}-${ARCH_SUFFIX}.AppImage"

echo "Building $APP_NAME AppImage v$APPIMAGE_VERSION ($TARGET_ARCH)"
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

# Copy seed data files (vendor codes, SSID patterns)
# app.py resolves these via Path(__file__).parent.parent / 'data', which
# from usr/lib/python3.x/app.py lands at usr/lib/data/
SEED_DIR="$SCRIPT_DIR/data"
mkdir -p "$APPDIR/usr/lib/data"
for seed_file in vendor_codes.json drone_ssid_patterns.json; do
    if [ -f "$SEED_DIR/$seed_file" ]; then
        cp "$SEED_DIR/$seed_file" "$APPDIR/usr/lib/data/"
        echo "  Bundled seed: $seed_file"
    else
        echo "  WARNING: seed file not found: $SEED_DIR/$seed_file"
    fi
done

# Create launcher script
cat > "$APPDIR/usr/bin/$APP_NAME" << LAUNCHER_EOF
#!/bin/bash
APPDIR="\$(dirname "\$(dirname "\$(dirname "\$(readlink -f "\$0")")")")"
PYLIB="\$APPDIR/usr/lib/python$PYTHON_VERSION"
export PYTHONPATH="\$PYLIB:\$PYTHONPATH"
# Run from current working directory so relative paths (like --data) work correctly
exec python3 -u "\$PYLIB/app.py" --html-dir "\$PYLIB/frontend" "\$@"
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

# Determine host architecture for appimagetool binary
HOST_ARCH="$(uname -m)"

# Find or download appimagetool (runs on host arch, ARCH env var controls output)
echo "Locating appimagetool..."
APPIMAGETOOL="${APPIMAGETOOL:-}"

# Check known locations in priority order
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL="$(command -v appimagetool 2>/dev/null || true)"
fi

if [ -z "$APPIMAGETOOL" ] || [ ! -e "$APPIMAGETOOL" ]; then
    for candidate in \
        "/usr/local/bin/appimagetool" \
        "/opt/appimagetoolkit/appimagetool-${HOST_ARCH}.AppImage" \
        "$TOOLS_DIR/appimagetool-${HOST_ARCH}.AppImage" \
        "$SCRIPT_DIR/appimagetool-${HOST_ARCH}.AppImage"; do
        if [ -x "$candidate" ]; then
            APPIMAGETOOL="$candidate"
            break
        fi
    done
fi

# Auto-download if not found (cache in tools/ directory)
if [ -z "$APPIMAGETOOL" ] || [ ! -e "$APPIMAGETOOL" ]; then
    echo "appimagetool not found. Downloading for ${HOST_ARCH}..."
    mkdir -p "$TOOLS_DIR"
    APPIMAGETOOL="$TOOLS_DIR/appimagetool-${HOST_ARCH}.AppImage"
    curl -fSL --progress-bar \
        -o "$APPIMAGETOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${HOST_ARCH}.AppImage"
    chmod +x "$APPIMAGETOOL"
    echo "Downloaded and cached at: $APPIMAGETOOL"
fi

echo "Using: $APPIMAGETOOL"

# Use cached runtime if available, otherwise download for cross-arch builds
RUNTIME_OPT=""
if [ "$RUNTIME_ARCH" != "$HOST_ARCH" ]; then
    RUNTIME_FILE="$TOOLS_DIR/runtime-${RUNTIME_ARCH}"
    mkdir -p "$TOOLS_DIR"
    if [ -f "$RUNTIME_FILE" ]; then
        echo "Using cached runtime: $RUNTIME_FILE"
    else
        echo "Downloading runtime for ${RUNTIME_ARCH}..."
        curl -fSL --progress-bar \
            -o "$RUNTIME_FILE" \
            "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-${RUNTIME_ARCH}"
        echo "Downloaded and cached at: $RUNTIME_FILE"
    fi
    RUNTIME_OPT="--runtime-file $RUNTIME_FILE"
fi

# Package the AppImage
echo "Packaging AppImage (ARCH=$APPIMAGETOOL_ARCH)..."
ARCH="$APPIMAGETOOL_ARCH" "$APPIMAGETOOL" $RUNTIME_OPT "$APPDIR" "$SCRIPT_DIR/$APPIMAGE_NAME"

# Clean up build directory
echo "Cleaning up build directory..."
rm -rf "$BUILD_DIR"

echo ""
echo "Done! AppImage created: $SCRIPT_DIR/$APPIMAGE_NAME"
echo ""
echo "Usage: sudo ./$APPIMAGE_NAME [--port 8097] [--interface wlan0] [--data ./data]"
