import json
import os
import hashlib
import base64
from cryptography.fernet import Fernet

USERS_FILE = "users.json"
KEY_FILE = "fernet.key"

# ==========================
# PASSWORD HASHING
# ==========================

def hash_password(password: str) -> bytes:
    """Return salt || hash for the given password."""
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        200000,          # iterations
    )
    return salt + hashed


def verify_password(stored: bytes, password: str) -> bool:
    """Check password against stored salt||hash."""
    salt = stored[:16]
    stored_hash = stored[16:]
    check_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        200000,
    )
    return stored_hash == check_hash


# ==========================
# USER REGISTRATION / LOGIN
# ==========================

def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)


def save_users(users: dict) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)


def register_user(username: str, password: str) -> bool:
    """
    Register new user. Returns True on success, False if username exists.
    """
    username = username.strip()
    if not username:
        return False
    users = load_users()
    if username in users:
        return False
    salted_hash = hash_password(password)
    users[username] = base64.b64encode(salted_hash).decode("ascii")
    save_users(users)
    return True


def authenticate(username: str, password: str) -> bool:
    """
    Authenticate existing user. Returns True on success.
    """
    username = username.strip()
    users = load_users()
    if username not in users:
        return False
    stored = base64.b64decode(users[username])
    return verify_password(stored, password)


# ==========================
# ENCRYPTION (Fernet)
# ==========================

def _load_or_create_key() -> bytes:
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        return key
    with open(KEY_FILE, "rb") as f:
        return f.read()


_FERNET = Fernet(_load_or_create_key())


def encrypt_data(plaintext: str) -> bytes:
    """Encrypt a text line and return bytes token."""
    return _FERNET.encrypt(plaintext.encode("utf-8"))


def decrypt_data(token: str | bytes) -> str:
    """Decrypt token (str or bytes) and return plaintext string."""
    if isinstance(token, str):
        token = token.encode("utf-8")
    return _FERNET.decrypt(token).decode("utf-8")
