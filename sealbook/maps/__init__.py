"""Map tiles and the offline basemap."""


def init_app(app):
    from sealbook.maps.cli import basemap
    from sealbook.maps.views import bp_maps

    app.register_blueprint(bp_maps)
    app.cli.add_command(basemap)
