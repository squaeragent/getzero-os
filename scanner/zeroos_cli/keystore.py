"""AES-256-GCM encrypted keystore for HL private key."""

import getpass
import hashlib
import os
import platform

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


KEYSTORE_PATH = os.path.expanduser("~/.zeroos/keystore.enc")
SALT_LEN = 16
NONCE_LEN = 12
KDF_ITERATIONS = 480_000


def _machine_salt() -> bytes:
    """Derive a machine-specific salt from hostname + username."""
    identity = f"{platform.node()}:{getpass.getuser()}"
    return hashlib.sha256(identity.encode()).digest()[:SALT_LEN]


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 256-bit key from password + salt using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(password.encode())


def encrypt(data: bytes, password: str) -> bytes:
    """Encrypt data with AES-256-GCM. Returns salt + nonce + ciphertext."""
    salt = os.urandom(SALT_LEN)
    machine_salt = _machine_salt()
    combined_salt = hashlib.sha256(salt + machine_salt).digest()[:SALT_LEN]
    key = _derive_key(password, combined_salt)
    nonce = os.urandom(NONCE_LEN)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, data, None)
    return salt + nonce + ct


def decrypt(data: bytes, password: str) -> bytes:
    """Decrypt AES-256-GCM data. Expects salt + nonce + ciphertext."""
    salt = data[:SALT_LEN]
    nonce = data[SALT_LEN : SALT_LEN + NONCE_LEN]
    ct = data[SALT_LEN + NONCE_LEN :]
    machine_salt = _machine_salt()
    combined_salt = hashlib.sha256(salt + machine_salt).digest()[:SALT_LEN]
    key = _derive_key(password, combined_salt)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None)


def store_key(private_key: str, password: str) -> None:
    """Encrypt and store a private key to the keystore."""
    os.makedirs(os.path.dirname(KEYSTORE_PATH), exist_ok=True)
    encrypted = encrypt(private_key.encode(), password)
    with open(KEYSTORE_PATH, "wb") as f:
        f.write(encrypted)
    os.chmod(KEYSTORE_PATH, 0o600)


def load_key(password: str) -> str:
    """Load and decrypt the private key from the keystore."""
    with open(KEYSTORE_PATH, "rb") as f:
        data = f.read()
    return decrypt(data, password).decode()
