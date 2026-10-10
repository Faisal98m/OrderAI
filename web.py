"""Compatibility launcher: preserve python web.py and flask --app web run."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("orderai.services.web", run_name="__main__")
else:
    import sys
    from orderai.services import web as _web
    sys.modules[__name__] = _web
