"""Certificate handling: content sniffing, normalisation, the on-disk store."""
import os
import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import (default_cfg, key_pem_encrypted, make_ca, make_cert, make_p12,  # noqa: E402
                         new_key, pem, write)

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from backend.tak import certs  # noqa: E402

CA = make_ca('Unit CA')
CA2 = make_ca('Other CA')
KEY, CERT = make_cert(CA, 'client-1', client=True)


class TestSniff(unittest.TestCase):

    def test_by_content_not_name(self):
        p12 = make_p12(KEY, CERT, [CA.cert], 'pw')
        self.assertEqual(certs.sniff(p12), 'p12')                  # even if named .pem
        self.assertEqual(certs.sniff(pem(CERT)), 'pem')            # even if named .p12
        self.assertEqual(certs.sniff(b'\n  ' + pem(CERT)), 'pem')
        self.assertEqual(certs.sniff(CERT.public_bytes(serialization.Encoding.DER)), 'der_cert')


class TestNormalizeClient(unittest.TestCase):

    def assert_valid(self, mat):
        self.assertIsInstance(mat, certs.ClientMaterial)
        certs_in = x509.load_pem_x509_certificate(mat.cert_chain_pem.split(b'-----END CERTIFICATE-----')[0]
                                                  + b'-----END CERTIFICATE-----\n')
        self.assertEqual(certs_in.subject, CERT.subject)         # leaf first
        self.assertTrue(certs.key_matches_cert(mat.cert_chain_pem, mat.key_pem))
        self.assertIn(b'BEGIN PRIVATE KEY', mat.key_pem)         # PKCS#8, unencrypted
        self.assertEqual(mat.subject, 'CN=client-1')
        self.assertEqual(mat.issuer, 'CN=Unit CA')
        self.assertGreater(mat.not_after, datetime.now(timezone.utc))

    def test_p12_with_and_without_password(self):
        self.assert_valid(certs.normalize_client(make_p12(KEY, CERT, [CA.cert], 'pw'), None, 'pw'))
        self.assert_valid(certs.normalize_client(make_p12(KEY, CERT, [CA.cert], ''), None, ''))

    def test_p12_chain_is_leaf_first(self):
        mat = certs.normalize_client(make_p12(KEY, CERT, [CA.cert], 'pw'), None, 'pw')
        self.assertEqual(mat.cert_chain_pem.count(b'BEGIN CERTIFICATE'), 2)

    def test_pem_pair(self):
        self.assert_valid(certs.normalize_client(pem(CERT), pem(KEY), ''))

    def test_pem_chain_ordering_leaf_first(self):
        bundle = pem(CA.cert) + pem(CERT)                           # leaf second in the file
        mat = certs.normalize_client(bundle, pem(KEY), '')
        first = x509.load_pem_x509_certificate(
            mat.cert_chain_pem[:mat.cert_chain_pem.index(b'-----END') + 25])
        self.assertEqual(first.subject, CERT.subject)
        self.assertEqual(mat.cert_chain_pem.count(b'BEGIN CERTIFICATE'), 2)

    def test_combined_pem(self):
        self.assert_valid(certs.normalize_client(pem(CERT) + pem(KEY), None, ''))

    def test_encrypted_pem_key(self):
        enc = key_pem_encrypted(KEY, 'secret')
        self.assert_valid(certs.normalize_client(pem(CERT), enc, 'secret'))
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_client(pem(CERT), enc, 'nope')
        self.assertIn('Wrong password', str(cm.exception))
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_client(pem(CERT), enc, '')
        self.assertIn('Wrong password', str(cm.exception))

    def test_der_cert_plus_pem_key(self):
        der = CERT.public_bytes(serialization.Encoding.DER)
        self.assert_valid(certs.normalize_client(der, pem(KEY), ''))

    def test_wrong_p12_password(self):
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_client(make_p12(KEY, CERT, [], 'right'), None, 'wrong')
        self.assertIn('Wrong password for the .p12 / key', str(cm.exception))

    def test_key_cert_mismatch(self):
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_client(pem(CERT), pem(new_key()), '')
        self.assertEqual(str(cm.exception), 'The private key does not match the certificate')

    def test_missing_key_and_garbage(self):
        with self.assertRaises(certs.CertError):
            certs.normalize_client(pem(CERT), None, '')
        with self.assertRaises(certs.CertError):
            certs.normalize_client(b'garbage bytes', None, '')
        with self.assertRaises(certs.CertError):
            certs.normalize_client(b'-----BEGIN NOTHING-----\nx\n-----END NOTHING-----\n', None, '')

    def test_p12_without_key_or_cert(self):
        trust = make_p12(None, None, [CA.cert], '')
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_client(trust, None, '')
        self.assertIn('no client certificate', str(cm.exception))


