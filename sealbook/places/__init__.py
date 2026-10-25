"""Place records: GeoNames rows and people-recorded sites, found by normalised name."""

import re

from sealbook.search.arabic import normalize


def normalise(name: str) -> str:
    """The search normaliser plus case and whitespace folding, for exact name lookup."""
    return re.sub(r"\s+", " ", normalize(name)).strip().lower()


def init_app(app):
    from sealbook.places.cli import places
    from sealbook.places.views import bp_places

    app.register_blueprint(bp_places)
    app.cli.add_command(places)
    app.navigation.append(
        {
            "title": "Places",
            "icon": "ti ti-map-pin",
            "to": "/places",
            "match": "/places",
        }
    )
