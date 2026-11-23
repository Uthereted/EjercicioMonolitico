"""Generate the local teaching certificates used by Docker Compose."""
from datetime import datetime, timedelta, timezone
import argparse
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renew', action='store_true', help='Replace the existing local certificate set.')
    options = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'infrastructure/tls/generated'
    expected = ('ca.crt', 'server.crt', 'server.key', 'server.pem')
    if not options.renew and any((output / name).exists() for name in expected):
        if all((output / name).exists() for name in expected):
            print('Local TLS certificates already exist; kept unchanged.')
            return
        raise SystemExit('Incomplete certificate set; restore it before generating a new set.')
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Manufacturing Local CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False,
                                      content_commitment=False, data_encipherment=False,
                                      key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = ['localhost', 'host.docker.internal', 'haproxy', 'consul', 'server.dc1.consul',
             'rabbitmq', 'mongodb', 'order-service', 'client_service', 'client-service',
             'paymentservice', 'payment-service', 'delivery', 'delivery-service',
             'machineservice', 'machine-service', 'event-logger', 'log-aggregator']
    certificate = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
                   .issuer_name(ca.subject).public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName(
                       [x509.DNSName(name) for name in names] + [x509.IPAddress(ip_address('127.0.0.1'))]
                   ), critical=False)
                   .add_extension(x509.ExtendedKeyUsage([
                       ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH
                   ]), critical=False)
                   .sign(ca_key, hashes.SHA256()))
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    (output / 'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (output / 'server.crt').write_bytes(cert_pem)
    (output / 'server.key').write_bytes(key_pem)
    (output / 'server.pem').write_bytes(cert_pem + key_pem)
    print('Created local TLS certificates in', output)


if __name__ == '__main__':
    main()
