#!/usr/bin/env python3
"""Lee los reportes de Surefire y escribe un resumen legible en GITHUB_STEP_SUMMARY.

Todo el detalle (incluido el stack trace completo de cada falla) queda en el resumen del
job, dentro de bloques colapsables <details> — así se ve directo en la página del run, sin
tener que descargar el artifact de Surefire.
"""

import glob
import os
import sys
import xml.etree.ElementTree as ET

extra_found = sys.argv[1] if len(sys.argv) > 1 else "false"
extra_class_name = sys.argv[2] if len(sys.argv) > 2 else ""


def build_success(path):
    try:
        with open(path, encoding="utf-8") as f:
            return "BUILD SUCCESS" in f.read()
    except FileNotFoundError:
        return False


base_ok = build_success("/tmp/test-output-base.txt")
extra_ok = build_success("/tmp/test-output-extra.txt") if extra_found == "true" else None

report_files = sorted(glob.glob("student-repo/target/surefire-reports/*.xml"))

groups = {"Verificación base": [], "Reto adicional": []}
group_totals = {"Verificación base": [0, 0], "Reto adicional": [0, 0]}  # [passed, total]
total_time = 0.0

for report_file in report_files:
    try:
        root = ET.parse(report_file).getroot()
    except ET.ParseError:
        continue
    for testcase in root.findall("testcase"):
        classname = testcase.get("classname", "?")
        name = testcase.get("name", "?")
        time_s = testcase.get("time", "0")
        try:
            total_time += float(time_s)
        except ValueError:
            pass

        is_extra = extra_class_name and (
            classname == extra_class_name or classname.endswith("." + extra_class_name)
        )
        group = "Reto adicional" if is_extra else "Verificación base"
        group_totals[group][1] += 1

        failure = testcase.find("failure")
        error = testcase.find("error")
        problem = failure if failure is not None else error

        if problem is not None:
            message = (problem.get("message") or "").splitlines()[0].strip()
            trace = (problem.text or "").strip()
            summary_line = f"❌ <code>{classname}.{name}</code> ({time_s}s)"
            if message:
                summary_line += f" — {message}"
            block = f"<details>\n<summary>{summary_line}</summary>\n\n```\n{trace}\n```\n\n</details>"
            groups[group].append(block)
        else:
            group_totals[group][0] += 1
            groups[group].append(f"✅ `{classname}.{name}` ({time_s}s)")

overall_ok = base_ok and (extra_ok is None or extra_ok)

lines = [
    "## Resultado de la verificación",
    ("### ✅ Aprobado" if overall_ok else "### ❌ No aprobado"),
    f"Tiempo total de ejecución: {total_time:.2f}s",
]

table = ["| Grupo | Resultado | Pruebas |", "|---|---|---|"]
table.append(
    f"| Verificación base | {'✅' if base_ok else '❌'} | "
    f"{group_totals['Verificación base'][0]}/{group_totals['Verificación base'][1]} |"
)
if extra_found == "true":
    table.append(
        f"| Reto adicional | {'✅' if extra_ok else '❌'} | "
        f"{group_totals['Reto adicional'][0]}/{group_totals['Reto adicional'][1]} |"
    )
else:
    table.append("| Reto adicional | — | sin asignar |")
lines.append("\n".join(table))

for group_name in ("Verificación base", "Reto adicional"):
    if not groups[group_name]:
        continue
    lines.append(f"#### {group_name}")
    lines.extend(groups[group_name])

summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
output = "\n\n".join(lines) + "\n"
if summary_path:
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(output)
else:
    print(output)
