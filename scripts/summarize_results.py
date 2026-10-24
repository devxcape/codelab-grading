#!/usr/bin/env python3
"""Lee los reportes de Surefire y escribe un resumen legible en GITHUB_STEP_SUMMARY."""

import glob
import os
import sys
import xml.etree.ElementTree as ET

extra_found = sys.argv[1] if len(sys.argv) > 1 else "false"

report_files = sorted(glob.glob("student-repo/target/surefire-reports/*.xml"))

total = 0
passed = 0
details = []

for report_file in report_files:
    try:
        root = ET.parse(report_file).getroot()
    except ET.ParseError:
        continue
    for testcase in root.findall("testcase"):
        total += 1
        name = testcase.get("name", "?")
        classname = testcase.get("classname", "?")
        failure = testcase.find("failure")
        error = testcase.find("error")
        problem = failure if failure is not None else error
        if problem is not None:
            message = (problem.get("message") or "").splitlines()[0]
            line = f"- ❌ `{classname}.{name}`"
            if message:
                line += f" — {message}"
            details.append(line)
        else:
            passed += 1
            details.append(f"- ✅ `{classname}.{name}`")

lines = [
    "## Resultado de la verificación",
    "",
    f"**{passed}/{total} pruebas pasaron**",
    "",
    "Reto adicional asignado: " + ("sí" if extra_found == "true" else "no"),
    "",
]
lines.extend(details)
lines.append("")

summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
output = "\n".join(lines) + "\n"
if summary_path:
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(output)
else:
    print(output)
