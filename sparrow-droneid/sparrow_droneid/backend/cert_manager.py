"""
Certificate Manager for Sparrow DroneID.

Manages TLS certificates stored in the {data_dir}/certs/ directory.
Uses the system `openssl` binary for all certificate operations to avoid
requiring the `cryptography` pip package.
"""
import os
import re
import shutil
import subprocess
from typing import Optional


def _openssl_available() -> bool:
    """Return True if openssl is on PATH."""
    return shutil.which('openssl') is not None


def _run_openssl(*args: str, input_text: str = None) -> subprocess.CompletedProcess:
    """Run an openssl command and return the CompletedProcess.

    Raises RuntimeError if openssl is not available or the command fails.
    """
    if not _openssl_available():
        raise RuntimeError(
            "openssl is not installed or not on PATH. "
            "Install it with: apt-get install openssl"
        )
    cmd = ['openssl'] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
    )
    return result


def _sanitize_name(name: str) -> str:
    """Convert a string to a safe filename by replacing non-alphanumeric chars."""
    return re.sub(r'[^\w.-]', '_', name).strip('_') or 'cert'


def _parse_openssl_output(cert_path: str) -> dict:
    """Run `openssl x509` to extract cert fields. Returns a dict of raw strings."""
    result = _run_openssl(
        'x509', '-in', cert_path, '-noout',
        '-subject', '-issuer', '-serial', '-dates', '-fingerprint',
        '-sha256',
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"openssl failed to parse cert {cert_path!r}: {result.stderr.strip()}"
        )

    fields: dict = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith('subject='):
            fields['subject'] = line[len('subject='):]
        elif line.startswith('issuer='):
            fields['issuer'] = line[len('issuer='):]
        elif line.startswith('serial='):
            fields['serial'] = line[len('serial='):]
        elif line.startswith('notBefore='):
            fields['not_before'] = line[len('notBefore='):]
        elif line.startswith('notAfter='):
            fields['not_after'] = line[len('notAfter='):]
        elif line.lower().startswith('sha256 fingerprint='):
            # e.g. "SHA256 Fingerprint=AA:BB:..."
            fields['fingerprint_sha256'] = line.split('=', 1)[1].strip()
    return fields


def _extract_cn(subject: str) -> str:
    """Extract CN value from an openssl subject string like 'CN = example.com'."""
    m = re.search(r'CN\s*=\s*([^,/\n]+)', subject)
    if m:
        return m.group(1).strip()
    return subject


def _build_cert_info(name: str, cert_path: str, certs_dir: str) -> dict:
    """Build a full cert info dict for the named cert file."""
    key_path = os.path.join(certs_dir, name + '.key')
    csr_path = os.path.join(certs_dir, name + '.csr')

    fields = _parse_openssl_output(cert_path)

    subject = fields.get('subject', '')
    issuer = fields.get('issuer', '')
    common_name = _extract_cn(subject)

    # A cert is self-signed when subject == issuer
    is_self_signed = subject.strip() == issuer.strip()

    return {
        'name':             name,
        'common_name':      common_name,
        'subject':          subject,
        'issuer':           issuer,
        'serial':           fields.get('serial', ''),
        'not_before':       fields.get('not_before', ''),
        'not_after':        fields.get('not_after', ''),
        'is_self_signed':   is_self_signed,
        'has_key':          os.path.isfile(key_path),
        'has_csr':          os.path.isfile(csr_path),
        'fingerprint_sha256': fields.get('fingerprint_sha256', ''),
    }


