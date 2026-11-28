"""Test-only stand-in for Streamlit: records what each page draws, so every page and button path can be run
without a browser. It checks that the page code runs; it does not check how the page looks."""
import sys
LOG = []
class _SS(dict):
    def __getattr__(self, k):
        try: return self[k]
        except KeyError: raise AttributeError(k)
    def __setattr__(self, k, v): self[k] = v
session_state = _SS()
class _Stop(Exception): pass
PAGES_SEEN = []
class _Switch(Exception):
    pass
class _Nav:
    def __init__(self, sections): self.sections = sections
    def run(self):
        want = session_state.get("_test_page")
        for pages in self.sections.values():
            for pg in pages:
                if pg._ptitle == want:
                    LOG.append(("RUN", (pg._ptitle,)))
                    return pg._fn()
        raise KeyError(want)
class Stub:
    def __init__(self, name="st"): self._n = name
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __setattr__(self, k, v): object.__setattr__(self, k, v)
    def __getattr__(self, k):
        if k.startswith("_"): raise AttributeError(k)
        def f(*a, **kw):
            LOG.append((k, a[:1]))
            if k == "tabs":
                return [Stub() for _ in a[0]]
            if k == "Page":
                page = Stub(); page._fn = a[0]; page._ptitle = kw.get("title"); PAGES_SEEN.append(page)
                return page
            if k == "navigation":
                return _Nav(a[0])
            if k == "switch_page":
                raise _Switch(a[0])
            if k == "segmented_control":
                return kw.get("default")
            if k == "number_input":
                return kw.get("value", 0)
            if k == "toggle":
                return kw.get("value", False)
            if k == "columns":
                n = a[0] if isinstance(a[0], int) else len(a[0])
                return [Stub() for _ in range(n)]
            if k in ("button",): return kw.get("key") in session_state.get("_press", ()) or (a and a[0] in session_state.get("_press", ()))
            if k in ("text_input",): return ""
            if k == "chat_input": return None
            if k == "checkbox": return kw.get("value", False)
            if k in ("selectbox", "radio"):
                opts = list(a[1]) if len(a) > 1 else list(kw.get("options", []))
                key = kw.get("key")
                if key and key in session_state: return session_state[key]
                v = opts[kw.get("index", 0)]
                if key: session_state[key] = v
                return v
            if k == "metric":
                LOG.append(("METRIC", (a[0], a[1] if len(a) > 1 else None)))
            if k == "stop": raise _Stop()
            if k in ("cache_data", "cache_resource"):
                if a and callable(a[0]): return a[0]
                return lambda fn: fn
            return Stub()
        return f
_root = Stub()
def __getattr__(k):
    if k == "secrets": raise FileNotFoundError("no secrets")
    if k == "sidebar": return _root
    return getattr(_root, k)
class _Secrets:
    def __contains__(self, k): raise FileNotFoundError("no secrets")
secrets = _Secrets()
sidebar = _root
