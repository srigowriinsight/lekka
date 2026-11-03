def register(app):
    from . import accounts, auth, entries, grid, heads, templates, users
    for bp in (auth.bp, grid.bp, entries.bp, heads.bp, templates.bp, accounts.bp, users.bp):
        app.register_blueprint(bp)
