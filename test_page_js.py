"""The page's script must parse and declare everything it calls at load time.

A careless edit to index.html removed 109 lines -- toast, oops, modal, the
state declarations -- and left valid JavaScript behind, so every check that
looked at syntax passed while the page died on first render. The server logs
showed it: the browser fetched /, /api/power and /api/stats, and never got as
far as /api/movies.
"""
import re
import shutil
import subprocess
import sys

HTML = "static/index.html"
# Names the boot path calls before the first paint; if one is missing the page
# throws before it ever asks for /api/movies.
REQUIRED = ["toast", "oops", "modal", "barFor", "tierOf", "resOf", "inRes",
            "inBucket", "paintTabs", "paintGrid", "TIER", "INFLIGHT",
            "KIND_LABEL", "MOVIES", "SHOWS", "JOBS"]


def main():
    src = open(HTML).read()
    js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", src, re.S))
    assert js.strip(), "no inline script found"

    for name in REQUIRED:
        declared = re.search(
            rf"^\s*(?:function\s+{name}\b|(?:const|let|var)\s+{name}\b)|"
            rf"(?:const|let|var)\s+[^;\n]*\b{name}\s*=", js, re.M)
        assert declared, f"{name} is used but never declared"

    node = shutil.which("node")
    if node:
        p = subprocess.run([node, "--check", "-"], input=js,
                           capture_output=True, text=True)
        assert p.returncode == 0, f"syntax error:\n{p.stderr}"
    else:
        print("(node no encontrado: solo se comprobaron las declaraciones)")
    print("ok")


if __name__ == "__main__":
    sys.exit(main())
