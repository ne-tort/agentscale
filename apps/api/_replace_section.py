import pathlib

path = pathlib.Path("tests/migration/m04e_ports/test_m04e_ports_contract.py")
source = path.read_text(encoding="utf-8")
start = source.index("# ---------------------------------------------------------------------------\n# 5. EvidenceStore")
end = source.index("# ---------------------------------------------------------------------------\n# 6. No in-memory path")
section = pathlib.Path("_section5.py").read_text(encoding="utf-8")
path.write_text(source[:start] + section + source[end:], encoding="utf-8")
print("ok", len(source), start, end)
