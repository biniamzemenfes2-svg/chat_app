from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Regexp,
    ValidationError,
)

from app.services import auth_service

from .validators import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    RESERVED_USERNAMES,
    check_password_strength,
    printable_text,
    strip,
    strip_lower,
)


class RegistrationForm(FlaskForm):
    username = StringField(
        "Username",
        filters=[strip_lower],
        validators=[
            DataRequired(),
            Length(min=3, max=32),
            Regexp(
                r"^[a-z0-9_]+$",
                message="Use only letters, numbers and underscores.",
            ),
        ],
    )
    email = StringField(
        "Email address",
        filters=[strip_lower],
        validators=[
            DataRequired(),
            Length(max=254),
            Email(check_deliverability=False, message="Enter a valid email address."),
        ],
    )
    display_name = StringField(
        "Display name",
        filters=[strip],
        validators=[DataRequired(), Length(max=64), printable_text],
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(
                min=PASSWORD_MIN_LENGTH,
                max=PASSWORD_MAX_LENGTH,
                message=f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.",
            ),
        ],
    )
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords do not match.")],
    )
    submit = SubmitField("Create account")

    def validate_username(self, field):
        if field.data in RESERVED_USERNAMES:
            raise ValidationError("That username is not available.")
        if auth_service.username_taken(field.data):
            raise ValidationError("That username is already taken.")

    def validate_email(self, field):
        if auth_service.email_taken(field.data):
            raise ValidationError("An account with that email already exists.")

    def validate_password(self, field):
        check_password_strength(
            field.data, username=self.username.data or "", email=self.email.data or ""
        )


class LoginForm(FlaskForm):
    identifier = StringField(
        "Username or email", filters=[strip_lower], validators=[DataRequired()]
    )
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")
