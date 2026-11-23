from flask_login import current_user
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import BooleanField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError

from app.services import avatar_service

from .validators import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    check_password_strength,
    printable_text,
    strip,
)


class ProfileForm(FlaskForm):
    display_name = StringField(
        "Display name",
        filters=[strip],
        validators=[DataRequired(), Length(max=64), printable_text],
    )
    bio = TextAreaField("Bio", filters=[strip], validators=[Length(max=280)])
    avatar = FileField("Profile picture")
    remove_avatar = BooleanField("Remove current picture")
    submit = SubmitField("Save changes")

    processed_avatar: bytes | None = None

    def validate_avatar(self, field):
        upload = field.data
        if not upload or not getattr(upload, "filename", ""):
            return
        try:
            self.processed_avatar = avatar_service.process_avatar(upload.stream)
        except avatar_service.InvalidImage as exc:
            raise ValidationError(str(exc)) from exc


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField(
        "New password",
        validators=[
            DataRequired(),
            Length(
                min=PASSWORD_MIN_LENGTH,
                max=PASSWORD_MAX_LENGTH,
                message=f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.",
            ),
        ],
    )
    confirm_new_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords do not match.")],
    )
    submit = SubmitField("Change password")

    def validate_current_password(self, field):
        if not current_user.check_password(field.data):
            raise ValidationError("Current password is incorrect.")

    def validate_new_password(self, field):
        if current_user.check_password(field.data):
            raise ValidationError("Choose a password different from your current one.")
        check_password_strength(
            field.data, username=current_user.username, email=current_user.email
        )
