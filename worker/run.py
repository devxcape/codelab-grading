#!/usr/bin/env python3
"""
Corre DENTRO de un Job de Kubernetes aislado (namespace challenge-sandbox) — clona el repo de
un estudiante, inyecta el test JUnit generado por IA (ya aprobado-por-approach por el
profesor) en el mismo paquete que la app del estudiante, corre los tests con cobertura (Gradle
o Maven, autodetectado — ver detect_build_tool), y calcula el score final. challenge-runner (el
control-plane Java) lee el resultado de los LOGS de este proceso, nunca de un callback — por
eso la última línea de stdout, y solo esa, lleva el marcador RESULT_MARKER seguido del JSON del
resultado. Todo lo demás que este script imprime es diagnóstico y se ignora.

El test inyectado usa `@SpringBootTest(webEnvironment = RANDOM_PORT)` + `TestRestTemplate` —
levanta la app REAL del estudiante en memoria dentro del propio proceso de test, así que este
script no empaqueta ni lanza ningún jar por separado ni le pega por HTTP externo — todo el
ciclo "levantar la app y probarla" es responsabilidad de JUnit/Spring dentro del test run.

Ver lms-challenge/CLAUDE.md "Enunciados y tests generados por IA" y
challenge-runner-worker/CLAUDE.md para el contrato completo.

⚠️ ESTE ARCHIVO TIENE UNA COPIA PÚBLICA en `devxcape/codelab-grading` (carpeta `worker/`),
usada por el pipeline de GitHub Actions para retos en `executionMode=GITHUB_ACTIONS` (ver
lms-challenge/CLAUDE.md "GitHub Actions como motor de calificación (repo del estudiante)") —
ESTE archivo (en devxcape-lms, privado) sigue siendo la fuente de verdad. Cualquier cambio acá
debe replicarse A MANO a esa copia pública; no hay sincronización automática.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    # Parseo endurecido de XML (2ª revisión de equipo-devops sobre el modo `score`, que lee
    # evidencia producida por un build que el estudiante influye -- ver "Script de verificación
    # protegido" en challenge-runner-worker/CLAUDE.md para el mismo criterio ya aplicado ahí).
    # Fallback silencioso al stdlib si defusedxml no está instalado -- preserva el camino K8s
    # (imagen actual, sin pip install) sin cambio de comportamiento mientras no se actualice el
    # Dockerfile.
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

RESULT_MARKER = "##CHALLENGE_RESULT##"
# Límite defensivo antes de parsear cualquier reporte XML -- un reporte real de Surefire/JaCoCo
# nunca se acerca a esto; un archivo más grande se trata como corrupto/hostil, nunca se parsea.
MAX_REPORT_FILE_SIZE_BYTES = 10 * 1024 * 1024
# Cuánto de la salida cruda del build se guarda en el feedback cuando ni corrió el test
# inyectado (ver _extract_build_excerpt/su uso en main()).
BUILD_OUTPUT_EXCERPT_CHARS = 6000
# Líneas de contexto a incluir ANTES/DESPUÉS de cada línea de error/advertencia real --
# pedido explícito del usuario (2026-10-13): un recorte plano del PRINCIPIO del output (lo que
# hacía esto antes) podía dejar la línea de error enterrada entre ruido INFO de Maven/Gradle,
# sin alcanzar a ver qué la originó. 3 líneas antes alcanza para ver, por ejemplo, qué archivo
# se estaba compilando o qué comando disparó la falla; 1 después porque muchos mensajes de
# javac/Maven se parten en 2-3 líneas ("cannot find symbol" + "symbol: ..." + "location: ...").
_BUILD_EXCERPT_CONTEXT_BEFORE = 3
_BUILD_EXCERPT_CONTEXT_AFTER = 1
_INTERESTING_BUILD_LINE_RE = re.compile(
    r"\[ERROR\]|\[WARNING\]|error:|warning:|Exception\b|FAILURE|BUILD FAILED", re.IGNORECASE,
)
REPO_DIR = os.environ.get("REPO_DIR", "/workspace/repo")
# Configurable vía env var -- la imagen de K8s sigue montando esto en /workspace (sin cambios),
# pero un runner de GitHub Actions (modo `build`/`score`, sin ese filesystem de contenedor)
# necesita poder apuntarlo a donde sea que haya checkouteado el repo del worker público.
GRADLE_INIT_SCRIPT = os.environ.get("GRADLE_INIT_SCRIPT_PATH", "/workspace/jacoco-init.gradle")
# Carpeta de evidencia normalizada entre el modo `build` (la escribe) y `score` (la lee) --
# ver main_build/main_score. Sin efecto en el modo monolítico de siempre (main_full).
EVIDENCE_DIR = os.environ.get("EVIDENCE_DIR", "/workspace/evidence")
VERIFICATION_CLASS_NAME = "GeneratedChallengeVerificationTest"
JACOCO_MAVEN_PLUGIN_VERSION = "0.8.12"

# Pesos fijos documentados en lms-challenge/CLAUDE.md — no configurables por el profesor
# (rubric_json es puramente informativo, ver esa misma sección). Ya no hay un tercer
# componente de "contrato": eso ahora está encarnado en si el test generado pasa.
WEIGHT_CORRECTNESS = 0.7
WEIGHT_COVERAGE = 0.3

# Un solo comando de test hace todo (compila, corre tests del estudiante + el inyectado que
# levanta la app entera, mide cobertura) — sin cache de Gradle/Maven entre entregas (cada Job
# es un filesystem nuevo), así que hay que dejar margen generoso.
BUILD_TEST_TIMEOUT_SECONDS = 600

_SPRING_BOOT_APPLICATION_RE = re.compile(r"@SpringBootApplication\b")
_PACKAGE_DECLARATION_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def log(msg):
    print(f"[challenge-runner-worker] {msg}", file=sys.stderr, flush=True)


def emit_result(status, score, feedback):
    result = {"status": status, "score": score, "feedback": feedback}
    # Única línea con el marcador — challenge-runner busca exactamente esto en los logs
    # del pod. Todo lo previo es diagnóstico, se descarta.
    print(RESULT_MARKER + json.dumps(result), flush=True)


def run(cmd, cwd, timeout):
    """Devuelve (returncode, stdout_combinado) -- el stdout se usa dos veces: recortado a 4000
    chars para el log de diagnóstico de siempre, y completo (recortado más generoso en main(),
    ver BUILD_OUTPUT_EXCERPT_CHARS) para meter en el feedback cuando el build falló tan temprano
    que ni llegó a generar un reporte JUnit (correctness_total == 0) -- antes de esto, ese caso
    solo decía "revisar si el proyecto compila" sin ningún detalle real, dejando al profesor
    sin ninguna pista de qué estaba roto (ej. clases duplicadas de un refactor a medio hacer,
    una dependencia que falta en el pom.xml)."""
    log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        log(proc.stdout[-4000:] if proc.stdout else "(sin salida)")
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired:
        log(f"Comando excedió el timeout de {timeout}s")
        return -1, ""


def clone_repo(repo_url):
    """
    Un reintento con backoff corto — confirmado real: una entrega terminó en `FAILED` sin
    resultado calificado en apenas un puñado de segundos (muy poco tiempo para haber pasado
    por gradle), consistente con un `git clone` fallando rápido por un problema de red
    transitorio (DNS/conexión) dentro del NetworkPolicy restringido del sandbox — no
    reproducible corriendo el mismo repo/test fuera del cluster. Reproducir el fallo exacto
    no fue posible (el pod ya no existía para inspeccionar), así que esto es una mitigación
    razonable para la causa más probable, no una confirmación certera.
    """
    # cwd = el padre de REPO_DIR, no un "/workspace" fijo -- así el clone funciona igual sin
    # importar si REPO_DIR vino del default de K8s o de un GRADLE_INIT_SCRIPT_PATH/REPO_DIR
    # apuntado al workspace de un runner de GitHub Actions (que no tiene /workspace).
    clone_cwd = os.path.dirname(REPO_DIR) or "."
    for attempt in range(1, 3):
        code, _ = run(["git", "clone", "--depth", "1", repo_url, REPO_DIR], cwd=clone_cwd, timeout=120)
        if code == 0:
            return 0
        log(f"git clone falló (intento {attempt}/2), código {code}")
        if attempt < 2:
            time.sleep(3)
            shutil.rmtree(REPO_DIR, ignore_errors=True)
    return code


def detect_build_tool(repo_dir):
    """
    `pom.xml` en la raíz → Maven. `gradlew` en la raíz → Gradle. Si hay ambos (inusual), se
    prioriza Maven porque es lo que declara "raíz del proyecto" de forma más inequívoca (un
    `gradlew` suelto sin `build.gradle` no compilaría igual). None si no se encuentra ninguno.
    """
    if os.path.isfile(f"{repo_dir}/pom.xml"):
        return "maven"
    if os.path.isfile(f"{repo_dir}/gradlew"):
        return "gradle"
    return None


def run_gradle_tests(repo_dir):
    subprocess.run(["chmod", "+x", f"{repo_dir}/gradlew"])
    _, output = run(
        ["./gradlew", "test", "--init-script", GRADLE_INIT_SCRIPT, "--no-daemon", "--stacktrace"],
        cwd=repo_dir, timeout=BUILD_TEST_TIMEOUT_SECONDS,
    )
    return output


def run_maven_tests(repo_dir):
    """
    Usa `./mvnw` si el estudiante lo incluyó (respeta la versión de Maven que fijó), si no cae
    al `mvn` del sistema (instalado en la imagen — a diferencia de Gradle, NO exigimos que el
    estudiante traiga el wrapper, porque en la práctica la mayoría de los proyectos Maven no lo
    incluyen). JaCoCo se inyecta vía goals de línea de comando (`prepare-agent` antes de `test`,
    `report` después) — igual que `jacoco-init.gradle` para Gradle, sin tocar el `pom.xml` del
    estudiante ni exigirle que declare el plugin. `-Dmaven.test.failure.ignore=true` es
    necesario para que el reactor NO aborte apenas un test falla — sin esto, el goal `report`
    nunca llega a correr tras la primera falla y la cobertura siempre da 0% (confirmado
    corriendo esto localmente contra un fixture con un test roto a propósito). Mismo criterio
    que Gradle: "no corta si algún test falla, se sigue para poder reportar feedback real".
    """
    mvnw = f"{repo_dir}/mvnw"
    if os.path.isfile(mvnw):
        subprocess.run(["chmod", "+x", mvnw])
        mvn_cmd = "./mvnw"
    else:
        mvn_cmd = "mvn"
    jacoco = f"org.jacoco:jacoco-maven-plugin:{JACOCO_MAVEN_PLUGIN_VERSION}"
    _, output = run(
        [mvn_cmd, "-B", "-Dmaven.test.failure.ignore=true",
         f"{jacoco}:prepare-agent", "test", f"{jacoco}:report"],
        cwd=repo_dir, timeout=BUILD_TEST_TIMEOUT_SECONDS,
    )
    return output


def test_report_paths(repo_dir, build_tool):
    if build_tool == "maven":
        return glob.glob(f"{repo_dir}/**/target/surefire-reports/*.xml", recursive=True)
    return glob.glob(f"{repo_dir}/**/build/test-results/test/*.xml", recursive=True)


def coverage_report_paths(repo_dir, build_tool):
    if build_tool == "maven":
        return glob.glob(f"{repo_dir}/**/target/site/jacoco/jacoco.xml", recursive=True)
    return glob.glob(f"{repo_dir}/**/build/reports/jacoco/test/jacocoTestReport.xml", recursive=True)


def detect_spring_boot_package(repo_dir):
    """
    Busca la clase anotada @SpringBootApplication en el repo del estudiante y devuelve su
    paquete (ej. "com.example.crud") — NUNCA se le pide esto a la IA que generó el test,
    porque no puede saber de antemano qué paquete va a elegir el estudiante.
    `@SpringBootTest` busca la configuración raíz subiendo desde el paquete del test, así que
    alcanza con escribir el test inyectado en ESE MISMO paquete para que la encuentre sola.
    Retorna None si no se encuentra ninguna.
    """
    for path in glob.glob(f"{repo_dir}/**/src/main/java/**/*.java", recursive=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        if _SPRING_BOOT_APPLICATION_RE.search(content):
            match = _PACKAGE_DECLARATION_RE.search(content)
            if match:
                return match.group(1)
    return None


def write_verification_test(repo_dir, package, test_source):
    """Escribe el test generado en el mismo paquete que la app, bajo src/test/java — junto a
    (no reemplazando) los tests que el propio estudiante haya escrito. Misma ruta estándar
    para Maven y Gradle, ninguno de los dos necesita ajuste acá."""
    package_path = package.replace(".", "/")
    test_dir = os.path.join(repo_dir, "src", "test", "java", package_path)
    os.makedirs(test_dir, exist_ok=True)
    test_file = os.path.join(test_dir, f"{VERIFICATION_CLASS_NAME}.java")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(f"package {package};\n\n{test_source}\n")
    return test_file


def _testcase_detail(testcase):
    """De un <testcase> de JUnit XML, arma {name, passed, message} — `message` es el texto de
    <failure>/<error> si lo hay (recortado, puede traer stacktraces largos), None si pasó.
    Gradle agrega "()" al final del nombre del método (JUnit 5); Maven no — se normaliza acá
    para que se vea igual sin importar qué build tool corrió el test."""
    name = testcase.attrib.get("name", "?")
    if name.endswith("()"):
        name = name[:-2]
    problem = testcase.find("failure")
    if problem is None:
        problem = testcase.find("error")
    if problem is None:
        return {"name": name, "passed": True, "message": None}
    message = (problem.attrib.get("message") or problem.text or "").strip()
    return {"name": name, "passed": False, "message": message[:500]}


def _parse_xml_safely(path):
    """Devuelve el elemento raíz, o None si el archivo no existe, supera
    MAX_REPORT_FILE_SIZE_BYTES, o no es XML válido -- nunca lanza, para que el llamador lo
    trate igual que "sin datos" en vez de reventar toda la calificación por un reporte
    corrupto/hostil."""
    try:
        if os.path.getsize(path) > MAX_REPORT_FILE_SIZE_BYTES:
            log(f"Reporte descartado por tamaño (> {MAX_REPORT_FILE_SIZE_BYTES} bytes): {path}")
            return None
        return ET.parse(path).getroot()
    except Exception as e:  # noqa: BLE001 -- defusedxml lanza tipos propios (EntitiesForbidden,
        # NotSupportedError, etc.) además de ET.ParseError/OSError -- cualquier fallo de parseo
        # se trata igual, como "sin datos", nunca como un crash del worker.
        log(f"No se pudo parsear {path}: {e}")
        return None


def parse_test_results(report_paths, class_name=None):
    """
    Suma tests/failures/errors de los reportes JUnit XML (Gradle: `TEST-<paquete>.<clase>.xml`
    bajo build/test-results/test; Maven: mismo nombre de archivo bajo target/surefire-reports —
    ambos siguen el mismo esquema XML). Si `class_name` se pasa, solo cuenta y detalla el
    reporte de ESA clase — así se aísla el resultado del test inyectado (que determina la
    corrección) del resto de los tests del estudiante (que solo aportan a cobertura).
    Retorna (total, passed, details) — `details` es la lista de resultados por método
    individual del reporte filtrado, para poder mostrar qué pasó y qué falló, no solo el
    agregado.
    """
    total = 0
    passed = 0
    details = []
    for path in report_paths:
        if class_name and not os.path.basename(path).endswith(f".{class_name}.xml"):
            continue
        root = _parse_xml_safely(path)
        if root is None:
            continue
        tests = int(root.attrib.get("tests", 0))
        failures = int(root.attrib.get("failures", 0))
        errors = int(root.attrib.get("errors", 0))
        skipped = int(root.attrib.get("skipped", 0))
        total += tests
        passed += tests - failures - errors - skipped
        for testcase in root.findall("testcase"):
            if testcase.find("skipped") is not None:
                continue
            details.append(_testcase_detail(testcase))
    return total, passed, details


def parse_coverage(report_paths):
    """% de líneas cubiertas, sumando el counter LINE de todos los reportes JaCoCo (Gradle o
    Maven, mismo esquema XML) — incluye tanto el test inyectado como los del propio
    estudiante, para incentivar que escriba los suyos."""
    missed_total = 0
    covered_total = 0
    for path in report_paths:
        root = _parse_xml_safely(path)
        if root is None:
            continue
        for counter in root.findall("counter"):
            if counter.attrib.get("type") == "LINE":
                missed_total += int(counter.attrib["missed"])
                covered_total += int(counter.attrib["covered"])
    if missed_total + covered_total == 0:
        return 0.0
    return 100.0 * covered_total / (missed_total + covered_total)


def _extract_build_excerpt(output, max_chars=BUILD_OUTPUT_EXCERPT_CHARS):
    """Ventanas de contexto alrededor de cada línea de error/advertencia real, en vez de un
    recorte plano del principio del output -- ver el comentario de
    _BUILD_EXCERPT_CONTEXT_BEFORE/_INTERESTING_BUILD_LINE_RE arriba para el porqué. Ventanas
    que se superponen o quedan pegadas se fusionan (evita repetir líneas); un "…" separa
    tramos no contiguos para que quede claro que hay output omitido entre ellos. Si no se
    encuentra ninguna línea de interés (caso raro -- `main()` solo llama esto cuando
    correctness_total == 0, así que casi siempre hay un error real en el output), cae al
    recorte plano de siempre en vez de devolver vacío."""
    lines = output.splitlines()
    windows = []
    for i, line in enumerate(lines):
        if _INTERESTING_BUILD_LINE_RE.search(line):
            start = max(0, i - _BUILD_EXCERPT_CONTEXT_BEFORE)
            end = min(len(lines), i + _BUILD_EXCERPT_CONTEXT_AFTER + 1)
            windows.append((start, end))
    if not windows:
        return output[:max_chars]

    merged = [windows[0]]
    for start, end in windows[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    parts = []
    for start, end in merged:
        if parts:
            parts.append("…")
        parts.extend(lines[start:end])
    return "\n".join(parts)[:max_chars]


def _compute_score_and_emit(test_reports, coverage_reports, min_coverage_percent, coverage_requested, build_output):
    """Única implementación del cálculo de nota -- compartida entre el modo monolítico de
    siempre (`main_full`, K8s) y el modo `score` (GitHub Actions, job C) para que no exista
    ninguna segunda copia de esta lógica que pueda divergir en silencio."""
    correctness_total, correctness_passed, correctness_tests = parse_test_results(
        test_reports, class_name=VERIFICATION_CLASS_NAME,
    )
    correctness_pct = 100.0 * correctness_passed / correctness_total if correctness_total > 0 else 0.0
    coverage_pct = parse_coverage(coverage_reports)
    coverage_enabled = coverage_requested and min_coverage_percent > 0

    coverage = {"percent": round(coverage_pct, 1), "enabled": coverage_enabled}
    if coverage_enabled:
        coverage_score_pct = min(coverage_pct / min_coverage_percent, 1.0) * 100.0
        overall_pct = correctness_pct * WEIGHT_CORRECTNESS + coverage_score_pct * WEIGHT_COVERAGE
        weights = {"correctness": WEIGHT_CORRECTNESS, "coverage": WEIGHT_COVERAGE}
        coverage["threshold_percent"] = min_coverage_percent
        coverage["score_percent"] = round(coverage_score_pct, 1)
    else:
        overall_pct = correctness_pct
        weights = {"correctness": 1.0, "coverage": 0.0}

    score = round(overall_pct / 20, 2)  # escala 0-5, igual que las evaluaciones de video/texto

    feedback = {
        "correctness": {
            "total": correctness_total,
            "passed": correctness_passed,
            "score_percent": round(correctness_pct, 1),
            "tests": correctness_tests,
        },
        "coverage": coverage,
        "weights": weights,
    }
    if correctness_total == 0:
        feedback["warning"] = (
            "El test de verificación no llegó a correr (probablemente no compiló contra el "
            "código del estudiante) — revisar si el proyecto compila."
        )
        # Antes de esto, este caso no traía NINGÚN detalle real -- el profesor (o el
        # estudiante) se quedaba con el aviso genérico de arriba sin poder saber qué estaba
        # roto. Se recorta tomando el PRINCIPIO del output, no el final -- un error de
        # compilación real (Maven o Gradle) aborta el build ahí mismo, así que lo que sigue
        # después es solo el resumen final ("BUILD FAILURE"), no información nueva.
        feedback["build_output_excerpt"] = _extract_build_excerpt(build_output)
    emit_result("COMPLETED", score, feedback)


def main_full():
    """Modo monolítico de siempre -- único camino usado por K8s (ChallengeJobFactory nunca
    setea WORKER_MODE). Clona, compila, corre los tests y califica, todo en el mismo proceso.
    Sin cambios de comportamiento respecto a como funcionaba antes de los modos build/score."""
    repo_url = os.environ["REPO_URL"]
    min_coverage_percent = float(os.environ.get("MIN_COVERAGE_PERCENT", "70"))
    coverage_requested = os.environ.get("COVERAGE_ENABLED", "true").strip().lower() == "true"
    test_source = os.environ["VERIFICATION_TEST_SOURCE"]

    if clone_repo(repo_url) != 0:
        emit_result("FAILED", None, {"error": "No se pudo clonar el repositorio. Verificá que la URL sea pública y correcta."})
        return

    build_tool = detect_build_tool(REPO_DIR)
    if build_tool is None:
        emit_result("FAILED", None, {"error": "No se encontró pom.xml (Maven) ni gradlew (Gradle) en la raíz del repositorio."})
        return
    log(f"Build tool detectado: {build_tool}")

    package = detect_spring_boot_package(REPO_DIR)
    if not package:
        emit_result("FAILED", None, {"error": "No se encontró ninguna clase @SpringBootApplication en src/main/java."})
        return

    test_file = write_verification_test(REPO_DIR, package, test_source)
    log(f"Test de verificación escrito en {test_file} (paquete {package})")

    if build_tool == "maven":
        build_output = run_maven_tests(REPO_DIR)
    else:
        build_output = run_gradle_tests(REPO_DIR)

    _compute_score_and_emit(
        test_report_paths(REPO_DIR, build_tool),
        coverage_report_paths(REPO_DIR, build_tool),
        min_coverage_percent,
        coverage_requested,
        build_output,
    )


def _write_build_status(status, error=None):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    payload = {"status": status}
    if error is not None:
        payload["error"] = error
    with open(os.path.join(EVIDENCE_DIR, "status.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _copy_reports(paths, dest_dir):
    """Preserva el nombre de archivo original -- parse_test_results filtra por
    `.{class_name}.xml` en el nombre, así que renombrar rompería ese filtro."""
    os.makedirs(dest_dir, exist_ok=True)
    for path in paths:
        try:
            shutil.copy2(path, os.path.join(dest_dir, os.path.basename(path)))
        except OSError as e:
            log(f"No se pudo copiar {path} a la evidencia: {e}")


def main_build():
    """Modo `build` (GitHub Actions, job SIN permissions.id-token -- el que corre código del
    estudiante). Clona, compila, corre los tests -- pero NO calcula nota ni llama a nada: solo
    normaliza la evidencia cruda a EVIDENCE_DIR para que `main_score`, en un runner nuevo que
    nunca ejecutó código del estudiante, la lea después. Reusa clone_repo/detect_build_tool/
    detect_spring_boot_package/write_verification_test/run_maven_tests/run_gradle_tests tal
    cual -- cero lógica nueva de orquestación de build."""
    repo_url = os.environ["REPO_URL"]
    test_source = os.environ["VERIFICATION_TEST_SOURCE"]

    if clone_repo(repo_url) != 0:
        _write_build_status(
            "BUILD_FAILED", "No se pudo clonar el repositorio. Verificá que la URL sea pública y correcta.")
        return

    build_tool = detect_build_tool(REPO_DIR)
    if build_tool is None:
        _write_build_status(
            "BUILD_FAILED", "No se encontró pom.xml (Maven) ni gradlew (Gradle) en la raíz del repositorio.")
        return
    log(f"Build tool detectado: {build_tool}")

    package = detect_spring_boot_package(REPO_DIR)
    if not package:
        _write_build_status("BUILD_FAILED", "No se encontró ninguna clase @SpringBootApplication en src/main/java.")
        return

    test_file = write_verification_test(REPO_DIR, package, test_source)
    log(f"Test de verificación escrito en {test_file} (paquete {package})")

    build_output = run_maven_tests(REPO_DIR) if build_tool == "maven" else run_gradle_tests(REPO_DIR)

    _copy_reports(test_report_paths(REPO_DIR, build_tool), os.path.join(EVIDENCE_DIR, "test-reports"))
    _copy_reports(coverage_report_paths(REPO_DIR, build_tool), os.path.join(EVIDENCE_DIR, "coverage-reports"))
    with open(os.path.join(EVIDENCE_DIR, "build-output.txt"), "w", encoding="utf-8") as f:
        f.write(build_output)
    _write_build_status("BUILD_COMPLETED")


def main_score():
    """Modo `score` (GitHub Actions, job CON permissions.id-token, runner que JAMÁS ejecutó
    código del estudiante). Lee la evidencia que dejó `main_build`, califica con la MISMA
    lógica que el camino K8s (_compute_score_and_emit) -- nunca re-corre el build del
    estudiante."""
    min_coverage_percent = float(os.environ.get("MIN_COVERAGE_PERCENT", "70"))
    coverage_requested = os.environ.get("COVERAGE_ENABLED", "true").strip().lower() == "true"

    status_path = os.path.join(EVIDENCE_DIR, "status.json")
    try:
        with open(status_path, encoding="utf-8") as f:
            status = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        emit_result("FAILED", None, {"error": f"No se pudo leer la evidencia del build: {e}"})
        return

    if status.get("status") != "BUILD_COMPLETED":
        emit_result("FAILED", None, {"error": status.get("error") or "El build no terminó correctamente."})
        return

    test_reports = glob.glob(os.path.join(EVIDENCE_DIR, "test-reports", "*.xml"))
    coverage_reports = glob.glob(os.path.join(EVIDENCE_DIR, "coverage-reports", "*.xml"))
    try:
        with open(os.path.join(EVIDENCE_DIR, "build-output.txt"), encoding="utf-8") as f:
            build_output = f.read()
    except OSError:
        build_output = ""

    _compute_score_and_emit(test_reports, coverage_reports, min_coverage_percent, coverage_requested, build_output)


if __name__ == "__main__":
    # Default sin setear = comportamiento de siempre -- ChallengeJobFactory (K8s) nunca setea
    # esta env var, cero riesgo para ese camino. `build`/`score` son exclusivos del pipeline de
    # 3 jobs de GitHub Actions (devxcape/codelab-grading).
    WORKER_MODE = os.environ.get("WORKER_MODE", "full")
    try:
        if WORKER_MODE == "build":
            main_build()
        elif WORKER_MODE == "score":
            main_score()
        else:
            main_full()
    except Exception as e:
        log(f"Fallo inesperado del worker (modo {WORKER_MODE}): {e}")
        if WORKER_MODE == "build":
            _write_build_status("BUILD_FAILED", f"Fallo inesperado preparando la entrega: {e}")
        else:
            emit_result("FAILED", None, {"error": f"Fallo inesperado calificando la entrega: {e}"})
