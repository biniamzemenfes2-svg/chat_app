from flask import jsonify, render_template, request
from flask_wtf.csrf import CSRFError

_MESSAGES = {
    400: ("Request could not be completed", "Your form expired or was invalid. Go back, reload the page and try again."),
    403: ("Access denied", "You don't have permission to view this."),
    404: ("Page not found", "The page you're looking for doesn't exist."),
    413: ("File too large", "That upload is larger than the allowed size."),
    429: ("Too many attempts", "You've made too many requests. Please wait a few minutes and try again."),
}


def _render(code):
    title, message = _MESSAGES.get(code, ("Something went wrong", "Please try again."))
    if request.path.startswith("/api/"):
        return jsonify(error=message), code
    return render_template("errors/error.html", code=code, title=title, message=message), code


def register_error_handlers(app):
    for code in _MESSAGES:
        app.register_error_handler(code, lambda error, code=code: _render(code))
    app.register_error_handler(CSRFError, lambda error: _render(400))

    @app.errorhandler(500)
    def server_error(error):
        return render_template(
            "errors/error.html", code=500, title="Something went wrong",
            message="An unexpected error occurred. Please try again.",
        ), 500
