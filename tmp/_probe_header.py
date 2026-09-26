import re
import textual.widgets._header as m
src = open(m.__file__, encoding="utf-8").read()
for i, ln in enumerate(src.splitlines(), 1):
    if re.search(r"class |component_class|DEFAULT_CSS|HeaderTitle|HeaderClock|self\.title|sub_title", ln):
        print(i, ln.rstrip()[:130])
