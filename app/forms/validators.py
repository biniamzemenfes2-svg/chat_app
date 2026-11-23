"""Shared form filters and validators."""
from wtforms.validators import ValidationError

PASSWORD_MIN_LENGTH = 10
# Upper bound keeps the (deliberately slow) password hash from being used to
# burn CPU with enormous inputs.
PASSWORD_MAX_LENGTH = 128

RESERVED_USERNAMES = frozenset(
    {
        "admin", "administrator", "root", "support", "help", "system", "staff",
        "moderator", "security", "api", "static", "login", "logout", "register",
        "settings", "profile", "me", "null", "undefined", "anonymous",
    }
)

# A deliberately small list: it catches the laziest choices, nothing more.
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password12", "password123", "1234567890",
        "12345678910", "qwertyuiop", "qwerty1234", "qwerty12345", "1q2w3e4r5t",
        "iloveyou123", "letmein1234", "welcome123", "welcome1234", "admin12345",
        "abc1234567", "abcdefghij", "0123456789", "11111111111", "passw0rd123",
    }
)


def strip_lower(value):
    return value.strip().lower() if isinstance(value, str) else value


def strip(value):
    return value.strip() if isinstance(value, str) else value


def printable_text(form, field):
    """Reject control characters (newlines, NULs, ...) but allow any language."""
    if field.data and not all(
        ch.isprintable() or ch == " " for ch in field.data
    ):
        raise ValidationError("Contains characters that are not allowed.")


def check_password_strength(password: str, *, username: str = "", email: str = ""):
    if password.isdigit():
        raise ValidationError("Password cannot be only numbers.")
    if password.lower() in COMMON_PASSWORDS:
        raise ValidationError("That password is too common. Choose another.")
    lowered = password.lower()
    if username and len(username) >= 3 and username.lower() in lowered:
        raise ValidationError("Password cannot contain your username.")
    local_part = email.split("@", 1)[0] if email else ""
    if len(local_part) >= 4 and local_part.lower() in lowered:
        raise ValidationError("Password cannot contain your email name.")
