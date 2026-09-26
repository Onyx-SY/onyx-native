import sys, time
sys.path.insert(0, ".")
from prompt_toolkit import PromptSession
from lib.terminal.input_lib import OnyxHistory, _HISTORY_BUFFER

mode = sys.argv[1]
_HISTORY_BUFFER[:] = ["cat > a.txt << EOF\n12\nls\nbd\nEOF", "echo hi"]
kw = {}
if mode == "with":
    kw["history"] = OnyxHistory()
t = time.time()
try:
    r = PromptSession(**kw).prompt("> ")
    print("returned", repr(r))
except EOFError:
    print("EOFError")
except Exception as e:
    print("ERR", type(e).__name__, e)
print("elapsed %.2fs" % (time.time() - t))
