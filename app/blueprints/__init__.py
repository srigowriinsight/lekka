def register(app):
    from . import accounts, auth, entries, grid, heads, templates
    for bp in (auth.bp, grid.bp, entries.bp, heads.bp, templates.bp, accounts.bp):
        app.register_blueprint(bp)
