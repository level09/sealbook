"""Cases: the list people pick from, creating one on purpose, and the check that
intake names a case that exists. A case is never created by typing into intake."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stk.cases.models import Case
from stk.ledger.models import Acquisition
from stk.people import data as people

MAX_NAME = 60
RECENT_ORIGINS = 6


class Unknown(LookupError):
    """Intake named a case that does not exist."""


class Taken(ValueError):
    """A case with this name (ignoring case and spacing) exists."""

    def __init__(self, match: Case):
        super().__init__(f"A case named {match.name} already exists.")
        self.match = match


class Close(ValueError):
    """A case with a very similar name exists; the person confirms it is new."""

    def __init__(self, match: Case):
        super().__init__(f"Close to {match.name}. Same case?")
        self.match = match


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def close(name: str, cases: list[Case]) -> Case | None:
    """The nearest existing case within two edits, or one whose name contains this
    one (or the reverse), for names of four characters or more."""
    key = _key(name)
    if len(key) < 4:
        return None
    near = [
        (_distance(key, _key(c.name)), c)
        for c in cases
        if _distance(key, _key(c.name)) <= 2
        or key in _key(c.name)
        or _key(c.name) in key
    ]
    return min(near, key=lambda x: x[0])[1] if near else None


async def all_cases(db: AsyncSession) -> list[Case]:
    return list((await db.scalars(select(Case).order_by(Case.slug))).all())


async def require(db: AsyncSession, slug: str | None) -> Case:
    """The case intake writes to. Unknown or missing refuses; nothing is created."""
    slug = (slug or "").strip()
    found = await db.scalar(select(Case).where(Case.slug == slug)) if slug else None
    if found is None:
        raise Unknown(
            f"There is no case named {slug}. Pick one from the list or create it first."
            if slug
            else "Choose the case these items belong to."
        )
    return found


async def create(
    db: AsyncSession, name: str, description: str, by, confirm: bool = False
) -> Case:
    name = " ".join((name or "").split())
    if not 0 < len(name) <= MAX_NAME:
        raise ValueError(f"A case name is 1 to {MAX_NAME} characters.")
    cases = await all_cases(db)
    if same := next((c for c in cases if _key(c.name) == _key(name)), None):
        raise Taken(same)
    if same := next((c for c in cases if _key(c.slug) == _key(name)), None):
        raise Taken(same)
    if not confirm and (near := close(name, cases)):
        raise Close(near)
    case = Case(
        slug=name, name=name, description=(description or "").strip(), created_by=by
    )
    db.add(case)
    await db.flush()
    return case


async def ensure(db: AsyncSession, slug: str, by=None) -> Case:
    """For seeds and scripts that write acquisitions directly, not for intake."""
    found = await db.scalar(select(Case).where(Case.slug == slug))
    if found is None:
        found = Case(slug=slug, name=slug, description="", created_by=by)
        db.add(found)
        await db.flush()
    return found


async def listing(db: AsyncSession) -> list[dict]:
    """Every case with its source and people counts, when something was last added,
    and the origins recently used for it (non-web), newest first."""
    cases = await all_cases(db)
    inv = Acquisition.context["investigation"].astext
    stats = {
        slug: (n, last)
        for slug, n, last in await db.execute(
            select(
                inv,
                func.count(func.distinct(Acquisition.blob_sha256)),
                func.max(Acquisition.at),
            )
            .where(inv.is_not(None))
            .group_by(inv)
        )
    }
    origins: dict[str, list[str]] = {}
    for slug, origin, _ in await db.execute(
        select(inv, Acquisition.origin, func.max(Acquisition.at).label("at"))
        .where(inv.is_not(None), ~Acquisition.origin.startswith("url:"))
        .group_by(inv, Acquisition.origin)
        .order_by(func.max(Acquisition.at).desc())
    ):
        seen = origins.setdefault(slug, [])
        if len(seen) < RECENT_ORIGINS:
            seen.append(origin)
    groups, _ = await people.identity(db)
    out = []
    for c in cases:
        n, last = stats.get(c.slug, (0, None))
        out.append(
            {
                "id": str(c.id),
                "slug": c.slug,
                "name": c.name,
                "description": c.description,
                "sources": n,
                "people": len(await people.roster(db, c.slug, groups)) if n else 0,
                "last_added": last.isoformat() if last else None,
                "created_at": c.created_at.isoformat(),
                "origins": origins.get(c.slug, []),
            }
        )
    return out


def row(c: Case) -> dict:
    return {
        "id": str(c.id),
        "slug": c.slug,
        "name": c.name,
        "description": c.description,
    }
