# codelab-grading

Workflow reutilizable de GitHub Actions para verificar entregas de codelabs.

## Uso

Desde el repositorio de tu entrega, agrega un workflow que invoque este workflow reutilizable:

```yaml
name: Verificar entrega

on:
  push:
    branches: [main]
  workflow_dispatch: {}

jobs:
  verificar:
    uses: devxcape/codelab-grading/.github/workflows/grade-spring-boot-05.yml@main
    with:
      challenge_id: spring-boot-05
```

El resultado queda registrado en la pestaña **Actions** de tu propio repositorio.

## Requisitos de tu repositorio

- Proyecto Maven (`pom.xml` en la raíz) basado en Spring Boot.
- Una clase anotada con `@SpringBootApplication`.
- La aplicación debe exponer los endpoints descritos en el enunciado de la actividad
  correspondiente.

No es necesario declarar ninguna prueba en tu repositorio — la verificación se ejecuta de
forma centralizada y no requiere ninguna configuración adicional de tu parte más allá del
workflow mostrado arriba.

## Retos adicionales por estudiante

Además de la verificación base, el workflow busca si tienes un reto adicional asignado,
identificándote por tu usuario de GitHub. Si existe, se ejecuta junto con la verificación
base y debe pasar igual que esta para que el workflow termine en verde. Si no tienes ninguno
asignado, este paso se omite automáticamente.

## Diagnóstico de fallos

Si el workflow termina en rojo, revisa el log del paso de ejecución de pruebas en la pestaña
**Actions** — el mensaje indica qué comportamiento esperado no se cumplió. El reporte completo
de pruebas también queda disponible como artifact descargable de esa misma ejecución.
