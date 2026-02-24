#!/usr/bin/python3
"""Quick tests for _findInterfaceApp() and WirelessEngine.getInterfaces().

Runs each available tool (iwconfig, iw, nmcli) independently and checks
whether they all agree on the list of wireless interfaces.
"""

import sys
import re
import shutil
import subprocess
sys.path.insert(0, '.')

import wirelessengine
from wirelessengine import _INTERFACE_APPS, WirelessEngine

PASS = '\033[32mPASS\033[0m'
FAIL = '\033[31mFAIL\033[0m'

def check(label, condition, detail=''):
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}" + (f": {detail}" if detail else ''))
    return condition

results = []

# ── Test 1: Run each tool independently and report results ─────────────────
print("\nTest 1: Individual tool results")
tool_results = {}  # exe -> sorted list of interfaces (or None if unavailable)

for exe, cmd, pattern in _INTERFACE_APPS:
    available = shutil.which(exe) is not None
    if available:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        output = result.stdout.decode('UTF-8')
        interfaces = sorted(i.replace(' ', '') for i in re.findall(pattern, output, re.MULTILINE))
        tool_results[exe] = interfaces
        print(f"  {exe:10s}  available  ->  {interfaces}")
    else:
        tool_results[exe] = None
        print(f"  {exe:10s}  not found")

# ── Test 2: Compare results across available tools ─────────────────────────
print("\nTest 2: Agreement across available tools")
available_results = {exe: ifaces for exe, ifaces in tool_results.items() if ifaces is not None}

if len(available_results) < 2:
    print("  [----] Only one tool available, nothing to compare")
else:
    values = list(available_results.values())
    all_agree = all(v == values[0] for v in values[1:])
    results.append(check("all available tools return the same interfaces", all_agree))
    if not all_agree:
        for exe, ifaces in available_results.items():
            print(f"    {exe}: {ifaces}")

# ── Test 3: getInterfaces() cache and basic sanity ─────────────────────────
print("\nTest 3: WirelessEngine.getInterfaces()")
wirelessengine.interfaceApp = None
interfaces = WirelessEngine.getInterfaces()
results.append(check("returns a list",    isinstance(interfaces, list)))
results.append(check("list is non-empty", len(interfaces) > 0, str(interfaces)))
results.append(check("no entries have spaces", all(' ' not in i for i in interfaces)))

cached = wirelessengine.interfaceApp
results.append(check("interfaceApp cached after call", cached is not None, str(cached)))

interfaces2 = WirelessEngine.getInterfaces()
results.append(check("second call uses same cached app", wirelessengine.interfaceApp is cached))
results.append(check("second call returns same list",   sorted(interfaces2) == sorted(interfaces)))

# ── Test 4: printResults=True produces output ──────────────────────────────
print("\nTest 4: getInterfaces(printResults=True)")
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    WirelessEngine.getInterfaces(printResults=True)
output = buf.getvalue().strip()
results.append(check("printed output is non-empty", len(output) > 0, repr(output)))
results.append(check("printed interfaces match return value",
                      sorted(output.splitlines()) == sorted(interfaces)))

# ── Summary ────────────────────────────────────────────────────────────────
passed = sum(results)
total  = len(results)
print(f"\n{'='*40}")
print(f"  {passed}/{total} checks passed")
print('='*40)
sys.exit(0 if passed == total else 1)
