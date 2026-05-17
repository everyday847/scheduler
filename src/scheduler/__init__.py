from importlib import import_module


def __getattr__(name):
    if name == "app":
        from .web_app import app

        return app
    if name == "cli":
        return import_module(".cli", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
