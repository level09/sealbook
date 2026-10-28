def init_app(app):
    from sealbook.agents.cli import agent_agreement
    from sealbook.agents.views import bp_agents

    app.register_blueprint(bp_agents)
    app.cli.add_command(agent_agreement)
    app.navigation.append(
        {
            "title": "Agents",
            "icon": "ti ti-robot-face",
            "hue": "is-agent",
            "to": "/agents/",
            "match": "/agents",
        }
    )
