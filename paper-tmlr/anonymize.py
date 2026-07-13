"""Create the anonymized (double-blind) TMLR version from recuse-tmlr.tex."""
import re

s = open("recuse-tmlr.tex", encoding="utf-8").read()
# 1. submission (anonymous) mode instead of preprint (named) mode
s = s.replace(r"\usepackage[preprint]{tmlr}", r"\usepackage{tmlr}")
# 2. strip the author block (name + email + the arXiv self-reference footnote)
s = re.sub(r"\\author\{.*?arXiv:2606\.06460\.\}\}",
           lambda m: r"\author{Anonymous authors}", s, flags=re.S)
open("recuse-tmlr-anon.tex", "w", encoding="utf-8").write(s)
print("created recuse-tmlr-anon.tex")
