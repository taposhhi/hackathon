"""python cli.py input.json [output.json]  -> runs the same solver without the server."""
import sys
from app import roster_from_bytes
import json

code, out = roster_from_bytes(open(sys.argv[1], "rb").read())
text = json.dumps(out, indent=2, ensure_ascii=False)
if len(sys.argv) > 2:
    open(sys.argv[2], "w").write(text + "\n")
else:
    print(text)
sys.exit(0 if code == 200 else 1)
