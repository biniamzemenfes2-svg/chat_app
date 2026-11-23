"""Profile picture validation, normalisation and storage.

Uploaded bytes are never stored as given. Each image is decoded, size-checked,
cropped to a square and re-encoded as WebP, which discards metadata (EXIF/GPS)
and anything smuggled inside the file. The stored name is random.
"""
import io
import os
import re
import secrets
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

AVATAR_SIZE = 256
MAX_PIXELS = 25_000_000  # checked from the header before any pixel decoding
MIN_SIDE = 32
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.webp$")


class InvalidImage(ValueError):
    """The upload is not an acceptable profile picture (message is user-facing)."""


def process_avatar(stream) -> bytes:
    limit = current_app.config["AVATAR_MAX_BYTES"]
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise InvalidImage(f"Image is too large (maximum {limit // (1024 * 1024)} MB).")
    if not data:
        raise InvalidImage("The uploaded file is empty.")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise InvalidImage("Use a JPEG, PNG or WebP image.")
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise InvalidImage("Image dimensions are too large.")
            if min(width, height) < MIN_SIDE:
                raise InvalidImage("Image is too small.")
            probe.verify()

        # verify() invalidates the object, so decode from a fresh one.
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            has_alpha = "A" in image.getbands() or image.mode == "P"
            image = image.convert("RGBA" if has_alpha else "RGB")
            image = ImageOps.fit(
                image, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS
            )
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=85)
            return out.getvalue()
    except InvalidImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise InvalidImage("That file is not a valid image.") from None


def avatar_dir() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]) / "avatars"


def save_avatar(user, webp_bytes: bytes) -> str | None:
    """Store processed bytes for ``user``; return the previous file name.

    The caller commits the database change and *then* calls ``discard_file`` on
    the returned name, so a failed commit never leaves a dangling reference.
    """
    directory = avatar_dir()
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.webp"
    tmp_path = directory / f".{name}.tmp"
    tmp_path.write_bytes(webp_bytes)
    os.replace(tmp_path, directory / name)
    old = user.avatar
    user.avatar = name
    return old


def clear_avatar(user) -> str | None:
    """Detach the picture from ``user``; return the file name to discard."""
    old = user.avatar
    user.avatar = None
    return old


def discard_file(name: str | None) -> None:
    if name and FILENAME_RE.match(name):
        try:
            (avatar_dir() / name).unlink()
        except FileNotFoundError:
            pass
