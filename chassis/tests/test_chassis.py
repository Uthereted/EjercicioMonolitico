import contextlib
import io
import logging
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from manufacturing_chassis import configure_logging, create_health_router


class ChassisTests(unittest.TestCase):
    def test_health_identifies_each_service(self):
        for name in ("order-service", "payment-service"):
            with self.subTest(service=name):
                app = FastAPI()
                app.include_router(create_health_router(name))
                with TestClient(app) as client:
                    response = client.get("/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "ok", "service": name})

    def test_logging_filters_level_and_does_not_duplicate_messages(self):
        output = io.StringIO()
        logger = logging.getLogger("app.messaging")
        external = logging.getLogger("external_library")
        try:
            with contextlib.redirect_stdout(output):
                configure_logging("order-service")
                configure_logging("order-service")
                logger.debug("hidden-debug")
                logger.info("payment-pending")
                external.warning("external-warning")
            text = output.getvalue()
            self.assertEqual(text.count("payment-pending"), 1)
            self.assertIn("[order-service] INFO app.messaging", text)
            self.assertNotIn("hidden-debug", text)
            self.assertIn("external-warning", text)
            self.assertFalse(external.disabled)
        finally:
            # Leave handlers pointing at the real stdout after the capture ends.
            configure_logging("test")


if __name__ == "__main__":
    unittest.main()
