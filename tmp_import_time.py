import time
t = time.perf_counter()
import Onyx
print("import Onyx cost ms:", round((time.perf_counter() - t) * 1000, 1))
