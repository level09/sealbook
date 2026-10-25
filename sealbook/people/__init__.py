def init_app(app):
    from sealbook.people.cli import erase_cmd
    from sealbook.people.views import bp_people

    app.register_blueprint(bp_people)
    app.cli.add_command(erase_cmd)
    app.navigation.append(
        {
            "title": "People",
            "icon": "ti ti-users",
            "hue": "is-person",
            "to": "/people",
            "match": "/people",
        }
    )
