import asyncio
import inspect

import click
from quart import Quart, g, render_template, request
from quart_security import Security, SQLAlchemyUserDatastore
from quart_security.views import _ensure_csrf_token
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from werkzeug.exceptions import HTTPException

import stk.cli as cli
import stk.extensions as ext
from stk.agent_login import agent_login_enabled, bp_agent_login
from stk.agents.views import bp_agents
from stk.assets import register_assets
from stk.casemap.views import bp_casemap
from stk.cases.views import bp_cases
from stk.collaboration.views import bp as bp_collaboration
from stk.desk.views import bp as bp_desk
from stk.discovery.views import bp as bp_discovery
from stk.entities.views import bp_entities
from stk.extensions import session
from stk.guide.views import bp_guide
from stk.ingest.views import bp_ingest
from stk.maps.views import bp_maps
from stk.mcp.views import bp_mcp
from stk.monitor.views import monitor
from stk.packages.views import packages
from stk.people.views import bp_people
from stk.places.views import bp_places
from stk.portal.views import portal
from stk.public.views import public
from stk.research.views import bp_research
from stk.review.views import review
from stk.search.views import bp as bp_search
from stk.settings import Config
from stk.sheets.views import bp as bp_sheets
from stk.sources.views import bp as bp_sources
from stk.stories.views import bp_stories
from stk.thumbs.views import bp as bp_thumbs
from stk.user.forms import ExtendedRegisterForm, OAuthAwareChangePasswordForm
from stk.user.models import Role, User, WebAuthn
from stk.user.views import bp_account, bp_user
from stk.watch.views import bp_watch
from stk.websocket import ws_bp


def create_app(config_object=Config):
    app = Quart(__name__)
    app.config.from_object(config_object)

    register_blueprints(app)
    register_assets(app)
    register_extensions(app)
    register_errorhandlers(app)
    register_commands(app, cli)
    return app


def register_extensions(app):
    # Async SQLAlchemy engine + session factory
    ext.engine = create_async_engine(app.config["SQLALCHEMY_DATABASE_URI"])
    ext.async_session_factory = async_sessionmaker(ext.engine, expire_on_commit=False)

    user_datastore = SQLAlchemyUserDatastore(
        ext.async_session_factory, User, Role, webauthn_model=WebAuthn
    )

    async def _open_session():
        g.db_session = user_datastore.session

    @app.after_request
    async def _close_session(response):
        db_session = g.pop("db_session", None)
        if db_session is not None:
            await db_session.close()
        return response

    async def _open_ws_session():
        g.db_session = user_datastore.session

    @app.after_websocket
    async def _close_ws_session(response):
        db_session = g.pop("db_session", None)
        if db_session is not None:
            await db_session.close()
        return response

    @app.while_serving
    async def _lifespan():
        yield
        # quart-session initializes backend connections in before_serving,
        # but doesn't close them on shutdown. Ensure we always release them
        # so Ctrl+C exits cleanly in dev.
        session_interface = getattr(app, "session_interface", None)
        backend = getattr(session_interface, "backend", None)
        if backend is not None:
            close_method = getattr(backend, "aclose", None) or getattr(
                backend, "close", None
            )
            if close_method is not None:
                try:
                    close_result = close_method()
                    if inspect.isawaitable(close_result):
                        await close_result
                except Exception:
                    app.logger.exception("Error while closing session backend")

            pool = getattr(backend, "connection_pool", None)
            disconnect = getattr(pool, "disconnect", None) if pool else None
            if disconnect is not None:
                try:
                    disconnect_result = disconnect()
                    if inspect.isawaitable(disconnect_result):
                        await disconnect_result
                except Exception:
                    app.logger.exception("Error while disconnecting session pool")

        try:
            await asyncio.wait_for(ext.engine.dispose(), timeout=5)
        except TimeoutError:
            app.logger.warning("Timed out while disposing SQLAlchemy engine")

    from stk.security import register_security_controls
    from stk.utils.ratelimit import register_rate_limits

    register_rate_limits(app)
    register_security_controls(app)

    Security(
        app,
        user_datastore,
        register_form=ExtendedRegisterForm,
        change_password_form=OAuthAwareChangePasswordForm,
    )
    # The library resets its session before loading the user. Share that session
    # so signals and application routes participate in the same transaction.
    app.before_request(_open_session)
    app.before_websocket(_open_ws_session)

    # Session initialization
    if app.config.get("SESSION_TYPE") == "redis":
        session.init_app(app)
    # For non-redis, fall back to Quart's built-in cookie sessions

    # CSRF token for POSTs outside quart-security templates (e.g. logout form).
    # Uses the library's own get-or-create so tokens stay in sync.
    app.jinja_env.globals["csrf_token"] = _ensure_csrf_token

    return None


