"""Self-signed TLS certificate for the campus server's HTTPS port.

Why the campus half needs HTTPS at all: browsers only hand a web page the
camera (getUserMedia) on a *secure context* - https://, or http://localhost.
The campus server is reached at http://<LAN IP>:8000, which is neither, so a
guard terminal or phone opening it can never scan a QR badge or slip with its
camera. Railway is already https and unaffected.

The launcher's own kiosk window does not need this (it starts Chrome/Edge with
the LAN origin marked secure). This certificate is for every OTHER device -
another gate PC, a phone - which opens https://<LAN IP>:8443 and accepts the
certificate warning once.

Usage: python campus-tls-cert.py <out_dir> <lan_ip>
Prints "reused" or "created". Re-issues when the certificate is missing, is
within 30 days of expiry, or does not name the current LAN address (a new DHCP
lease would otherwise make every browser reject it as the wrong host).
"""
import datetime
import ipaddress
import os
import socket
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _names(lan):
    ips = {ipaddress.ip_address('127.0.0.1')}
    try:
        ips.add(ipaddress.ip_address(lan))
    except ValueError:
        pass
    dns = {'localhost', socket.gethostname()}
    return ips, dns


def _still_good(cert_path, key_path, ips):
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        return False
    try:
        with open(cert_path, 'rb') as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = set(san.get_values_for_type(x509.IPAddress))
        left = cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
        return ips <= have and left > datetime.timedelta(days=30)
    except Exception:
        return False


def main():
    out_dir, lan = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    cert_path = os.path.join(out_dir, 'cert.pem')
    key_path = os.path.join(out_dir, 'key.pem')
    ips, dns = _names(lan)

    if _still_good(cert_path, key_path, ips):
        print('reused')
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Saint Louis College CDSO'),
        x509.NameAttribute(NameOID.COMMON_NAME, f'SLC-VMS campus server {lan}'),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        # 825 days: the longest lifetime browsers accept for a server certificate.
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(
            [x509.DNSName(d) for d in sorted(dns)] + [x509.IPAddress(i) for i in sorted(ips)]),
            critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, 'wb') as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    with open(cert_path, 'wb') as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    print('created')


if __name__ == '__main__':
    main()
