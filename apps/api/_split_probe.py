import pathlib

path = pathlib.Path("src/prodavan/application/migration/m04e_ports.py")
source = path.read_text(encoding="utf-8")
marker = "# ---------------------------------------------------------------------------\n# EvidenceStore boundary"
index = source.index(marker)
print("total", len(source), "marker at", index)
print("head ends with:", repr(source[index - 120 : index]))