def register_blueprints(app):
    app.register_blueprint(bp_user)
    app.register_blueprint(bp_account)
    app.register_blueprint(public)
    app.register_blueprint(portal)
    app.register_blueprint(ws_bp)
    app.register_blueprint(bp_ingest)
    app.register_blueprint(bp_mcp)
    app.register_blueprint(monitor)
    app.register_blueprint(bp_watch)
    app.register_blueprint(review)
    app.register_blueprint(packages)
    app.register_blueprint(bp_people)
    app.register_blueprint(bp_places)
    app.register_blueprint(bp_entities)
    app.register_blueprint(bp_maps)
    app.register_blueprint(bp_collaboration)
    app.register_blueprint(bp_discovery)
    app.register_blueprint(bp_desk)
    app.register_blueprint(bp_search)
    app.register_blueprint(bp_sources)
    app.register_blueprint(bp_stories)
    app.register_blueprint(bp_casemap)
    app.register_blueprint(bp_thumbs)
    app.register_blueprint(bp_sheets)
    app.register_blueprint(bp_research)
    app.register_blueprint(bp_agents)
    app.register_blueprint(bp_cases)
    app.register_blueprint(bp_guide)
    if agent_login_enabled(app):
        app.register_blueprint(bp_agent_login)
    return None


def register_errorhandlers(app):
    import logging

    logger = logging.getLogger(__name__)

    def _is_api_request():
        return (
            request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
        )

    from stk.blobs import MissingBytes

    @app.errorhandler(MissingBytes)
    async def no_file(error):
        # Synthetic filler records and lost files: a plain 404, never a 500.
        if _is_api_request():
            return {
                "message": "No file is stored for this source",
                "reason": "no_file",
            }, 404
        return await render_template(["404.html"]), 404

    @app.errorhandler(Exception)
    async def handle_exception(error):
        db_session = g.pop("db_session", None)
        if db_session is not None:
            try:
                # Detach loaded instances first so rollback doesn't expire them;
                # error templates still read current_user after the session closes.
                db_session.expunge_all()
                await db_session.rollback()
            except Exception:
                logger.warning(
                    "Failed to rollback session during error handling", exc_info=True
                )
            finally:
                await db_session.close()

        # Only HTTP errors carry a status; SQLAlchemy errors also have a .code ("e3q8").
        code = error.code if isinstance(error, HTTPException) else 500
        if code == 500:
            logger.exception("Unhandled exception")

        if _is_api_request():
            return {
                "message": "Internal server error"
                if code == 500
                else getattr(error, "description", "Request failed")
            }, code
        # Fall back to 500.html for codes without a dedicated template (405, 403, ...)
        return await render_template([f"{code}.html", "500.html"]), code

    async def render_error(error):
        error_code = getattr(error, "code", 500)
        if _is_api_request():
            return {
                "message": error.name if hasattr(error, "name") else "Error"
            }, error_code
        return await render_template([f"{error_code}.html", "500.html"]), error_code

    for errcode in [401, 404, 500]:
        app.errorhandler(errcode)(render_error)
    return None


def register_commands(app: Quart, commands_module):
    """Automatically register all Click commands and command groups."""
    for _name, obj in inspect.getmembers(commands_module):
        if isinstance(obj, click.Command | click.Group):
            app.cli.add_command(obj)
