"""Read-only TLS checks against the running Compose stack.

PowerShell, from the repository root:
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" --entrypoint python order-service /verification/tests/test_tls.py
"""
import csv
import io
import os
import socket
import ssl
import unittest

import httpx
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError


SERVICES = ('order-service', 'client_service', 'paymentservice', 'delivery',
            'machineservice', 'event-logger', 'log-aggregator')


class TLSChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = ssl.create_default_context(cafile=os.environ['TLS_CA_FILE'])
        cls.http = httpx.Client(verify=cls.context, trust_env=False, timeout=5)

    @classmethod
    def tearDownClass(cls):
        cls.http.close()

    def test_https_apis_gateway_and_management(self):
        urls = [f'https://{name}:8000/service/health' for name in SERVICES]
        urls += ['https://haproxy:8080/health', 'https://haproxy:8443/health',
                 'https://consul:8501/v1/status/leader', 'https://rabbitmq:15691/metrics']
        for url in urls:
            with self.subTest(url=url):
                response = self.http.get(url)
                self.assertEqual(response.status_code, 200)
                stream = response.extensions['network_stream']
                self.assertIsNotNone(stream.get_extra_info('ssl_object'))

    def test_consul_health_checks_use_https(self):
        response = self.http.get('https://consul:8501/v1/agent/checks')
        response.raise_for_status()
        checks = response.json()
        self.assertEqual(len(checks), 7)
        for check in checks.values():
            self.assertEqual(check['Status'], 'passing', check['ServiceName'])
            self.assertTrue(check['Output'].startswith('HTTP GET https://'), check)

    def test_haproxy_backends_are_healthy(self):
        response = self.http.get('https://haproxy:8404/;csv', auth=('admin', 'admin'))
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text.lstrip('# ')))
        backends = [row for row in rows if row['pxname'].startswith('be_') and row['svname'] == 'BACKEND']
        self.assertEqual(len(backends), 7)
        for backend in backends:
            self.assertEqual(backend['status'], 'UP', backend['pxname'])

    def test_rabbitmq_connections_are_encrypted(self):
        response = self.http.get('https://rabbitmq:15671/api/connections',
                                 auth=('manufacturing', 'manufacturing'))
        response.raise_for_status()
        connections = response.json()
        self.assertTrue(connections)
        for connection in connections:
            self.assertTrue(connection['ssl'])
            self.assertIn(connection['ssl_protocol'], ('tlsv1.2', 'tlsv1.3'))

    def test_mongodb_requires_tls(self):
        with MongoClient(os.environ['MONGODB_URL'], serverSelectionTimeoutMS=2000) as mongo:
            self.assertEqual(mongo.admin.command('ping')['ok'], 1)
        with MongoClient('mongodb://mongodb:27017', serverSelectionTimeoutMS=1000,
                         connectTimeoutMS=500) as plaintext:
            with self.assertRaises(ServerSelectionTimeoutError):
                plaintext.admin.command('ping')

    def test_plaintext_listeners_are_not_available(self):
        for host, port in (('rabbitmq', 5672), ('rabbitmq', 15672), ('rabbitmq', 15692),
                           ('consul', 8500), ('consul', 8600)):
            with self.subTest(host=host, port=port):
                with self.assertRaises(OSError):
                    with socket.create_connection((host, port), timeout=2):
                        self.fail(f'Unexpected plaintext listener: {host}:{port}')
        for url in ['http://haproxy:8080/health', 'http://haproxy:8443/health',
                    'http://haproxy:8404/', *[f'http://{name}:8000/service/health' for name in SERVICES]]:
            with self.subTest(url=url):
                try:
                    response = self.http.get(url)
                except httpx.HTTPError:
                    continue
                self.assertGreaterEqual(response.status_code, 400)

    def test_certificate_verification_is_enabled(self):
        with socket.create_connection(('haproxy', 8080), timeout=3) as connection:
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.context.wrap_socket(connection, server_hostname='incorrect-hostname')

    def test_authenticated_reads_work_over_https(self):
        login = self.http.post('https://haproxy:8080/api/auth/token', auth=(
            os.getenv('TEST_AUTH_USERNAME', 'admin'), os.getenv('TEST_AUTH_PASSWORD', 'admin123')))
        login.raise_for_status()
        headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
        for path in ('/api/orders', '/api/orders/sagas/history', '/api/logs'):
            response = self.http.get('https://haproxy:8080' + path, headers=headers)
            self.assertEqual(response.status_code, 200, path)
        response = self.http.get('https://haproxy:8080/api/orders')
        self.assertEqual(response.status_code, 401)


if __name__ == '__main__':
    unittest.main()