class TestNormalizeCa(unittest.TestCase):

    def test_cert_only_p12(self):
        mat = certs.normalize_ca(make_p12(None, None, [CA.cert, CA2.cert], 'atakatak'), 'atakatak')
        self.assertEqual(mat.count, 2)
        self.assertEqual(sorted(mat.subjects), ['CN=Other CA', 'CN=Unit CA'])
        self.assertEqual(mat.bundle_pem.count(b'BEGIN CERTIFICATE'), 2)

    def test_pem_bundle_two_certs(self):
        mat = certs.normalize_ca(pem(CA.cert) + b'\n# comment\n' + pem(CA2.cert), '')
        self.assertEqual(mat.count, 2)
        self.assertLess(mat.earliest_not_after, datetime.now(timezone.utc) + timedelta(days=4000))

    def test_der(self):
        mat = certs.normalize_ca(CA.cert.public_bytes(serialization.Encoding.DER), '')
        self.assertEqual((mat.count, mat.subjects), (1, ['CN=Unit CA']))

    def test_empty_and_wrong_password(self):
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_ca(b'-----BEGIN NOTHING-----\nx\n', '')
        self.assertEqual(str(cm.exception), 'No CA certificates found')
        with self.assertRaises(certs.CertError) as cm:
            certs.normalize_ca(make_p12(None, None, [CA.cert], 'a'), 'b')
        self.assertIn('Wrong password', str(cm.exception))

    def test_p12_with_cert_and_key_uses_cert_too(self):
        mat = certs.normalize_ca(make_p12(KEY, CERT, [CA.cert], ''), '')
        self.assertEqual(mat.count, 2)


class TestCertStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, 'tak_certs')
        self.store = certs.CertStore(self.root)

    def test_write_atomic_modes_and_no_temp_files(self):
        path = self.store.write_atomic('sub/dir/file.pem', b'data')
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.store.write_atomic('sub/dir/file.pem', b'newer')
        with open(path, 'rb') as fh:
            self.assertEqual(fh.read(), b'newer')
        leftovers = [n for _r, _d, files in os.walk(self.root) for n in files
                     if n.startswith('.tmp-')]
        self.assertEqual(leftovers, [])

    def test_store_client_and_info(self):
        mat = certs.normalize_client(make_p12(KEY, CERT, [CA.cert], 'pw'), None, 'pw')
        info = self.store.store_client(mat, source='upload', filename='c.p12', fmt='p12')
        self.assertEqual(info['subject'], 'CN=client-1')
        self.assertEqual(info['source'], 'upload')
        self.assertEqual(info['format'], 'p12')
        self.assertEqual(info['filename'], 'c.p12')
        self.assertEqual(info['chain_length'], 2)
        self.assertFalse(info['expired'])
        self.assertIn(info['days_left'], (364, 365))
        for name in ('client_cert.pem', 'client_key.pem', 'client_meta.json'):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, name)).st_mode), 0o600)
        self.assertTrue(self.store.delete_client())
        self.assertIsNone(self.store.client_info())
        self.assertFalse(self.store.delete_client())

    def test_expired_client_reports_expired(self):
        key, cert = make_cert(CA, 'old', client=True, days=-1)
        mat = certs.normalize_client(pem(cert), pem(key), '')
        info = self.store.store_client(mat, source='upload', filename='', fmt='pem')
        self.assertTrue(info['expired'])
        self.assertLess(info['days_left'], 0)

    def test_store_ca_info(self):
        mat = certs.normalize_ca(pem(CA.cert) + pem(CA2.cert), '')
        info = self.store.store_ca(mat, source='package', filename='ts.p12', fmt='p12')
        self.assertEqual(info['count'], 2)
        self.assertEqual(info['source'], 'package')
        self.assertEqual(sorted(info['subjects']), ['CN=Other CA', 'CN=Unit CA'])
        self.assertTrue(info['earliest_not_after'].endswith('Z'))
        self.assertTrue(self.store.delete_ca())
        self.assertIsNone(self.store.ca_info())

    def test_path_mode_rebuilds_only_when_source_changes(self):
        cert_path = write(os.path.join(self.tmp.name, 'c.p12'), make_p12(KEY, CERT, [CA.cert], 'pw'))
        cfg = default_cfg(tak_tls_client_cert_path=cert_path, tak_tls_client_password='pw')
        cert_file, key_file = self.store.resolve_path_material(cfg)
        self.assertTrue(cert_file.endswith('runtime/client_cert.pem'))
        self.assertEqual(stat.S_IMODE(os.stat(key_file).st_mode), 0o600)
        mtime = os.stat(cert_file).st_mtime_ns
        time.sleep(0.01)
        self.store.resolve_path_material(cfg)
        self.assertEqual(os.stat(cert_file).st_mtime_ns, mtime)      # not rebuilt
        # replace the source with a different cert (size / mtime change) -> rebuilt
        key2, cert2 = make_cert(CA, 'client-2', client=True)
        write(cert_path, make_p12(key2, cert2, [], 'pw'))
        self.store.resolve_path_material(cfg)
        self.assertGreater(os.stat(cert_file).st_mtime_ns, mtime)
        with open(cert_file, 'rb') as fh:
            leaf = x509.load_pem_x509_certificate(fh.read())
        self.assertEqual(leaf.subject.rfc4514_string(), 'CN=client-2')
        # a password change rebuilds too (and fails loudly when wrong)
        bad = default_cfg(tak_tls_client_cert_path=cert_path, tak_tls_client_password='nope')
        with self.assertRaises(certs.CertError):
            self.store.resolve_path_material(bad)

    def test_path_mode_pem_pair_and_missing_file(self):
        cpath = write(os.path.join(self.tmp.name, 'c.pem'), pem(CERT))
        kpath = write(os.path.join(self.tmp.name, 'k.pem'), pem(KEY))
        cfg = default_cfg(tak_tls_client_cert_path=cpath, tak_tls_client_key_path=kpath)
        self.store.resolve_path_material(cfg)
        gone = default_cfg(tak_tls_client_cert_path=os.path.join(self.tmp.name, 'none.pem'))
        with self.assertRaises(certs.CertError) as cm:
            self.store.resolve_path_material(gone)
        self.assertIn('File not found', str(cm.exception))

    def test_path_ca(self):
        ca_path = write(os.path.join(self.tmp.name, 'ca.p12'),
                        make_p12(None, None, [CA.cert], 'atakatak'))
        cfg = default_cfg(tak_tls_ca_mode='path', tak_tls_ca_path=ca_path,
                          tak_tls_ca_password='atakatak')
        ca_file = self.store.resolve_path_ca(cfg)
        with open(ca_file, 'rb') as fh:
            self.assertEqual(fh.read().count(b'BEGIN CERTIFICATE'), 1)


class TestResolveMaterials(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = certs.CertStore(os.path.join(self.tmp.name, 'c'))

    def test_upload_mode_needs_files(self):
        cfg = default_cfg(tak_transport='tls', cot_address='h.example')
        with self.assertRaises(certs.CertError) as cm:
            certs.resolve_tls_materials(cfg, self.store)
        self.assertEqual(str(cm.exception), 'No client certificate uploaded')
        mat = certs.normalize_client(pem(CERT), pem(KEY), '')
        self.store.store_client(mat, source='upload', filename='', fmt='pem')
        mats = certs.resolve_tls_materials(cfg, self.store)
        self.assertEqual(mats.subject, 'CN=client-1')
        self.assertIsNone(mats.ca_file)

    def test_ca_upload_mode_missing(self):
        mat = certs.normalize_client(pem(CERT), pem(KEY), '')
        self.store.store_client(mat, source='upload', filename='', fmt='pem')
        cfg = default_cfg(tak_transport='tls', cot_address='h.example', tak_tls_ca_mode='upload')
        with self.assertRaises(certs.CertError) as cm:
            certs.resolve_tls_materials(cfg, self.store)
        self.assertEqual(str(cm.exception), 'No CA truststore uploaded')

    def test_enroll_mode_without_manager(self):
        cfg = default_cfg(tak_transport='tls', cot_address='h.example', tak_tls_client_mode='enroll')
        with self.assertRaises(certs.CertError):
            certs.resolve_tls_materials(cfg, self.store, None)


if __name__ == '__main__':
    unittest.main()
