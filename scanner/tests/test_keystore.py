"""Tests for scanner/zeroos_cli/keystore.py — AES-256-GCM encrypted keystore."""

import os
import stat

import pytest
from unittest.mock import patch

from scanner.zeroos_cli.keystore import (
    encrypt,
    decrypt,
    store_key,
    load_key,
    _derive_key,
    _machine_salt,
    SALT_LEN,
    NONCE_LEN,
    KDF_ITERATIONS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ENCRYPT / DECRYPT ROUND-TRIP
# ═══════════════════════════════════════════════════════════════════════════════

class TestEncryptDecrypt:
    """Core cryptographic operations."""

    def test_round_trip_basic(self):
        """encrypt → decrypt returns original data."""
        data = b"0xdeadbeefcafebabe1234567890abcdef"
        password = "hunter2"
        encrypted = encrypt(data, password)
        assert decrypt(encrypted, password) == data

    def test_round_trip_empty_data(self):
        """Empty plaintext round-trips correctly."""
        data = b""
        password = "test"
        encrypted = encrypt(data, password)
        assert decrypt(encrypted, password) == data

    def test_round_trip_long_password(self):
        """Long passwords work correctly."""
        data = b"secret_key_data"
        password = "a" * 1000
        encrypted = encrypt(data, password)
        assert decrypt(encrypted, password) == data

    def test_round_trip_unicode_password(self):
        """Unicode characters in password work."""
        data = b"private_key_hex"
        password = "p\u00e4ssw\u00f6rd\U0001f512"
        encrypted = encrypt(data, password)
        assert decrypt(encrypted, password) == data

    def test_wrong_password_fails(self):
        """Decryption with wrong password raises."""
        data = b"0xdeadbeef"
        encrypted = encrypt(data, "correct_password")
        with pytest.raises(Exception):  # cryptography raises InvalidTag
            decrypt(encrypted, "wrong_password")

    def test_tampered_ciphertext_fails(self):
        """Modifying ciphertext causes decryption failure (GCM authentication)."""
        data = b"sensitive_key"
        encrypted = bytearray(encrypt(data, "pass"))
        # Flip a byte in the ciphertext portion
        encrypted[-1] ^= 0xFF
        with pytest.raises(Exception):
            decrypt(bytes(encrypted), "pass")

    def test_truncated_ciphertext_fails(self):
        """Truncated data causes decryption failure."""
        data = b"key_data"
        encrypted = encrypt(data, "pass")
        with pytest.raises(Exception):
            decrypt(encrypted[:10], "pass")

    def test_ciphertext_format(self):
        """Ciphertext has salt + nonce + encrypted data."""
        data = b"test"
        encrypted = encrypt(data, "pass")
        # Must be at least salt + nonce + GCM tag (16 bytes)
        assert len(encrypted) >= SALT_LEN + NONCE_LEN + 16
        # First SALT_LEN bytes are salt
        salt = encrypted[:SALT_LEN]
        assert len(salt) == SALT_LEN
        # Next NONCE_LEN bytes are nonce
        nonce = encrypted[SALT_LEN:SALT_LEN + NONCE_LEN]
        assert len(nonce) == NONCE_LEN

    def test_different_encryptions_differ(self):
        """Same plaintext + password produces different ciphertext (random salt/nonce)."""
        data = b"same_data"
        password = "same_pass"
        enc1 = encrypt(data, password)
        enc2 = encrypt(data, password)
        assert enc1 != enc2  # random salt + nonce


# ═══════════════════════════════════════════════════════════════════════════════
# KEY DERIVATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeyDerivation:
    """PBKDF2 key derivation."""

    def test_derive_key_deterministic(self):
        """Same password + salt always produces same key."""
        salt = b"0123456789abcdef"
        key1 = _derive_key("password", salt)
        key2 = _derive_key("password", salt)
        assert key1 == key2

    def test_derive_key_length(self):
        """Derived key is 32 bytes (256 bits)."""
        key = _derive_key("test", b"salt" * 4)
        assert len(key) == 32

    def test_different_passwords_different_keys(self):
        """Different passwords produce different keys."""
        salt = b"0123456789abcdef"
        key1 = _derive_key("password1", salt)
        key2 = _derive_key("password2", salt)
        assert key1 != key2

    def test_different_salts_different_keys(self):
        """Different salts produce different keys."""
        key1 = _derive_key("password", b"salt_a__________")
        key2 = _derive_key("password", b"salt_b__________")
        assert key1 != key2


# ═══════════════════════════════════════════════════════════════════════════════
# MACHINE SALT
# ═══════════════════════════════════════════════════════════════════════════════

class TestMachineSalt:
    """Machine-specific salt derivation."""

    def test_machine_salt_length(self):
        """Machine salt is SALT_LEN bytes."""
        salt = _machine_salt()
        assert len(salt) == SALT_LEN

    def test_machine_salt_deterministic(self):
        """Same machine produces same salt."""
        salt1 = _machine_salt()
        salt2 = _machine_salt()
        assert salt1 == salt2

    def test_different_machine_different_salt(self):
        """Different hostname/username produces different salt."""
        real_salt = _machine_salt()
        with patch("scanner.zeroos_cli.keystore.platform") as mock_plat, \
             patch("scanner.zeroos_cli.keystore.getpass") as mock_gp:
            mock_plat.node.return_value = "other-machine"
            mock_gp.getuser.return_value = "other-user"
            other_salt = _machine_salt()
        assert real_salt != other_salt


# ═══════════════════════════════════════════════════════════════════════════════
# STORE / LOAD KEY
# ═══════════════════════════════════════════════════════════════════════════════

class TestStoreLoadKey:
    """Keystore file operations."""

    def test_store_and_load(self, tmp_path):
        """store_key → load_key returns the original private key."""
        keystore_path = str(tmp_path / "keystore.enc")
        private_key = "0xdeadbeefcafebabe1234567890abcdef1234567890abcdef1234567890abcdef"
        password = "secure_password_123"

        with patch("scanner.zeroos_cli.keystore.KEYSTORE_PATH", keystore_path):
            store_key(private_key, password)
            loaded = load_key(password)

        assert loaded == private_key

    def test_store_creates_directory(self, tmp_path):
        """store_key creates parent directories if needed."""
        keystore_path = str(tmp_path / "deep" / "nested" / "keystore.enc")

        with patch("scanner.zeroos_cli.keystore.KEYSTORE_PATH", keystore_path):
            store_key("0xkey", "pass")

        assert os.path.exists(keystore_path)

    def test_store_sets_permissions(self, tmp_path):
        """store_key sets 0o600 (owner read/write only)."""
        keystore_path = str(tmp_path / "keystore.enc")

        with patch("scanner.zeroos_cli.keystore.KEYSTORE_PATH", keystore_path):
            store_key("0xkey", "pass")

        mode = os.stat(keystore_path).st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_load_wrong_password_fails(self, tmp_path):
        """load_key with wrong password raises."""
        keystore_path = str(tmp_path / "keystore.enc")

        with patch("scanner.zeroos_cli.keystore.KEYSTORE_PATH", keystore_path):
            store_key("0xkey", "correct")
            with pytest.raises(Exception):
                load_key("wrong")

    def test_load_missing_file_fails(self, tmp_path):
        """load_key on missing file raises FileNotFoundError."""
        keystore_path = str(tmp_path / "nonexistent.enc")

        with patch("scanner.zeroos_cli.keystore.KEYSTORE_PATH", keystore_path):
            with pytest.raises(FileNotFoundError):
                load_key("pass")

    def test_store_overwrites_existing(self, tmp_path):
        """store_key replaces previous key."""
        keystore_path = str(tmp_path / "keystore.enc")

        with patch("scanner.zeroos_cli.keystore.KEYSTORE_PATH", keystore_path):
            store_key("0xold_key", "pass")
            store_key("0xnew_key", "pass")
            loaded = load_key("pass")

        assert loaded == "0xnew_key"