class CertManager:
    """Manages TLS certificates in a dedicated directory.

    All certificates are stored as PEM files:
      {name}.pem  — the certificate
      {name}.key  — the private key (when available)
      {name}.csr  — the certificate signing request (when in CSR workflow)
    """

    def __init__(self, certs_dir: str) -> None:
        """
        Args:
            certs_dir: Directory where cert files are stored.
                       Typically {data_dir}/certs.  Created if absent.
        """
        self._certs_dir = certs_dir
        os.makedirs(certs_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # List / inspect
    # ------------------------------------------------------------------

    def list_certs(self) -> list:
        """Return a list of cert info dicts for every .pem file in certs_dir."""
        certs = []
        try:
            entries = os.listdir(self._certs_dir)
        except OSError:
            return []

        for entry in sorted(entries):
            if not entry.endswith('.pem'):
                continue
            name = entry[:-4]  # strip .pem
            cert_path = os.path.join(self._certs_dir, entry)
            try:
                info = _build_cert_info(name, cert_path, self._certs_dir)
                certs.append(info)
            except Exception as e:
                # Include broken certs in list with an error marker
                certs.append({
                    'name':    name,
                    'error':   str(e),
                    'has_key': os.path.isfile(
                        os.path.join(self._certs_dir, name + '.key')
                    ),
                    'has_csr': os.path.isfile(
                        os.path.join(self._certs_dir, name + '.csr')
                    ),
                })
        return certs

    def get_cert_info(self, name: str) -> dict:
        """Return detailed cert info dict for the named cert.

        Raises FileNotFoundError if {name}.pem does not exist.
        Raises RuntimeError if openssl cannot parse the cert.
        """
        cert_path = os.path.join(self._certs_dir, name + '.pem')
        if not os.path.isfile(cert_path):
            raise FileNotFoundError(
                f"Certificate {name!r} not found in {self._certs_dir}"
            )
        return _build_cert_info(name, cert_path, self._certs_dir)

    def get_cert_path(self, name: str) -> tuple:
        """Return (cert_path, key_path) for the named cert.

        Raises FileNotFoundError if either .pem or .key is missing.
        """
        cert_path = os.path.join(self._certs_dir, name + '.pem')
        key_path = os.path.join(self._certs_dir, name + '.key')
        if not os.path.isfile(cert_path):
            raise FileNotFoundError(
                f"Certificate file not found: {cert_path}"
            )
        if not os.path.isfile(key_path):
            raise FileNotFoundError(
                f"Private key file not found: {key_path}"
            )
        return cert_path, key_path

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def generate_self_signed(
        self,
        common_name: str,
        days: int = 365,
        key_size: int = 2048,
    ) -> dict:
        """Generate a self-signed certificate and private key.

        Args:
            common_name: Certificate CN (e.g. "localhost" or "myserver.local").
            days:        Validity period in days.
            key_size:    RSA key size in bits (2048 or 4096 recommended).

        Returns:
            Cert info dict (same schema as get_cert_info()).

        Raises:
            RuntimeError: if openssl is unavailable or generation fails.
        """
        safe_name = _sanitize_name(common_name)
        cert_path = os.path.join(self._certs_dir, safe_name + '.pem')
        key_path = os.path.join(self._certs_dir, safe_name + '.key')

        result = _run_openssl(
            'req', '-x509',
            '-newkey', f'rsa:{key_size}',
            '-keyout', key_path,
            '-out', cert_path,
            '-days', str(days),
            '-nodes',
            '-subj', f'/CN={common_name}',
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to generate self-signed cert: {result.stderr.strip()}"
            )

        return _build_cert_info(safe_name, cert_path, self._certs_dir)

    def generate_csr(
        self,
        common_name: str,
        organization: str = '',
        country: str = '',
        key_size: int = 2048,
    ) -> dict:
        """Generate a CSR and private key.

        Args:
            common_name:  Certificate CN field.
            organization: O field (optional).
            country:      C field — must be a 2-letter ISO code (optional).
            key_size:     RSA key size in bits.

        Returns:
            Dict with keys: name, csr_path, key_path, csr_text.

        Raises:
            RuntimeError: if openssl is unavailable or generation fails.
        """
        safe_name = _sanitize_name(common_name)
        csr_path = os.path.join(self._certs_dir, safe_name + '.csr')
        key_path = os.path.join(self._certs_dir, safe_name + '.key')

        # Build subject string — only include non-empty fields
        subj_parts = [f'CN={common_name}']
        if organization:
            subj_parts.append(f'O={organization}')
        if country:
            subj_parts.append(f'C={country}')
        subj = '/' + '/'.join(subj_parts)

        result = _run_openssl(
            'req', '-new',
            '-newkey', f'rsa:{key_size}',
            '-keyout', key_path,
            '-out', csr_path,
            '-nodes',
            '-subj', subj,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to generate CSR: {result.stderr.strip()}"
            )

        # Read CSR text so the UI can display it
        try:
            with open(csr_path, 'r') as fh:
                csr_text = fh.read()
        except OSError as e:
            csr_text = ''

        return {
            'name':     safe_name,
            'csr_path': csr_path,
            'key_path': key_path,
            'csr_text': csr_text,
        }

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_cert(
        self,
        name: str,
        cert_pem: str,
        key_pem: Optional[str] = None,
    ) -> dict:
        """Import a PEM certificate (and optional private key).

        If a matching .csr file already exists, this completes the CSR flow.

        Args:
            name:     Filename stem (will be sanitized).
            cert_pem: PEM-encoded certificate text.
            key_pem:  PEM-encoded private key text (optional).

        Returns:
            Cert info dict.

        Raises:
            RuntimeError: if the cert PEM is not parseable by openssl.
            ValueError:   if cert_pem is empty.
        """
        if not cert_pem or not cert_pem.strip():
            raise ValueError("cert_pem must not be empty")

        safe_name = _sanitize_name(name)
        cert_path = os.path.join(self._certs_dir, safe_name + '.pem')
        key_path = os.path.join(self._certs_dir, safe_name + '.key')

        # Write the cert
        try:
            with open(cert_path, 'w') as fh:
                fh.write(cert_pem)
        except OSError as e:
            raise RuntimeError(f"Failed to write cert file: {e}") from e

        # Validate via openssl (raises RuntimeError if invalid)
        validate_result = _run_openssl('x509', '-in', cert_path, '-noout')
        if validate_result.returncode != 0:
            # Remove the invalid file before raising
            try:
                os.remove(cert_path)
            except OSError:
                pass
            raise RuntimeError(
                f"Cert PEM is not valid: {validate_result.stderr.strip()}"
            )

        # Write the key if provided
        if key_pem and key_pem.strip():
            try:
                with open(key_path, 'w') as fh:
                    fh.write(key_pem)
            except OSError as e:
                raise RuntimeError(f"Failed to write key file: {e}") from e

        return _build_cert_info(safe_name, cert_path, self._certs_dir)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_cert(self, name: str) -> bool:
        """Remove all files (.pem, .key, .csr) for the named cert.

        Returns:
            True if at least one file was deleted, False if nothing existed.
        """
        deleted = False
        for ext in ('.pem', '.key', '.csr'):
            path = os.path.join(self._certs_dir, name + ext)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    deleted = True
                except OSError:
                    pass
        return deleted
