#!/usr/bin/env python3
"""
install_dashboards.py — Import Sparrow WiFi Kibana dashboards via the
Kibana saved-objects import API.

Usage:
    python install_dashboards.py --kibana-url http://kibana.example.com \\
        --username elastic --password elastic --overwrite

Requirements: Python 3.6+ stdlib only (urllib, no requests).
"""

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import json

DASHBOARDS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sparrow_elastic",
    "dashboards",
)

# Standard import order: index patterns first, then dashboards
_IMPORT_ORDER = [
    "index_patterns.ndjson",
    "sparrow_wifi_situational_awareness.ndjson",
    "sparrow_wifi_pattern_of_life.ndjson",
    "sparrow_wifi_new_device_detection.ndjson",
    "sparrow_wifi_spectrum_planning.ndjson",
]


def _build_auth_header(username, password, api_key):
    """Return Authorization header value, or None."""
    if api_key:
        return f"ApiKey {api_key}"
    if username and password:
        import base64
        credentials = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {credentials}"
    return None


def _encode_multipart(file_bytes, filename="import.ndjson",
                      boundary="----KibanaBoundary"):
    """
    Build a minimal multipart/form-data body with a single `file` field.
    Returns (body_bytes, content_type_header_value).
    """
    CRLF = b"\r\n"
    b = boundary.encode("ascii")
    disposition = (
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'
    ).encode("ascii")
    content_type_part = b"Content-Type: application/x-ndjson"

    parts = (
        b"--" + b + CRLF +
        disposition + CRLF +
        content_type_part + CRLF +
        CRLF +
        file_bytes + CRLF +
        b"--" + b + b"--" + CRLF
    )
    content_type = f"multipart/form-data; boundary={boundary}"
    return parts, content_type


def import_file(kibana_url, ndjson_path, overwrite=False,
                auth_header=None, verify_tls=True):
    """
    POST a single NDJSON file to the Kibana saved-objects import endpoint.

    Returns a dict:
        {
            "success": bool,
            "success_count": int,
            "error_count": int,
            "errors": list[dict],
            "raw_response": dict | None,
            "http_error": str | None,
        }
    """
    url_params = {"overwrite": "true" if overwrite else "false",
                  "compatibilityMode": "true"}
    qs = urllib.parse.urlencode(url_params)
    url = f"{kibana_url.rstrip('/')}/api/saved_objects/_import?{qs}"

    with open(ndjson_path, "rb") as fh:
        file_bytes = fh.read()

    filename = os.path.basename(ndjson_path)
    body, ct = _encode_multipart(file_bytes, filename=filename)

    headers = {
        "kbn-xsrf": "true",
        "Content-Type": ct,
    }
    if auth_header:
        headers["Authorization"] = auth_header

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    if not verify_tls:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        )
    else:
        opener = urllib.request.build_opener()

    try:
        with opener.open(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")
        except Exception:
            pass
        return {
            "success": False,
            "success_count": 0,
            "error_count": 1,
            "errors": [],
            "raw_response": None,
            "http_error": f"HTTP {e.code}: {e.reason} — {body_text[:500]}",
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "success_count": 0,
            "error_count": 1,
            "errors": [],
            "raw_response": None,
            "http_error": f"URLError: {e.reason}",
        }

    # Parse Kibana's response
    success = raw.get("success", False)
    success_count = raw.get("successCount", 0)
    errors = raw.get("errors", [])
    error_count = len(errors)

    return {
        "success": success,
        "success_count": success_count,
        "error_count": error_count,
        "errors": errors,
        "raw_response": raw,
        "http_error": None,
    }


def _ndjson_files_in_dir(directory):
    """Return ordered list of NDJSON file paths in import order."""
    present = set(os.listdir(directory))
    files = []
    # Ordered first
    for name in _IMPORT_ORDER:
        if name in present:
            files.append(os.path.join(directory, name))
    # Then any remaining NDJSON files not in the explicit order
    for name in sorted(present):
        if name.endswith(".ndjson") and name not in _IMPORT_ORDER:
            files.append(os.path.join(directory, name))
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Import Sparrow WiFi dashboards into Kibana.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import all dashboards (overwrite existing):
  python install_dashboards.py --kibana-url http://kibana.example.com \\
      --username elastic --password elastic --overwrite

  # Import a single file:
  python install_dashboards.py --kibana-url http://kibana.example.com \\
      --username elastic --password elastic \\
      --file sparrow_elastic/dashboards/sparrow_wifi_situational_awareness.ndjson

  # Use API key auth:
  python install_dashboards.py --kibana-url https://kibana.example.com \\
      --api-key abc123== --overwrite --no-verify-tls
        """,
    )
    parser.add_argument(
        "--kibana-url",
        required=True,
        help="Kibana base URL, e.g. http://kibana.example.com",
    )
    parser.add_argument("--username", default=None, help="Basic auth username")
    parser.add_argument("--password", default=None, help="Basic auth password")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Kibana API key (alternative to --username/--password)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite existing saved objects (default: False)",
    )
    parser.add_argument(
        "--file",
        default=None,
        metavar="FILE",
        help="Import a single NDJSON file instead of all dashboards",
    )

    tls_group = parser.add_mutually_exclusive_group()
    tls_group.add_argument(
        "--verify-tls",
        dest="verify_tls",
        action="store_true",
        default=True,
        help="Verify TLS certificates (default)",
    )
    tls_group.add_argument(
        "--no-verify-tls",
        dest="verify_tls",
        action="store_false",
        help="Skip TLS certificate verification",
    )

    args = parser.parse_args()

    auth_header = _build_auth_header(args.username, args.password, args.api_key)

    # Determine which files to import
    if args.file:
        files = [os.path.abspath(args.file)]
        if not os.path.isfile(files[0]):
            print(f"ERROR: file not found: {files[0]}", file=sys.stderr)
            sys.exit(1)
    else:
        files = _ndjson_files_in_dir(DASHBOARDS_DIR)
        if not files:
            print(
                f"ERROR: no NDJSON files found under {DASHBOARDS_DIR}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(
        f"Kibana:  {args.kibana_url}\n"
        f"Files:   {len(files)}\n"
        f"Overwrite: {args.overwrite}\n"
        f"Auth:    {'api-key' if args.api_key else ('basic' if auth_header else 'none')}\n"
    )

    overall_ok = True
    total_success = 0
    total_errors = 0

    for path in files:
        filename = os.path.relpath(path)
        print(f"  Importing {filename} ...", end=" ", flush=True)

        result = import_file(
            args.kibana_url,
            path,
            overwrite=args.overwrite,
            auth_header=auth_header,
            verify_tls=args.verify_tls,
        )

        if result["http_error"]:
            print(f"FAILED ({result['http_error']})")
            overall_ok = False
            total_errors += 1
            continue

        ok = result["success"]
        sc = result["success_count"]
        ec = result["error_count"]
        total_success += sc
        total_errors += ec
        if ok:
            print(f"OK ({sc} objects imported)")
        else:
            overall_ok = False
            print(f"PARTIAL/FAILED ({sc} ok, {ec} errors)")
            for err in result["errors"][:10]:
                eid = err.get("id", "?")
                etype = err.get("type", "?")
                emsg = (err.get("error") or {}).get("message", str(err))
                print(f"    [{etype}/{eid}] {emsg}")
            if ec > 10:
                print(f"    ... and {ec - 10} more errors")

    print(
        f"\nSummary: {total_success} objects imported, "
        f"{total_errors} errors across {len(files)} file(s)"
    )

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
