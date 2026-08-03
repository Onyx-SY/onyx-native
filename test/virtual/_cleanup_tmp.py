import os
for f in [
    "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test/home/u0_a305/onyx/onyx/test/virtual/_run_tmp.py",
    "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test/home/u0_a305/onyx/onyx/test/virtual/test_ai_sandbox_tmp.py",
]:
    try:
        os.remove(f)
        print("deleted:", f)
    except Exception as e:
        print("skip:", f, e)
