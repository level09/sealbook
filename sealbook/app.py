from stk import Stk

from sealbook import (
    agents,
    anchor,
    bayanat,
    casemap,
    cases,
    collaboration,
    desk,
    discovery,
    entities,
    guide,
    ingest,
    ledger,
    maps,
    mcp,
    monitor,
    outbound,
    packages,
    people,
    places,
    record,
    research,
    review,
    runs,
    search,
    sheets,
    shell,
    sources,
    speech,
    stories,
    thumbs,
    watch,
)
from sealbook.settings import Config

# Each extension is a package with init_app(app). The ones that add a rail entry
# come first, in rail order; the rest only add routes and commands.
extensions = [
    ledger,
    shell,
    review,
    desk,
    collaboration,
    discovery,
    sources,
    people,
    watch,
    places,
    stories,
    packages,
    casemap,
    agents,
    guide,
    outbound,
    ingest,
    mcp,
    monitor,
    entities,
    maps,
    search,
    thumbs,
    sheets,
    bayanat,
    research,
    cases,
    runs,
    speech,
    anchor,
    record,
]

app = Stk(__name__, config=Config, extensions=extensions)


def create_app(config=Config):
    """Another app over the same extensions: tests and scripts pass their config."""
    return Stk(__name__, config=config, extensions=extensions)
