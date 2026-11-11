"""
Safe TAK data-package (.zip) parser.

A data package is an untrusted upload. This module reads it entirely in memory
(``zipfile`` over ``BytesIO``): nothing is ever extracted, and member names are
only compared as normalised basenames, never used as paths.
"""
import io
import ipaddress
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional

MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 32
MAX_MEMBER_BYTES = 1 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 4 * 1024 * 1024
MAX_RATIO = 100            # uncompressed / compressed, for members > 1 KiB


class PackageError(ValueError):
    """An operator-facing package problem."""


@dataclass
class PackageContents:
    kind: str                     # 'enrollment' | 'certificate' | 'truststore_only'
    host: str
    port: int
    transport: str                # from connectString0
    description: str
    ca_bytes: Optional[bytes]
    ca_name: str
    ca_password: str
    client_bytes: Optional[bytes]
    client_name: str
    client_password: str
    enroll: bool
    use_auth: bool
    warnings: List[str] = field(default_factory=list)


def _basename(name: str) -> str:
    return PurePosixPath(name.replace('\\', '/')).name


def _unsafe_name(name: str) -> bool:
    norm = name.replace('\\', '/')
    return ('\x00' in name or norm.startswith('/') or re.match(r'^[A-Za-z]:', norm) is not None
            or '..' in norm.split('/'))


def _reject_dtd(data: bytes, what: str) -> None:
    low = data.lower()
    if b'<!doctype' in low or b'<!entity' in low:
        raise PackageError('%s contains a DTD / entity declaration, which is not accepted' % what)


def _parse_xml(data: bytes, what: str):
    _reject_dtd(data, what)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PackageError('%s is not valid XML: %s' % (what, exc))


def parse_package(data: bytes) -> PackageContents:
    """Parse a TAK data package. Raises PackageError with an operator-facing message."""
    if len(data) > MAX_ZIP_BYTES:
        raise PackageError('Package is too large (max %d MiB)' % (MAX_ZIP_BYTES // (1024 * 1024)))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError('Not a zip file')
    try:
        return _parse(zf)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as exc:
        raise PackageError('The package is corrupt or uses an unsupported zip feature: %s' % exc)
    finally:
        zf.close()


def _parse(zf: zipfile.ZipFile) -> PackageContents:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise PackageError('Package has too many files (max %d)' % MAX_MEMBERS)
    total = 0
    for info in infos:
        if '\x00' in info.filename:
            raise PackageError('Package contains an invalid file name')
        if info.flag_bits & 0x1:
            raise PackageError('Package contains encrypted files, which are not supported')
        if info.file_size > MAX_MEMBER_BYTES:
            raise PackageError('Package file %s is too large' % _basename(info.filename))
        total += info.file_size
        if info.file_size > 1024 and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
            raise PackageError('Package file %s has a suspicious compression ratio'
                               % _basename(info.filename))
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise PackageError('Package is too large when unpacked')

    warnings: List[str] = []
    by_base: Dict[str, list] = {}
    for info in infos:
        if info.filename.endswith('/') or info.filename.endswith('\\'):
            continue
        if _unsafe_name(info.filename):
            warnings.append('Ignored a file with an unsafe path: %s'
                            % _basename(info.filename)[:64])
            continue
        by_base.setdefault(_basename(info.filename), []).append(info)

    def read(info: zipfile.ZipInfo) -> bytes:
        with zf.open(info) as fh:
            content = fh.read(MAX_MEMBER_BYTES + 1)
        if len(content) > MAX_MEMBER_BYTES:
            raise PackageError('Package file %s is too large' % _basename(info.filename))
        return content

    # --- config.pref --------------------------------------------------------
    pref_infos = [i for b, lst in by_base.items() if b.lower() == 'config.pref' for i in lst]
    if not pref_infos:
        pref_infos = [i for b, lst in by_base.items() if b.lower().endswith('.pref')
                      for i in lst]
        if len(pref_infos) != 1:
            raise PackageError('No config.pref in the package')
    elif len(pref_infos) > 1:
        raise PackageError('Ambiguous file config.pref')
    pref_root = _parse_xml(read(pref_infos[0]), 'config.pref')

    entries: Dict[str, str] = {}
    for pref in pref_root.iter('preference'):
        for entry in pref.iter('entry'):
            key = entry.get('key')
            if key and key not in entries:
                entries[key] = (entry.text or '').strip()

    # --- optional manifest (well-formedness only) ------------------------------
    for base, lst in by_base.items():
        if base.lower() == 'manifest.xml':
            _parse_xml(read(lst[0]), 'MANIFEST/manifest.xml')
            break

    def indexed(name: str, fallback: bool = False) -> str:
        v = entries.get(name + '0', '')
        if not v and fallback:
            v = entries.get(name, '')
        return v

    # --- connection ---------------------------------------------------------
    conn = entries.get('connectString0', '')
    if not conn:
        raise PackageError('The package has no server connection (connectString0)')
    try:
        host, port_s, proto = conn.rsplit(':', 2)
        port = int(port_s)
    except ValueError:
        raise PackageError('Unreadable server connection in the package: %s' % conn[:80])
    host = host.strip('[]')
    if not host or not 1 <= port <= 65535:
        raise PackageError('Unreadable server connection in the package: %s' % conn[:80])
    proto = proto.strip().lower()
    if proto == 'ssl':
        transport = 'tls'
    elif proto == 'tcp':
        transport = 'tcp'
    elif proto == 'udp':
        try:
            multicast = ipaddress.IPv4Address(host).is_multicast
        except ValueError:
            multicast = False
        transport = 'udp_multicast' if multicast else 'udp_unicast'
    else:
        raise PackageError('Unsupported connection protocol in the package: %s' % proto[:20])
    count = entries.get('count', '1')
    try:
        if int(count) > 1:
            warnings.append('The package has %s connections; imported the first' % count)
    except ValueError:
        pass

    def resolve(location: str) -> Optional[bytes]:
        if not location:
            return None
        base = _basename(location)
        matches = by_base.get(base, [])
        if not matches:
            raise PackageError('Package references %s but does not contain it' % base)
        if len(matches) > 1:
            raise PackageError('Ambiguous file %s' % base)
        return read(matches[0])

    ca_loc = indexed('caLocation', fallback=True)
    ca_pass = indexed('caPassword', fallback=True)
    ca_bytes = resolve(ca_loc)
    client_loc = indexed('certificateLocation', fallback=True)
    client_bytes = resolve(client_loc)
    client_pass = indexed('clientPassword', fallback=True) or ca_pass

    enroll = indexed('enrollForCertificateWithTrust').lower() == 'true'
    if enroll:
        kind = 'enrollment'
    elif client_bytes is not None:
        kind = 'certificate'
    else:
        kind = 'truststore_only'
        warnings.append('No client certificate and no enrollment in this package')
    description = indexed('description') or 'TAK %s' % host
    return PackageContents(
        kind=kind, host=host, port=port, transport=transport, description=description[:80],
        ca_bytes=ca_bytes, ca_name=_basename(ca_loc) if ca_loc else '', ca_password=ca_pass,
        client_bytes=client_bytes, client_name=_basename(client_loc) if client_loc else '',
        client_password=client_pass, enroll=enroll,
        use_auth=indexed('useAuth').lower() == 'true', warnings=warnings)
