"""Work assignment: any analyst may assign or reassign; every change is an event."""

import re
import uuid
from datetime import UTC, date, datetime
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from stk.collaboration.models import (
    PRIORITIES,
    STATUSES,
    Assignment,
    AssignmentEvent,
)
from stk.collaboration.notifications import notify
from stk.collaboration.service import eligible
from stk.discovery.models import FindingKey
from stk.ledger.models import Acquisition, Blob, Run, Statement
from stk.monitor.filter import statement_clause
from stk.user.models import User

MAX_BULK = 500
MAX_PAGE = 200
BLOB = re.compile(r"blob:[0-9a-f]{64}")
FINDING = re.compile(r"finding:[0-9a-f]{1,64}")


async def _known(db, kinds):
    """Subjects that exist anywhere, per kind."""
    out = set()
    if kinds["blob"]:
        out |= {
            f"blob:{s}"
            for s in await db.scalars(
                select(Blob.sha256).where(Blob.sha256.in_(kinds["blob"]))
            )
        }
    if kinds["statement"]:
        out |= {
            f"statement:{i}"
            for i in await db.scalars(
                select(Statement.id).where(Statement.id.in_(kinds["statement"]))
            )
        }
    if kinds["finding"]:
        out |= {
            f"finding:{k}"
            for k in await db.scalars(
                select(FindingKey.key).where(FindingKey.key.in_(kinds["finding"]))
            )
        }
    return out


async def _visible(db, investigation, kinds):
    out = set()
    if kinds["blob"]:
        out |= {
            f"blob:{s}"
            for s in await db.scalars(
                select(Acquisition.blob_sha256).where(
                    Acquisition.blob_sha256.in_(kinds["blob"]),
                    Acquisition.context["investigation"].astext == investigation,
                )
            )
        }
    if kinds["statement"]:
        out |= {
            f"statement:{i}"
            for i in await db.scalars(
                select(Statement.id).where(
                    Statement.id.in_(kinds["statement"]),
                    statement_clause({"investigation": investigation, "time": None}),
                )
            )
        }
    if kinds["finding"]:
        out |= {
            f"finding:{k}"
            for k in await db.scalars(
                select(FindingKey.key)
                .join(Run, Run.id == FindingKey.run_id)
                .where(
                    FindingKey.key.in_(kinds["finding"]),
                    Run.inputs["investigation"].astext == investigation,
                )
            )
        }
    return out


async def check_subjects(db, investigation, subjects):
    """400 for a malformed or unknown subject, 403 for one outside the investigation."""
    if not isinstance(investigation, str) or not investigation:
        raise ValueError("investigation is required")
    if (
        not isinstance(subjects, list)
        or not subjects
        or not all(isinstance(s, str) for s in subjects)
    ):
        raise ValueError("subjects must be a list of subject refs")
    subjects = list(dict.fromkeys(subjects))
    if len(subjects) > MAX_BULK:
        raise ValueError(f"at most {MAX_BULK} subjects per call")
    kinds = {"blob": [], "statement": [], "finding": []}
    for s in subjects:
        kind, _, ref = s.partition(":")
        if kind == "statement":
            try:
                uuid.UUID(ref)
            except ValueError:
                raise ValueError(f"malformed subject {s[:80]}") from None
        elif not (BLOB if kind == "blob" else FINDING).fullmatch(s):
            raise ValueError(f"malformed subject {s[:80]}")
        kinds[kind].append(ref if kind != "statement" else uuid.UUID(ref))
    known = await _known(db, kinds)
    missing = [s for s in subjects if s not in known]
    if missing:
        raise ValueError(f"unknown subject {missing[0][:80]}")
    visible = await _visible(db, investigation, kinds)
    if any(s not in visible for s in subjects):
        raise PermissionError("a subject is not in this investigation")
    return subjects


def _check(priority, due):
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
    if due is None:
        return None
    try:
        return date.fromisoformat(due)
    except (TypeError, ValueError):
        raise ValueError("due must be an ISO date") from None


