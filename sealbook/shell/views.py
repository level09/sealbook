"""Pages every signed-in user reaches, and the rail's counts."""

from quart import Blueprint, g, render_template, request, session
from quart_security import auth_required, current_user, roles_required
from sqlalchemy import Text, func, select

from sealbook.collaboration import notifications
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Payload, Statement, StatementTier

# stk's users blueprint is admin-only; these pages are for every analyst.
bp_account = Blueprint("account", __name__)


@bp_account.before_request
@auth_required("session")
async def account_guard():
    pass


@bp_account.get("/security")
async def security():
    return await render_template(
        "security/index.html",
        title="Security",
        cards=[
            ("Passkeys", "/wan-register", "ti-fingerprint"),
            ("Change Password", "/change", "ti-key"),
            ("Two-Factor", "/tf-setup", "ti-shield-lock"),
            ("Recovery Codes", "/mf-recovery-codes", "ti-lifebuoy"),
        ],
    )


@bp_account.get("/admin")
@roles_required("admin")
async def admin():
    return await render_template(
        "security/index.html",
        title="Admin",
        cards=[
            ("Users", "/users/", "ti-users"),
            ("Roles", "/roles/", "ti-shield"),
            ("Activity", "/activities/", "ti-history"),
        ],
    )


@bp_account.get("/api/nav/counts")
async def nav_counts():
    open_requests = await g.db_session.scalar(
        select(func.count())
        .select_from(Statement)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(
            StatementTier,
            func.concat("statement:", StatementTier.statement_id.cast(Text))
            == Statement.subject,
        )
        .where(
            Statement.prop == Prop.REVIEW_REQUEST,
            Payload.value["reviewer"].as_integer() == current_user.id,
            StatementTier.tier == Tier.UNCONFIRMED,
        )
    )
    unread = await g.db_session.scalar(notifications.unread_query(current_user.id))
    return {"review": open_requests, "notifications": unread}


def register_passkey_return(app):
    @app.before_request
    async def remember_passkey_next():
        """/wan-register?next=<path> returns there once a passkey is registered."""
        if request.endpoint == "security.wan_register" and "next" in request.args:
            from sealbook.review.views import safe_next

            target = safe_next(request.args["next"])
            if target is None:
                session.pop("passkey_next", None)
            else:
                session["passkey_next"] = target

    @app.after_request
    async def return_after_passkey(response):
        if (
            request.endpoint == "security.wan_register_response"
            and response.status_code == 302
            and "passkey_next" in session
            and current_user.is_authenticated
        ):
            from sealbook.ledger.attest import registered

            db = g.get("db_session")
            if db is not None and await registered(db, current_user):
                response.headers["Location"] = session.pop("passkey_next")
        return response
