# =================================================================================================
# Contributing Authors:     Rudwika Manne, Harshini Ponnam, Jayadeep Kothapalli
# Email Addresses:          rma425@uky.edu, hpo245@uky.edu, jsko232@uky.edu
# Date:                     2025-11-26
# Purpose:                  Provide secure password handling (salted hashing), persistent
#                           user registration/login storage, and symmetric encryption utilities
#                           using Fernet for protecting all player-to-server communication.
#                           This module is imported by both pongClient and pongServer.
# Misc:                     CS 371 Fall 2025 Project — Authentication + Encryption Extension
# =================================================================================================

import json
import os
import hashlib
import base64
from typing import Dict
from cryptography.fernet import Fernet

USERS_FILE: str = "users.json"
KEY_FILE: str = "fernet.key"

# ==========================
# PASSWORD HASHING
# ==========================
# Author:      Harshini Ponnam
# Purpose:     Generate a secure salted hash for a new password using PBKDF2-HMAC (SHA-256).
# Pre:         password is a non-empty UTF-8 string provided during registration.
# Post:        Returns `salt || hash` as raw bytes, where:
#                  salt = 16 random bytes
#                  hash = PBKDF2-HMAC result with 200k iterations
def hash_password(password: str) -> bytes:
    """Return salt || hash for the given password."""
    salt: bytes = os.urandom(16)
    hashed: bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        200000,          # iterations
    )
    return salt + hashed


# ---------------------------------------------------------------------------------------------
# verify_password function
# ---------------------------------------------------------------------------------------------
# Author:      Harshini Ponnam
# Purpose:     Verify a user-entered password by recomputing PBKDF2 and comparing hashes.
# Pre:         stored = salt||hash bytes from users.json,
#              password = plaintext password user tries to log in with.
# Post:        Returns True if password is correct; otherwise False.
def verify_password(stored: bytes, password: str) -> bool:
    """Check password against stored salt||hash."""
    salt: bytes = stored[:16]
    stored_hash: bytes = stored[16:]
    check_hash: bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        200000,
    )
    return stored_hash == check_hash


# ==========================
# USER REGISTRATION / LOGIN
# ==========================
# Author:      Rudwika Manne
# Purpose:     Load the persistent users database from disk.
# Pre:         USERS_FILE ("users.json") may or may not exist.
# Post:        Returns a dict: { username: base64(salt||hash) }.
def load_users() -> Dict[str, str]:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)


# Author:      Rudwika Manne
# Purpose:     Save the in-memory users mapping back to disk as JSON.
# Pre:         users is a dict mapping usernames to base64-encoded salt||hash strings.
# Post:        Overwrites USERS_FILE with the serialized users dict.
def save_users(users: Dict[str, str]) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)


# ---------------------------------------------------------------------------------------------
# register_user function
# ---------------------------------------------------------------------------------------------
# Author:      Rudwika Manne
# Purpose:     Register a new user by hashing their password and storing it persistently.
# Pre:         username/password are raw strings from client auth; username must not exist.
# Post:        Writes new entry to users.json and returns True if successful, False otherwise.
def register_user(username: str, password: str) -> bool:
    """
    Register new user. Returns True on success, False if username exists.
    """
    username = username.strip()
    if not username:
        return False
    users: Dict[str, str] = load_users()
    if username in users:
        return False
    salted_hash: bytes = hash_password(password)
    users[username] = base64.b64encode(salted_hash).decode("ascii")
    save_users(users)
    return True


# Author:      Rudwika Manne
# Purpose:     Authenticate an existing user by verifying their password.
# Pre:         username exists in users.json and has a stored base64(salt||hash) entry.
# Post:        Returns True if password matches stored hash, False otherwise.
def authenticate(username: str, password: str) -> bool:
    """
    Authenticate existing user. Returns True on success.
    """
    username = username.strip()
    users: Dict[str, str] = load_users()
    if username not in users:
        return False
    stored: bytes = base64.b64decode(users[username])
    return verify_password(stored, password)


# ==========================
# ENCRYPTION (Fernet)
# ==========================
# Author:      Jayadeep Kothapalli
# Purpose:     Load an existing Fernet key if present; otherwise generate and save a new one.
# Pre:         KEY_FILE ("fernet.key") may or may not exist on disk.
# Post:        Returns a bytes key suitable for Fernet symmetric encryption.
def _load_or_create_key() -> bytes:
    if not os.path.exists(KEY_FILE):
        key: bytes = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        return key
    with open(KEY_FILE, "rb") as f:
        return f.read()


_FERNET: Fernet = Fernet(_load_or_create_key())


# Author:      Jayadeep Kothapalli
# Purpose:     Encrypt a small text message (e.g., "up", "down", "ready", or state line).
# Pre:         plaintext is a UTF-8 string; _FERNET has been initialized with a shared key.
# Post:        Returns an opaque Fernet token as bytes that can be sent over the network.
def encrypt_data(plaintext: str) -> bytes:
    """Encrypt a text line and return bytes token."""
    return _FERNET.encrypt(plaintext.encode("utf-8"))


# Author:      Jayadeep Kothapalli
# Purpose:     Decrypt an incoming Fernet token and return the original UTF-8 text.
# Pre:         token is either a bytes object or a UTF-8 string representation of a token.
# Post:        Returns the decrypted plaintext string (or raises if token is invalid).
def decrypt_data(token: str | bytes) -> str:
    """Decrypt token (str or bytes) and return plaintext string."""
    if isinstance(token, str):
        token = token.encode("utf-8")
    return _FERNET.decrypt(token).decode("utf-8")