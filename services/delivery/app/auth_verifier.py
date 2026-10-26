"""Configure the shared JWT verifier with this service's persisted public key."""
from my_chassis.auth import JWTVerifier
from my_chassis.keys import database_public_key_loader
from .events import SigningKey
from .sql import database

verifier = JWTVerifier(key_loader=database_public_key_loader(database.SessionLocal, SigningKey),
                       issuer="manufacturing-client", audience="manufacturing-services")
fetch_public_key = verifier.fetch_public_key
verify_jwt = verifier.verify_jwt
