#!/usr/bin/env python3
"""
test_monitor_mode.py — does this WiFi card actually do monitor mode?

Standalone diagnostic. Mirrors the exact monitor-mode methodology used by
sparrow-droneid's CaptureManager (driver-aware: VIF method for iwlwifi,
in-place 'iw set type monitor' otherwise), then proves it end-to-end:

  1. Enumerate WiFi interfaces and check phy monitor capability (iw phy).
  2. Flip the chosen interface into monitor mode on a channel.
  3. Run tcpdump and count frames actually received (RX is alive).
  4. Restore the interface to managed mode.
  5. Report yes/no support along the way.

The frame-RX step matters because some drivers (notably iwlwifi) report
monitor capability and accept the mode switch, yet silently drop every frame
in direct monitor mode — the card "supports" monitor mode on paper but is
useless for capture. Only an actual frame count proves it works.

Usage:
    sudo ./test_monitor_mode.py                # auto-pick first capable iface
    sudo ./test_monitor_mode.py wlan1          # test a specific interface
    sudo ./test_monitor_mode.py wlan1 -c 1     # channel 1 (default 6)
    sudo ./test_monitor_mode.py wlan1 -d 8     # 8s capture dwell (default 5)
    sudo ./test_monitor_mode.py --list         # just enumerate, don't switch

Requires root and the 'iw', 'ip', and 'tcpdump' tools.
"""

import argparse
import os
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Pretty status helpers
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def step(msg):
    print(f"[*] {msg}")


def ok(msg):
    print(_c("32", f"[+] {msg}"))


def warn(msg):
    print(_c("33", f"[!] {msg}"))


def fail(msg):
    print(_c("31", f"[-] {msg}"))


