"""Run from the project root: python tests/test_chassis_integration.py."""

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChassisIntegrationTests(unittest.TestCase):
    def test_services_use_the_installed_chassis(self):
        # Each service owns a different package named app, so isolate imports.
        for folder, name in (("order", "order-service"), ("payment", "payment-service")):
            with self.subTest(service=folder):
                code = f"""
import logging
from fastapi.testclient import TestClient
from app.main import app
# No lifespan: this test exercises HTTP routing without starting RabbitMQ workers.
client = TestClient(app)
response = client.get('/health')
assert response.status_code == 200, response.text
assert response.json() == {{'status': 'ok', 'service': {name!r}}}
assert '/health' in client.get('/openapi.json').json()['paths']
logger = logging.getLogger('app.messaging')
assert logger.isEnabledFor(logging.INFO)
assert not logger.disabled
logger.info('Chassis integration verified')
"""
                env = {**os.environ, "SQLALCHEMY_DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
                result = subprocess.run(
                    [sys.executable, "-c", code], cwd=ROOT / "services" / folder,
                    env=env, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"[{name}] INFO app.messaging", result.stdout)


if __name__ == "__main__":
    unittest.main()
