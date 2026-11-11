"""TAK data-package parsing (in memory, never extracted) and the import API path."""
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import (build_zip, make_ca, make_cert, make_p12,  # noqa: E402
                         patch_central_directory)

from backend.tak import package  # noqa: E402
from backend.tak.package import PackageError, parse_package  # noqa: E402

CA = make_ca('Pkg CA')
CKEY, CCERT = make_cert(CA, 'pkguser', client=True)
TRUST = make_p12(None, None, [CA.cert], 'atakatak')
CLIENT = make_p12(CKEY, CCERT, [CA.cert], 'clientpw')


def pref(entries, extra_prefs=''):
    rows = ''.join('<entry key="%s" class="class java.lang.String">%s</entry>' % kv
                   for kv in entries)
    return ("<?xml version='1.0' encoding='ASCII' standalone='yes'?><preferences>"
            '<preference version="1" name="cot_streams">%s</preference>%s</preferences>'
            % (rows, extra_prefs)).encode()


ENROLL_ENTRIES = [
    ('count', '1'), ('description0', 'Lab TAK'), ('enabled0', 'true'),
    ('connectString0', '127.0.0.1:8089:ssl'), ('caLocation0', 'cert/truststore-root.p12'),
    ('caPassword0', 'atakatak'), ('enrollForCertificateWithTrust0', 'true'),
    ('useAuth0', 'true'), ('cacheCredentials0', 'true')]


def enroll_zip(**over):
    members = {'MANIFEST/manifest.xml': b'<MissionPackageManifest version="2"/>',
               'config.pref': pref(ENROLL_ENTRIES), 'truststore-root.p12': TRUST}
    members.update(over)
    return build_zip(members)


class TestLayouts(unittest.TestCase):

    def test_atak_enrollment_package(self):
        pc = parse_package(enroll_zip())
        self.assertEqual((pc.kind, pc.host, pc.port, pc.transport),
                         ('enrollment', '127.0.0.1', 8089, 'tls'))
        self.assertEqual(pc.description, 'Lab TAK')
        self.assertEqual(pc.ca_bytes, TRUST)
        self.assertEqual((pc.ca_name, pc.ca_password), ('truststore-root.p12', 'atakatak'))
        self.assertIsNone(pc.client_bytes)
        self.assertTrue(pc.enroll)
        self.assertTrue(pc.use_auth)
        self.assertEqual(pc.warnings, [])

    def test_certificate_package(self):
        entries = [e for e in ENROLL_ENTRIES if e[0] != 'enrollForCertificateWithTrust0'] + [
            ('certificateLocation0', 'cert/user.p12'), ('clientPassword0', 'clientpw')]
        pc = parse_package(build_zip({'config.pref': pref(entries), 'truststore-root.p12': TRUST,
                                      'user.p12': CLIENT}))
        self.assertEqual(pc.kind, 'certificate')
        self.assertEqual(pc.client_bytes, CLIENT)
        self.assertEqual(pc.client_password, 'clientpw')
        self.assertFalse(pc.enroll)

    def test_itak_layout_unindexed_keys_and_password_fallback(self):
        main = pref([('count', '1'), ('connectString0', 'tak.example.org:8089:ssl'),
                     ('description0', 'iTAK')])
        app = ('<preference version="1" name="com.atakmap.app_preferences">'
               '<entry key="caLocation" class="class java.lang.String">cert/ca.p12</entry>'
               '<entry key="caPassword" class="class java.lang.String">atakatak</entry>'
               '<entry key="certificateLocation" class="class java.lang.String">cert/me.p12</entry>'
               '</preference>')
        data = pref([('count', '1'), ('connectString0', 'tak.example.org:8089:ssl'),
                     ('description0', 'iTAK')], extra_prefs=app)
        pc = parse_package(build_zip({'config.pref': data, 'ca.p12': TRUST, 'me.p12': CLIENT}))
        self.assertEqual(pc.kind, 'certificate')
        self.assertEqual(pc.ca_password, 'atakatak')
        self.assertEqual(pc.client_password, 'atakatak')       # falls back to the CA password
        self.assertEqual(pc.host, 'tak.example.org')

    def test_files_in_subfolder_resolve_by_basename(self):
        pc = parse_package(enroll_zip(**{'certs/deep/truststore-root.p12': TRUST}
                                      ) if False else build_zip({
            'config.pref': pref(ENROLL_ENTRIES), 'certs/deep/truststore-root.p12': TRUST}))
        self.assertEqual(pc.ca_bytes, TRUST)

    def test_pref_found_when_not_named_config_pref(self):
        pc = parse_package(build_zip({'tak.pref': pref(ENROLL_ENTRIES),
                                      'truststore-root.p12': TRUST}))
        self.assertEqual(pc.kind, 'enrollment')

    def test_truststore_only(self):
        entries = [e for e in ENROLL_ENTRIES if e[0] != 'enrollForCertificateWithTrust0']
        pc = parse_package(build_zip({'config.pref': pref(entries), 'truststore-root.p12': TRUST}))
        self.assertEqual(pc.kind, 'truststore_only')
        self.assertIn('No client certificate and no enrollment in this package', pc.warnings)

    def test_connect_strings(self):
        def kind(conn):
            entries = [('connectString0', conn)]
            return parse_package(build_zip({'config.pref': pref(entries)}))
        self.assertEqual(kind('h.example:8087:tcp').transport, 'tcp')
        self.assertEqual(kind('239.2.3.1:6969:udp').transport, 'udp_multicast')
        self.assertEqual(kind('10.0.0.9:4242:udp').transport, 'udp_unicast')
        self.assertEqual(kind('[::1]:8089:ssl').host, '::1')
        for bad in ('h:8089:quic', 'h:99999:ssl', 'nonsense', 'h:abc:ssl'):
            with self.assertRaises(PackageError, msg=bad):
                kind(bad)
        with self.assertRaises(PackageError):
            parse_package(build_zip({'config.pref': pref([])}))

    def test_multiple_connections_warning(self):
        entries = [('count', '2')] + ENROLL_ENTRIES[1:]
        pc = parse_package(build_zip({'config.pref': pref(entries), 'truststore-root.p12': TRUST}))
        self.assertIn('The package has 2 connections; imported the first', pc.warnings)

    def test_manifest_optional_but_must_be_well_formed(self):
        parse_package(build_zip({'config.pref': pref(ENROLL_ENTRIES), 'truststore-root.p12': TRUST}))
        with self.assertRaises(PackageError):
            parse_package(enroll_zip(**{'MANIFEST/manifest.xml': b'<unclosed'}))


