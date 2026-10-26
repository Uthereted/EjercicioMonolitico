"""Run the payment domain event regression tests."""
import os
import unittest
os.environ["DOMAIN_SERVICE"] = "payment"
from test_domain_events import DomainEventTests

if __name__ == "__main__":
    unittest.main()
