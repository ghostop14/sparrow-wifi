#!/usr/bin/env python3
"""
Sparrow DroneID — Main Entry Point

FAA Remote ID drone detection via Wi-Fi NAN/Beacon frame capture.
Serves a browser-based UI and REST API.
"""
import argparse
import os
import signal
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from time import sleep

# Add project root to path so relative imports work from any CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.database import get_db
from backend.droneid_engine import DroneIDEngine, CaptureManager, check_prerequisites
from backend.gps_engine import GPSEngine
from backend.alert_engine import AlertEngine
from backend.cot_engine import CotEngine
from backend.cert_manager import CertManager
from backend.api_handler import (
    MultithreadHTTPServer, RequestHandler, set_engines,
)

__version__ = '1.0.0'


class SparrowDroneID:
    """Main application class managing lifecycle of all components."""

    def __init__(self, port: int = 8097, html_dir: str = 'frontend',
                 data_dir: str = None, interface: str = None):
        self.port = port
        self.html_dir = html_dir
        self.data_dir = data_dir or 'data'
        self.db_path = os.path.join(self.data_dir, 'sparrow_droneid.db')
        self.auto_interface = interface  # Auto-start monitoring on this interface

        self._shutdown_event = threading.Event()
        self._httpd = None
        self._maintenance_thread = None

        # Engine references
        self.db = None
        self.gps_engine = None
        self.droneid_engine = None
        self.alert_engine = None
        self.cot_engine = None
        self.cert_manager = None

    def _check_prerequisites(self):
        """Check root, tcpdump, iw. Exit on failure."""
        errors = check_prerequisites()
        if errors:
            for err in errors:
                print(f"ERROR: {err}")
            sys.exit(1)

    def _ensure_data_dir(self):
        """Create data directory if needed."""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except OSError as e:
            print(f"ERROR: Unable to create data directory '{self.data_dir}': {e.strerror}")
            sys.exit(1)

    def _init_database(self):
        """Initialize database and read saved port if applicable."""
        self.db = get_db(self.db_path)

        # Use saved port if default was passed
        if self.port == 8097:
            saved_port = self.db.get_setting('port', '8097')
            try:
                self.port = int(saved_port)
            except (ValueError, TypeError):
                pass

        return self.db

    def _init_engines(self):
        """Create and configure all engines."""
        db = self.db

        # Certificate manager
        self.cert_manager = CertManager(os.path.join(self.data_dir, 'certs'))

        # GPS engine
        self.gps_engine = GPSEngine()
        gps_mode = db.get_setting('gps_mode', 'none')
        self.gps_engine.configure(
            mode=gps_mode,
            static_lat=float(db.get_setting('gps_static_lat', '0.0')),
            static_lon=float(db.get_setting('gps_static_lon', '0.0')),
            static_alt=float(db.get_setting('gps_static_alt', '0.0')),
        )

        # DroneID engine
        self.droneid_engine = DroneIDEngine(db, self.gps_engine)

        # Alert engine
        self.alert_engine = AlertEngine(db)

        # CoT engine
        self.cot_engine = CotEngine()
        cot_enabled = db.get_setting('cot_enabled', 'false').lower() == 'true'
        if cot_enabled:
            self.cot_engine.configure(
                enabled=True,
                address=db.get_setting('cot_address', '239.2.3.1'),
                port=int(db.get_setting('cot_port', '6969')),
            )

        # Wire detection callback: alert engine + CoT engine
        def on_detection(device):
            self.alert_engine.evaluate(device)
            if self.cot_engine.enabled:
                self.cot_engine.send_event(device)

        self.droneid_engine.on_detection = on_detection

    def _setup_signal_handlers(self):
        """Graceful shutdown on Ctrl+C / SIGTERM."""
        def signal_handler(signum, frame):
            print("\nShutting down...")
            self._shutdown_event.set()
            if self._httpd:
                threading.Thread(target=self._httpd.shutdown, daemon=True).start()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _start_maintenance_thread(self):
        """Periodic maintenance: retention purge, stale cleanup, signal-lost checks."""
        def maintenance_loop():
            while not self._shutdown_event.is_set():
                self._shutdown_event.wait(60)
                if self._shutdown_event.is_set():
                    break
                try:
                    # Retention purge
                    retention_days = int(self.db.get_setting('retention_days', '14'))
                    if retention_days > 0:
                        self.db.run_retention_purge(retention_days)

                    # Clean stale drones from memory
                    self.droneid_engine.cleanup_stale(300)

                    # Check for signal-lost alerts
                    with self.droneid_engine._lock:
                        snapshot = dict(self.droneid_engine._active_drones)
                    self.alert_engine.check_signal_lost(snapshot)
                except Exception as e:
                    print(f"Maintenance error: {e}")

        self._maintenance_thread = threading.Thread(
            target=maintenance_loop, daemon=True, name='maintenance'
        )
        self._maintenance_thread.start()

    def _auto_start_monitoring(self):
        """Auto-start monitoring if interface is configured."""
        interface = self.auto_interface
        if not interface:
            interface = self.db.get_setting('monitor_interface', '')
        if not interface:
            return

        try:
            self.droneid_engine.start(interface)
            print(f"  Monitor:  {interface} on channel 6")
        except Exception as e:
            print(f"  Monitor:  Failed to start on {interface}: {e}")

    def run(self):
        """Run the application."""
        # Prerequisites
        self._check_prerequisites()
        self._ensure_data_dir()

        # Database
        db = self._init_database()

        # Engines
        self._init_engines()

        # Signal handlers
        self._setup_signal_handlers()

        # Resolve html_dir
        if not os.path.isabs(self.html_dir):
            self.html_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), self.html_dir
            )

        # Wire engines into API handler
        set_engines(
            droneid=self.droneid_engine,
            gps=self.gps_engine,
            alert=self.alert_engine,
            cot=self.cot_engine,
            db=db,
            data_dir=self.data_dir,
            html_dir=self.html_dir,
            cert_manager=self.cert_manager,
        )

        # Bind address
        bind_addr = db.get_setting('bind_address', '127.0.0.1')
        server_address = (bind_addr, self.port)

        try:
            self._httpd = MultithreadHTTPServer(server_address, RequestHandler)
        except OSError as e:
            print(f"ERROR: Unable to bind to {bind_addr}:{self.port}: {e.strerror}")
            sys.exit(1)

        # HTTPS
        https_enabled = db.get_setting('https_enabled', 'false').lower() == 'true'
        scheme = 'http'
        if https_enabled:
            cert_name = db.get_setting('https_cert_name', '')
            if cert_name:
                try:
                    cert_path, key_path = self.cert_manager.get_cert_path(cert_name)
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ctx.load_cert_chain(cert_path, key_path)
                    self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
                    scheme = 'https'
                except Exception as e:
                    print(f"WARN: HTTPS setup failed: {e}. Falling back to HTTP.")
            else:
                print("WARN: HTTPS enabled but https_cert_name is not set. Falling back to HTTP.")

        # Start maintenance thread
        self._start_maintenance_thread()

        # Auto-start monitoring
        self._auto_start_monitoring()

        # Banner
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display_host = 'localhost' if bind_addr == '127.0.0.1' else bind_addr
        print(f"\n[{ts}] Sparrow DroneID v{__version__}")
        print(f"  URL:      {scheme}://{display_host}:{self.port}")
        print(f"  Database: {db.db_path}")
        gps_mode = db.get_setting('gps_mode', 'none')
        print(f"  GPS:      {gps_mode}")
        status = "Monitoring" if self.droneid_engine.monitoring else "Ready (not monitoring)"
        print(f"  Status:   {status}")
        print("\nPress Ctrl+C to stop\n")

        # Serve
        try:
            self._httpd.serve_forever()
        except Exception as e:
            if not self._shutdown_event.is_set():
                print(f"Server error: {e}")

        # Cleanup
        if self.droneid_engine.monitoring:
            print("Stopping monitor capture...")
            self.droneid_engine.stop()

        self.gps_engine.stop()
        self.cot_engine.stop()
        self._httpd.server_close()

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] Sparrow DroneID stopped.")


def main():
    parser = argparse.ArgumentParser(
        description='Sparrow DroneID — FAA Remote ID drone detection',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sudo %(prog)s                              # Start with defaults
  sudo %(prog)s --port 8080                  # Custom port
  sudo %(prog)s --interface wlan1            # Auto-start monitoring
  sudo %(prog)s --data /var/lib/droneid      # Custom data directory
        """
    )

    parser.add_argument(
        '--port', '-p', type=int, default=8097,
        help='HTTP server port (default: 8097 or from settings)',
    )
    parser.add_argument(
        '--html-dir', default='frontend',
        help='Directory for static frontend files (default: frontend)',
    )
    parser.add_argument(
        '--data', default=None,
        help='Data directory for database and tile cache (default: ./data)',
    )
    parser.add_argument(
        '--interface', '-i', default=None,
        help='WiFi interface to auto-start monitoring on (e.g., wlan0)',
    )

    args = parser.parse_args()

    app = SparrowDroneID(
        port=args.port,
        html_dir=args.html_dir,
        data_dir=args.data,
        interface=args.interface,
    )
    app.run()


if __name__ == '__main__':
    main()
