"""Run the delivery domain event regression tests."""
import os
import unittest
os.environ["DOMAIN_SERVICE"] = "delivery"
from test_domain_events import DomainEventTests

if __name__ == "__main__":
    unittest.main()
