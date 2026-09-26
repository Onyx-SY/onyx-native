import sys, runpy
sys.path.insert(0, ".")
mode = sys.argv[1]
import lib.terminal.input_lib as il
_orig = il.PromptSession
def _patched(*a, **kw):
    if mode != "hist": kw.pop("history", None)
    if mode != "editor": kw.pop("enable_open_in_editor", None)
    if mode != "icase": kw.pop("search_ignore_case", None)
    return _orig(*a, **kw)
il.PromptSession = _patched
sys.argv = ["test"]
runpy.run_path("test/test_hist_multiline_e2e.py", run_name="__main__")
