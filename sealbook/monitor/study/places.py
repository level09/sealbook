"""The planted places with coordinates, imported once as sites a person recorded;
the case analyst confirms the place of every written located claim that names one.
A place without coordinates stays a name and is never drawn."""

from __future__ import annotations

from sealbook.ledger.write import append
from sealbook.monitor.data import LOCATED
from sealbook.monitor.seed_study import import_places
from sealbook.monitor.study import step
from sealbook.places import resolve


@step("places")
async def places(st, act, args) -> None:
    if st.sites is None:
        st.sites = await import_places(st.s, st.spec)
    for c in st.spec["claims"]:
        found = st.written.get(c["key"])
        if (
            found is None
            or found.id in st.confirmed
            or c["place"] not in st.sites
            or c["prop"] not in LOCATED
        ):
            continue
        await append(
            st.session,
            resolve.confirmation(
                found.id, st.sites[c["place"]].id, c["value"], st.analyst.id
            ),
            sign=st.s.assertion,
        )
        st.confirmed.add(found.id)