async def _commit(db):
    """The partial unique index refuses a second open assignment for one subject."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_assignment_open_subject" in str(exc):
            raise ValueError(
                "a subject already has an open assignment, retry"
            ) from None
        raise


async def bulk(db, user, item):
    investigation, assignee = item.get("investigation"), item.get("assignee")
    priority = item.get("priority", "normal")
    due = _check(priority, item.get("due"))
    if not isinstance(assignee, int) or isinstance(assignee, bool):
        raise ValueError("assignee must be a user id")
    subjects = await check_subjects(db, investigation, item.get("subjects"))
    await eligible(db, assignee)
    existing = {
        a.subject: a
        for a in await db.scalars(
            select(Assignment)
            .where(
                Assignment.investigation == investigation,
                Assignment.subject.in_(subjects),
                Assignment.status == "open",
            )
            .with_for_update()
        )
    }
    now, changed = datetime.now(UTC), 0
    for s in subjects:
        a = existing.get(s)
        if a is None:
            a = Assignment(
                id=uuid.uuid4(),
                investigation=investigation,
                subject=s,
                assignee_id=assignee,
                assigned_by=user.id,
                due=due,
                priority=priority,
                status="open",
                created_at=now,
                updated_at=now,
            )
            db.add(a)
            try:
                await db.flush()
            except IntegrityError:
                await _commit(db)  # rolls back and raises the 400
                raise
            action, detail = "assigned", {"to": assignee}
        else:
            if (a.assignee_id, a.due, a.priority) == (assignee, due, priority):
                continue
            action = "reassigned" if a.assignee_id != assignee else "updated"
            detail = {
                "from": a.assignee_id,
                "to": assignee,
                "due": due and due.isoformat(),
                "priority": priority,
            }
            a.assignee_id, a.assigned_by = assignee, user.id
            a.due, a.priority, a.updated_at = due, priority, now
        db.add(
            AssignmentEvent(
                assignment_id=a.id, actor_id=user.id, action=action, detail=detail
            )
        )
        changed += 1
    await _commit(db)
    if changed and assignee != user.id:
        await notify(
            db,
            assignee,
            "assigned",
            "Work assigned to you",
            f"{changed} item{'s' if changed != 1 else ''}",
            f"/sources?investigation={quote(investigation)}&assignee=me",
            None,
        )
    return {"assigned": changed, "unchanged": len(subjects) - changed}


async def update(db, user, assignment_id, item):
    a = await db.get(Assignment, assignment_id, with_for_update=True)
    if a is None:
        raise LookupError("assignment not found")
    changes, detail = [], {}
    if "status" in item:
        if item["status"] not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        detail["status"] = [a.status, item["status"]]
        a.status = item["status"]
        changes.append("status")
    if "assignee" in item:
        target = item["assignee"]
        if not isinstance(target, int) or isinstance(target, bool):
            raise ValueError("assignee must be a user id")
        await eligible(db, target)
        detail["from"], detail["to"] = a.assignee_id, target
        a.assignee_id, a.assigned_by = target, user.id
        changes.append("reassigned")
    if "due" in item or "priority" in item:
        a.priority = item.get("priority", a.priority)
        if a.priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
        a.due = _check(a.priority, item["due"]) if "due" in item else a.due
        detail["priority"], detail["due"] = a.priority, a.due and a.due.isoformat()
        changes.append("updated")
    if not changes:
        raise ValueError("nothing to change")
    a.updated_at = datetime.now(UTC)
    for action in changes:
        db.add(
            AssignmentEvent(
                assignment_id=a.id, actor_id=user.id, action=action, detail=detail
            )
        )
    await _commit(db)
    if "reassigned" in changes and a.assignee_id != user.id:
        await notify(
            db,
            a.assignee_id,
            "assigned",
            "Work assigned to you",
            "1 item",
            f"/sources?investigation={quote(a.investigation)}&assignee=me",
            None,
        )
    return row(a)


def row(a):
    return {
        "id": str(a.id),
        "investigation": a.investigation,
        "subject": a.subject,
        "assignee": a.assignee_id,
        "assigned_by": a.assigned_by,
        "due": a.due.isoformat() if a.due else None,
        "priority": a.priority,
        "status": a.status,
        "updated_at": a.updated_at.isoformat(),
    }


async def listing(db, investigation, assignee, status, page, per_page):
    if not 1 <= per_page <= MAX_PAGE or page < 1:
        raise ValueError(f"per_page must be 1 to {MAX_PAGE}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    q = select(Assignment)
    if investigation:
        q = q.where(Assignment.investigation == investigation)
    if assignee is not None:
        q = q.where(Assignment.assignee_id == assignee)
    if status:
        q = q.where(Assignment.status == status)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.scalars(
        q.order_by(Assignment.due.asc().nulls_last(), Assignment.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    return {"items": [row(a) for a in rows], "total": total, "perPage": per_page}


async def workload(db, investigation):
    today = datetime.now(UTC).date()
    q = (
        select(
            Assignment.assignee_id,
            func.count(),
            func.count().filter(Assignment.due < today),
        )
        .where(Assignment.status == "open")
        .group_by(Assignment.assignee_id)
    )
    if investigation:
        q = q.where(Assignment.investigation == investigation)
    return {
        "items": [
            {"assignee": u, "open": n, "overdue": late}
            for u, n, late in (await db.execute(q)).all()
        ]
    }


async def analysts(db, investigation):
    load = {w["assignee"]: w for w in (await workload(db, investigation))["items"]}
    users = (await db.execute(select(User).where(User.active.is_(True)))).scalars()
    return {
        "items": [
            {
                "id": u.id,
                "name": u.name or u.email,
                "open": load.get(u.id, {}).get("open", 0),
                "overdue": load.get(u.id, {}).get("overdue", 0),
            }
            for u in users
            if any(u.has_role(r) for r in ("analyst", "admin"))
        ]
    }