class TestMalicious(unittest.TestCase):

    def test_traversal_and_absolute_names_ignored_with_warning_and_nothing_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            cwd_before = sorted(os.listdir('.'))
            pc = parse_package(build_zip({
                'config.pref': pref(ENROLL_ENTRIES), 'truststore-root.p12': TRUST,
                '../../etc/passwd': b'root', '/abs/evil': b'x', 'C:\\win\\evil': b'x'}))
            self.assertEqual(pc.kind, 'enrollment')
            self.assertEqual(len([w for w in pc.warnings if 'unsafe path' in w]), 3)
            self.assertEqual(sorted(os.listdir(tmp)), before)
            self.assertEqual(sorted(os.listdir('.')), cwd_before)

    def test_unsafe_file_is_never_used_for_a_reference(self):
        entries = [e if e[0] != 'caLocation0' else ('caLocation0', '../../evil.p12')
                   for e in ENROLL_ENTRIES]
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'config.pref': pref(entries), '../../evil.p12': TRUST}))
        self.assertIn('does not contain it', str(cm.exception))

    def test_duplicate_basenames_are_ambiguous(self):
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'config.pref': pref(ENROLL_ENTRIES),
                                     'a/truststore-root.p12': TRUST,
                                     'b/truststore-root.p12': TRUST}))
        self.assertIn('Ambiguous file truststore-root.p12', str(cm.exception))

    def test_too_many_members(self):
        members = {'f%d.txt' % i: b'x' for i in range(package.MAX_MEMBERS)}
        members['config.pref'] = pref(ENROLL_ENTRIES)
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip(members))
        self.assertIn('too many files', str(cm.exception))

    def test_encrypted_flag(self):
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'config.pref': pref(ENROLL_ENTRIES),
                                     'truststore-root.p12': TRUST},
                                    encrypted_flag_on='truststore-root.p12'))
        self.assertIn('encrypted', str(cm.exception))

    def test_declared_size_over_cap(self):
        big = os.urandom(package.MAX_MEMBER_BYTES + 10)
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'config.pref': pref(ENROLL_ENTRIES), 'big.bin': big},
                                    compression=zipfile.ZIP_STORED))
        self.assertIn('too large', str(cm.exception))

    def test_lying_header_is_rejected(self):
        # The central directory claims a smaller size than the data really has,
        # for a member the import needs (the truststore).
        data = bytearray(build_zip({'config.pref': pref(ENROLL_ENTRIES),
                                    'truststore-root.p12': TRUST},
                                   compression=zipfile.ZIP_STORED))
        patch_central_directory(data, 'truststore-root.p12', file_size=100, compress_size=100)
        with self.assertRaises(PackageError):
            parse_package(bytes(data))

    def test_ratio_bomb(self):
        bomb = b'\x00' * (1024 * 1024)
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'config.pref': pref(ENROLL_ENTRIES), 'bomb.bin': bomb}))
        self.assertIn('compression ratio', str(cm.exception))

    def test_total_size_over_cap(self):
        members = {'config.pref': pref(ENROLL_ENTRIES)}
        for i in range(5):
            members['r%d.bin' % i] = os.urandom(300 * 1024) + b'\0' * (600 * 1024)
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip(members))
        self.assertIn('too large when unpacked', str(cm.exception))

    def test_doctype_and_entity_rejected(self):
        for payload in (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><preferences/>',
                        b'<!doctype html><preferences/>'):
            with self.assertRaises(PackageError) as cm:
                parse_package(build_zip({'config.pref': payload}))
            self.assertIn('DTD', str(cm.exception))

    def test_missing_pref_missing_file_not_zip_oversized(self):
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'readme.txt': b'hi'}))
        self.assertEqual(str(cm.exception), 'No config.pref in the package')
        entries = [e for e in ENROLL_ENTRIES]
        with self.assertRaises(PackageError) as cm:
            parse_package(build_zip({'config.pref': pref(entries)}))
        self.assertEqual(str(cm.exception),
                         'Package references truststore-root.p12 but does not contain it')
        with self.assertRaises(PackageError) as cm:
            parse_package(b'definitely not a zip')
        self.assertEqual(str(cm.exception), 'Not a zip file')
        with self.assertRaises(PackageError):
            parse_package(b'\x00' * (package.MAX_ZIP_BYTES + 1))


if __name__ == '__main__':
    unittest.main()
