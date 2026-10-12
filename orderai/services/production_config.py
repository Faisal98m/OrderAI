"""Strict Render demo startup and reverse-proxy configuration, without network I/O."""
import re
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import jsonify, request


def validate_environment(environ):
    problems = []
    if environ.get('ORDERAI_DEMO_ENABLED') != '1':
        problems.append('ORDERAI_DEMO_ENABLED must be 1')
    if environ.get('ORDERAI_MENU_EDITOR_ENABLED') != '0':
        problems.append('ORDERAI_MENU_EDITOR_ENABLED must be 0')
    for name, minimum in (('OPENAI_API_KEY', 1), ('ORDERAI_DEMO_ACCESS_CODE', 16), ('FLASK_SECRET_KEY', 32)):
        value = environ.get(name, '')
        if len(value.strip()) < minimum:
            problems.append(f'{name} is missing or too short (minimum {minimum} characters)')
    hostname = environ.get('RENDER_EXTERNAL_HOSTNAME', '')
    if environ.get('RENDER') == 'true' and not hostname:
        problems.append('RENDER_EXTERNAL_HOSTNAME is required on Render')
    if hostname and not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', hostname):
        problems.append('RENDER_EXTERNAL_HOSTNAME must be a hostname without a scheme or port')
    if problems:
        # Do not include secret values in startup errors.
        raise RuntimeError('Unsafe production configuration: ' + '; '.join(problems))


def initialise_storage(app):
    """An explicit startup step usable from WSGI or the local launcher."""
    if not app.config['DEMO_ENABLED']:
        from orderai.services.database import init_db
        init_db()


def configure_production(app, environ):
    validate_environment(environ)
    hostname = environ.get('RENDER_EXTERNAL_HOSTNAME')
    app.config.update(
        DEMO_ENABLED=True, MENU_EDITOR_ENABLED=False,
        DEMO_ACCESS_CODE=environ['ORDERAI_DEMO_ACCESS_CODE'],
        SECRET_KEY=environ['FLASK_SECRET_KEY'],
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        DEBUG=False, TESTING=False, PROPAGATE_EXCEPTIONS=False,
        MAX_CONTENT_LENGTH=64 * 1024,
        TRUSTED_HOSTS=[hostname] if hostname else ['localhost', '127.0.0.1'],
        PREFERRED_URL_SCHEME='https',
    )
    # Render terminates TLS and overwrites/sets the forwarded scheme. Trust one
    # scheme value only, never forwarded client IP, host, port or prefix.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=0, x_host=0, x_port=0, x_prefix=0)

    @app.before_request
    def https_only():
        # Render's internal health probe may use plain HTTP; it has no side effects.
        if request.path != '/healthz' and not request.is_secure:
            return jsonify(error='HTTPS is required.'), 403

    @app.after_request
    def security_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'microphone=(self), camera=(), geolocation=()'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self' https://api.openai.com; media-src 'self' blob:; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    initialise_storage(app)
    return app