def run(cmd, timeout=5):
    """Run a command, return CompletedProcess (never raises on non-zero)."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Interface enumeration  (mirrors CaptureManager.get_interfaces)
# ---------------------------------------------------------------------------

def get_driver(interface):
    """Resolve the kernel driver for an interface, or '' if unknown."""
    try:
        driver_path = f"/sys/class/net/{interface}/device/driver"
        if os.path.islink(driver_path):
            return os.path.basename(os.readlink(driver_path))
    except OSError:
        pass
    return ""


def get_interfaces():
    """Enumerate WiFi interfaces with monitor-mode capability via iw."""
    interfaces = []
    try:
        output = run(['iw', 'dev']).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return interfaces

    current_phy = ""
    current = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('phy#'):
            current_phy = line.rstrip()
        elif line.startswith('Interface '):
            if current.get('name'):
                interfaces.append(current)
            current = {
                'name': line.split()[1],
                'phy': current_phy,
                'mode': 'managed',
                'mac_address': '',
                'monitor_capable': False,
                'driver': '',
            }
        elif line.startswith('addr '):
            current['mac_address'] = line.split()[1]
        elif line.startswith('type '):
            current['mode'] = line.split()[1]
    if current.get('name'):
        interfaces.append(current)

    # Per-phy "Supported interface modes" — does it list monitor?
    try:
        phy_output = run(['iw', 'phy']).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        phy_output = ""

    phy_monitor = set()
    current_phy_name = ""
    in_modes = False
    for line in phy_output.splitlines():
        stripped = line.strip()
        if line.startswith('Wiphy '):
            current_phy_name = 'phy#' + stripped.split()[-1].replace('phy', '')
            in_modes = False
        elif 'Supported interface modes:' in stripped:
            in_modes = True
        elif in_modes:
            if stripped.startswith('*'):
                if 'monitor' in stripped.lower():
                    phy_monitor.add(current_phy_name)
            elif stripped:
                in_modes = False

    for iface in interfaces:
        iface['monitor_capable'] = iface['phy'] in phy_monitor
        iface['driver'] = get_driver(iface['name'])
    return interfaces


# ---------------------------------------------------------------------------
# Monitor-mode switching  (mirrors CaptureManager.start_monitor/stop_monitor)
# ---------------------------------------------------------------------------

def _is_rpi_wireless(interface):
    """Onboard RPi WiFi (brcmfmac) runs managed + monitor VIFs on one phy."""
    return get_driver(interface) in ('brcmfmac', 'brcm80211')


def start_monitor(interface, channel=6):
    """Switch interface to monitor mode. Returns the monitor iface name.

    iwlwifi → VIF method (iw phy interface add <name>mon type monitor),
    since its firmware silently drops frames in direct monitor mode.
    Everything else → in-place 'iw set type monitor'.
    """
    driver = get_driver(interface)

    if driver == 'iwlwifi':
        mon_iface = interface + 'mon'
        info = run(['iw', 'dev', interface, 'info']).stdout
        phy = None
        for line in info.splitlines():
            if 'wiphy' in line:
                phy = f"phy{line.strip().split()[-1]}"
                break
        if not phy:
            raise RuntimeError(f"Could not determine phy for {interface}")

        # Clean up any stale mon VIF, then build a fresh one (airmon-ng style).
        run(['iw', 'dev', mon_iface, 'del'])
        is_rpi = _is_rpi_wireless(interface)
        run(['ip', 'link', 'set', interface, 'down'])
        cmds = [['iw', phy, 'interface', 'add', mon_iface, 'type', 'monitor']]
        if not is_rpi:
            cmds.append(['iw', 'dev', interface, 'del'])
        cmds += [
            ['ip', 'link', 'set', mon_iface, 'up'],
            ['iw', 'dev', mon_iface, 'set', 'channel', str(channel)],
        ]
        for cmd in cmds:
            r = run(cmd)
            if r.returncode != 0:
                raise RuntimeError(f"Failed: {' '.join(cmd)}: {r.stderr.strip()}")
        return mon_iface

    # Standard in-place method
    cmds = [
        ['ip', 'link', 'set', interface, 'down'],
        ['iw', 'dev', interface, 'set', 'type', 'monitor'],
        ['ip', 'link', 'set', interface, 'up'],
        ['iw', 'dev', interface, 'set', 'channel', str(channel)],
    ]
    for cmd in cmds:
        r = run(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"Failed: {' '.join(cmd)}: {r.stderr.strip()}")
    return interface


def stop_monitor(interface):
    """Restore managed mode, or delete the VIF and rebuild the base iface."""
    if interface.endswith('mon'):
        base_iface = interface[:-3]
        info = run(['iw', 'dev', interface, 'info']).stdout
        phy = None
        for line in info.splitlines():
            if 'wiphy' in line:
                phy = f"phy{line.strip().split()[-1]}"
                break
        run(['iw', 'dev', interface, 'del'])
        base_exists = os.path.exists(f"/sys/class/net/{base_iface}")
        if phy and not base_exists:
            run(['iw', phy, 'interface', 'add', base_iface, 'type', 'managed'])
        if os.path.exists(f"/sys/class/net/{base_iface}"):
            run(['ip', 'link', 'set', base_iface, 'up'])
    else:
        for cmd in (
            ['ip', 'link', 'set', interface, 'down'],
            ['iw', 'dev', interface, 'set', 'type', 'managed'],
            ['ip', 'link', 'set', interface, 'up'],
        ):
            run(cmd)


# ---------------------------------------------------------------------------
# Frame-RX probe
# ---------------------------------------------------------------------------

def count_frames(interface, dwell):
    """Capture for `dwell` seconds; return (total_frames, odid_frames).

    Counts ALL 802.11 management/data frames (beacons are everywhere, so any
    nonzero count proves RX works), and separately counts ODID-relevant
    frames (Action 0xd0 / Beacon 0x80 / Probe-Resp 0x50) that the droneid
    capture filter would keep.
    """
    odid_bpf = 'wlan[0] == 0xd0 or wlan[0] == 0x80 or wlan[0] == 0x50'
    # -nn no name resolution, -e link layer, line-buffered count of frames.
    proc = subprocess.Popen(
        ['tcpdump', '-i', interface, '-nn', '-l', '--immediate-mode', '-s', '0'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    odid_proc = subprocess.Popen(
        ['tcpdump', '-i', interface, '-nn', '-l', '--immediate-mode', '-s', '0', odid_bpf],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    time.sleep(dwell)

    total = odid = 0
    for p, attr in ((proc, 'total'), (odid_proc, 'odid')):
        try:
            p.terminate()
            out, _ = p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate(timeout=2)
        n = sum(1 for ln in out.splitlines() if ln.strip())
        if attr == 'total':
            total = n
        else:
            odid = n
    return total, odid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_table(interfaces):
    print()
    print(f"  {'IFACE':<10} {'PHY':<7} {'MODE':<10} {'DRIVER':<12} MON-CAPABLE")
    print(f"  {'-'*10} {'-'*7} {'-'*10} {'-'*12} {'-'*11}")
    for i in interfaces:
        cap = _c("32", "yes") if i['monitor_capable'] else _c("31", "no")
        phy = i['phy'].replace('phy#', '')
        print(f"  {i['name']:<10} {phy:<7} {i['mode']:<10} "
              f"{i['driver'] or '?':<12} {cap}")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Test whether a WiFi card supports working monitor mode.")
    ap.add_argument('interface', nargs='?',
                    help="interface to test (default: first capable one)")
    ap.add_argument('-c', '--channel', type=int, default=6,
                    help="channel to dwell on (default: 6)")
    ap.add_argument('-d', '--dwell', type=int, default=5,
                    help="seconds to capture frames (default: 5)")
    ap.add_argument('--list', action='store_true',
                    help="only enumerate interfaces; don't switch modes")
    args = ap.parse_args()

    # Tool presence
    missing = [t for t in ('iw', 'ip', 'tcpdump')
               if run(['which', t]).returncode != 0]
    if missing:
        fail(f"Missing required tool(s): {', '.join(missing)}")
        return 2

    step("Enumerating WiFi interfaces (iw dev / iw phy)...")
    interfaces = get_interfaces()
    if not interfaces:
        fail("No WiFi interfaces found.")
        return 1
    print_table(interfaces)

    if args.list:
        return 0

    # Pick the interface to test
    by_name = {i['name']: i for i in interfaces}
    if args.interface:
        target = by_name.get(args.interface)
        if not target:
            # Maybe they passed a base name whose mon VIF is what iw shows;
            # otherwise it's just not present.
            fail(f"Interface '{args.interface}' not found.")
            return 1
    else:
        target = next((i for i in interfaces if i['monitor_capable']), None)
        if not target:
            fail("No monitor-capable interface found to test.")
            return 1
        step(f"Auto-selected '{target['name']}' (first capable interface).")

    name = target['name']
    driver = target['driver'] or get_driver(name)

    # Capability gate
    if target['monitor_capable']:
        ok(f"{name}: phy reports monitor mode in supported interface modes.")
    else:
        warn(f"{name}: phy does NOT list monitor in supported modes — "
             f"the switch will probably fail, but trying anyway.")

    if os.geteuid() != 0:
        fail("Mode switching requires root. Re-run with sudo.")
        return 2

    if driver == 'iwlwifi':
        step(f"Driver is iwlwifi → using VIF method (creates {name}mon).")
    else:
        step(f"Driver is {driver or 'unknown'} → using in-place monitor switch.")

    mon_iface = None
    supported = False
    try:
        step(f"Switching {name} to monitor mode on channel {args.channel}...")
        mon_iface = start_monitor(name, args.channel)
        ok(f"Monitor mode active on '{mon_iface}'.")

        # Confirm the kernel actually reports type monitor
        info = run(['iw', 'dev', mon_iface, 'info']).stdout
        if 'type monitor' in info:
            ok(f"{mon_iface}: kernel confirms 'type monitor'.")
        else:
            warn(f"{mon_iface}: kernel did not confirm monitor type.")

        step(f"Capturing frames for {args.dwell}s to verify RX is alive...")
        total, odid = count_frames(mon_iface, args.dwell)

        if total > 0:
            ok(f"Received {total} frames ({odid} ODID-relevant) in {args.dwell}s "
               f"→ {total / args.dwell:.1f} fps.")
            supported = True
        else:
            fail(f"Monitor mode set, but ZERO frames received in {args.dwell}s.")
            if driver == 'iwlwifi':
                warn("iwlwifi commonly drops all frames in monitor mode even "
                     "via VIF — consider an external USB adapter (e.g. AX-class "
                     "or RTL8812AU) for reliable capture.")
            else:
                warn("Possible causes: dead-air channel (try -c 1/6/11), antenna "
                     "issue, or a driver that accepts the mode but won't deliver "
                     "frames. Try a busier channel before concluding.")
    except RuntimeError as exc:
        fail(f"Monitor mode switch FAILED: {exc}")
    except Exception as exc:
        fail(f"Unexpected error: {exc}")
    finally:
        if mon_iface:
            step("Restoring interface to managed mode...")
            try:
                stop_monitor(mon_iface)
                ok(f"Restored. ('{name}' back to managed.)")
            except Exception as exc:
                warn(f"Restore hit an error: {exc} — check 'iw dev' manually.")

    print()
    if supported:
        ok(f"RESULT: {name} ({driver or '?'}) — monitor mode WORKS. ✓")
        return 0
    else:
        fail(f"RESULT: {name} ({driver or '?'}) — monitor mode NOT usable. ✗")
        return 1


if __name__ == '__main__':
    sys.exit(main())
