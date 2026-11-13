"""Optional encryption of chat histories and their titles in the shared folder.

Only the standard library is used: SHAKE-256 keyed with a secret produces the
cipher stream, and HMAC-SHA-256 authenticates the result before it is read. The
32-byte key lives in this profile. Its owner copies it to each other desktop,
and it never enters the shared folder.
"""

import base64
import hashlib
import hmac
import os

import sync

MAGIC = b"CSE1"
KEY_NAME = "vault.key"
_cache = {}


class Locked(ValueError):
    """An encrypted file was found and this profile has no folder key."""


def key_file():
    return sync.STATE / KEY_NAME


def stored_key():
    """The folder key of this profile, or None. Reading needs only the file."""
    path = key_file()
    try:
        stamp = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if _cache.get("stamp") != stamp:
        try:
            key = base64.urlsafe_b64decode(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            key = b""
        if len(key) != 32:
            raise ValueError("The folder key file is damaged. Copy it again from another desktop.")
        _cache.update(stamp=stamp, key=key)
    return _cache["key"]


def writing_key():
    """The key for new uploads, when this desktop has encryption switched on."""
    if not sync.read_json(sync.CONFIG, {}).get("encrypted"):
        return None
    key = stored_key()
    if key is None:
        raise ValueError("Encryption is on but chat-sync/" + KEY_NAME + " is missing from this profile.")
    return key


def create_key():
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as out:
        out.write(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii") + "\n")


def part(key, purpose):
    return hmac.new(key, purpose, hashlib.sha256).digest()


def block_name(key, data):
    # A keyed name still lets equal blocks share one file, without revealing
    # whether the folder holds a block someone else can guess.
    return hmac.new(part(key, b"block name"), data, hashlib.sha256).hexdigest()


def mix(key, nonce, data):
    if not data:
        return b""
    stream = hashlib.shake_256(part(key, b"stream") + nonce).digest(len(data))
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def sealed(data):
    return data[:4] == MAGIC


def seal(key, nonce, plain):
    """Each nonce is derived from the content it seals, so a stream is never used twice."""
    body = mix(key, nonce, plain)
    return MAGIC + hmac.new(part(key, b"seal"), nonce + body, hashlib.sha256).digest() + body


def unseal(key, nonce, data):
    if key is None:
        raise Locked("This chat is encrypted. Copy chat-sync/" + KEY_NAME + " from a desktop that can read it.")
    tag, body = data[4:36], data[36:]
    if len(data) < 36 or not hmac.compare_digest(tag, hmac.new(part(key, b"seal"), nonce + body, hashlib.sha256).digest()):
        raise ValueError("An encrypted file failed its check. This desktop may hold a different folder key.")
    return mix(key, nonce, body)
