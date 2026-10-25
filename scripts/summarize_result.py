#!/usr/bin/env python3
"""Lee el JSON que `worker/run.py` (modo score) emitió tras `##CHALLENGE_RESULT##` y escribe
un resumen legible en GITHUB_STEP_SUMMARY — se ve directo en la página del run, sin descargar
ningún artifact. A diferencia de la versión anterior de este script, ya no parsea XML de
Surefire directo: `run.py` ya arma `feedback.correctness.tests` con el detalle por método, así
que este script solo lo formatea.

Uso: summarize_result.py <ruta al archivo con la línea ##CHALLENGE_RESULT##{json}>
"""

import json
import os
import sys

RESULT_MARKER = "##CHALLENGE_RESULT##"


def extract_result(output_path):
    with open(output_path, encoding="utf-8") as f:
        lines = f.readlines()
    for line in reversed(lines):
        idx = line.find(RESULT_MARKER)
        if idx >= 0:
            return json.loads(line[idx + len(RESULT_MARKER):])
    return None


def main():
    if len(sys.argv) < 2:
        print("uso: summarize_result.py <archivo-con-salida-de-run.py>", file=sys.stderr)
        sys.exit(1)

    result = extract_result(sys.argv[1])
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if result is None:
        output = "## Resultado de la verificación\n\n### ⚠️ No se encontró ningún resultado en la salida del worker.\n"
        _write(output, summary_path)
        return

    status = result.get("status")
    score = result.get("score")
    feedback = result.get("feedback") or {}

    lines = ["## Resultado de la verificación"]

    if status == "COMPLETED":
        lines.append(f"### {'✅' if (score or 0) >= 3 else '⚠️'} Calificado — {score}/5")
    else:
        lines.append("### ❌ No se pudo calificar")
        error = feedback.get("error")
        if error:
            lines.append(f"**Error**: {error}")
        _write("\n\n".join(lines) + "\n", summary_path)
        return

    correctness = feedback.get("correctness") or {}
    coverage = feedback.get("coverage") or {}
    lines.append(
        f"**Corrección**: {correctness.get('passed', 0)}/{correctness.get('total', 0)} pruebas pasaron "
        f"({correctness.get('score_percent', 0)}%)"
    )
    if coverage.get("enabled"):
        lines.append(
            f"**Cobertura**: {coverage.get('percent', 0)}% (umbral {coverage.get('threshold_percent', 0)}%)"
        )
    else:
        lines.append("**Cobertura**: no exigida para este reto")

    warning = feedback.get("warning")
    if warning:
        lines.append(f"⚠️ {warning}")
    excerpt = feedback.get("build_output_excerpt")
    if excerpt:
        lines.append(f"<details>\n<summary>Salida del build</summary>\n\n```\n{excerpt}\n```\n\n</details>")

    for test in correctness.get("tests", []):
        if test.get("passed"):
            lines.append(f"✅ `{test.get('name')}`")
        else:
            message = test.get("message") or ""
            summary_line = f"❌ <code>{test.get('name')}</code>"
            if message:
                summary_line += f" — {message.splitlines()[0]}"
            block = f"<details>\n<summary>{summary_line}</summary>\n\n```\n{message}\n```\n\n</details>"
            lines.append(block)

    _write("\n\n".join(lines) + "\n", summary_path)


def _write(output, summary_path):
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
