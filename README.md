# codelab-grading

Workflow reutilizable de GitHub Actions para verificar entregas de retos de código.

## Uso

Desde el repositorio de tu entrega, agrega `.github/workflows/entregar.yml`:

```yaml
name: Calificar mi entrega

permissions:
  id-token: write
  contents: read

on:
  push:
    branches: [main]
  workflow_dispatch: {}

jobs:
  calificar:
    uses: devxcape/codelab-grading/.github/workflows/grade.yml@v1
    with:
      challenge_id: "<uuid del reto, te lo da el profesor>"
```

El bloque `permissions` de arriba es obligatorio tal cual está — sin él, el workflow falla al
arrancar. No necesitas entender qué hace, solo copiarlo.

El resultado queda registrado en la pestaña **Actions** de tu propio repositorio.

## Requisitos de tu repositorio

- Proyecto Maven (`pom.xml` en la raíz) basado en Spring Boot.
- Una clase anotada con `@SpringBootApplication`.
- La aplicación debe exponer los endpoints descritos en el enunciado de la actividad
  correspondiente.

No es necesario declarar ninguna prueba en tu repositorio — la verificación se ejecuta de
forma centralizada y no requiere ninguna configuración adicional de tu parte más allá del
workflow mostrado arriba.

## Diagnóstico de fallos

Si el workflow termina en rojo, abre la ejecución en la pestaña **Actions** y revisa el
**resumen** que aparece arriba de la página (sección "Resultado de la verificación") — ahí se
ve, prueba por prueba, cuál pasó y cuál no, con el detalle completo al hacer clic sobre cada
una que falló. No es necesario descargar nada.

Si el workflow falla en el paso "Pedir token OIDC" o "Buscar la prueba asignada", confirma que
ya entregaste tu repositorio desde la plataforma antes de hacer push — el workflow busca una
entrega en curso, no la crea.
