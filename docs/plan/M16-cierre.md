---
id: M16
title: Cierre — requisitos no funcionales, completitud y release candidata
status: pending
wave: 8
depends_on: [M14, M15]
branch: feature/m16-cierre
tags: [plan, milestone, trelloctl]
---

# M16 — Cierre: requisitos no funcionales, completitud y release candidata

**Estado:** ⚪ Pendiente · **Ola:** 8 · **Depende de:** M14, M15 (y por transitividad todos) · **Rama:** `feature/m16-cierre`

## Objetivo

Verificar sobre el producto completo los requisitos no funcionales y las métricas del PRD, cerrar la trazabilidad en modo estricto, completar el README en inglés y dejar `develop` listo para una release.

## Lectura obligatoria

PRD §5, §8 (RNF‑1 a RNF‑10), RF‑27, §10.5 (lista de comandos), [[01-estrategia-de-pruebas]], [[tc-manuales-trello]].

## Alcance

- Pruebas de RNF transversales y de completitud de la CLI frente a §10.5.
- README en inglés: instalación (`pipx`, `uv tool install`), configuración mínima, credenciales, selección de tablero, ejemplos, MCP experimental, licencia.
- Revisión de licencias de las dependencias de desarrollo y del extra.
- Ejecución de los TCs manuales contra Trello por la persona responsable (fuera de CI) y registro del resultado en [[tc-manuales-trello]].

## Fuera de alcance

Publicación en PyPI y merge a `main` (decisión del responsable tras este milestone).

## Archivos

- **Crea:** `tests/acceptance/test_m16_*`.
- **Modifica:** `README.md`, `docs/tc-manuales-trello.md` (registro de ejecución).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación (README y ajustes de cierre)                     | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados y ejecución de los TCs manuales               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad estricta | ⚪ |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC      | Tipo | Nivel | Escenario                                                                                   | Resultado esperado                                                                                         |
| ---------- | ------- | ---- | ----- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| TC-M16-001 | AC-27.1 | P    | C     | Análisis estático de ramas por plataforma (`sys.platform`, `os.name`, `platform.system`)     | Solo en la resolución de HOME, la configuración de consola y los permisos de archivo                       |
| TC-M16-002 | AC-27.2 | P    | C     | Análisis estático final de `src/`                                                           | Ninguna invocación de shell ni de procesos externos                                                        |
| TC-M16-003 | RNF-1   | P    | S     | Construir el wheel e instalarlo sin extras en un entorno limpio                             | Metadatos sin `Requires-Dist` fuera del extra `mcp`; el entorno solo contiene `trelloctl`; `--version` funciona |
| TC-M16-004 | RNF-2   | P    | S     | `trelloctl --version` en frío, mediana de 5 ejecuciones menos el arranque del intérprete vacío | Menor de 300 ms                                                                                         |
| TC-M16-005 | RNF-4   | P    | E     | Todos los comandos JSON del registro (éxito y error)                                         | `meta.schema_version = "1"` en todos                                                                       |
| TC-M16-006 | RNF-5   | P    | E     | Un caso por código del catálogo provocado de extremo a extremo                               | Código de salida igual al de la tabla de §10.5                                                             |
| TC-M16-007 | RNF-6   | P    | E     | Repetir cada operación idempotente (añadir etiqueta o miembro presente, archivar archivada, marcar ítem marcado) | Segunda ejecución `ok: true` sin escritura                                    |
| TC-M16-008 | RNF-7   | P    | E     | Recorrer todas las fechas de las respuestas de la suite                                      | ISO‑8601 en UTC                                                                                            |
| TC-M16-009 | RNF-8   | P    | C     | Registro de conexiones de toda la suite y análisis de literales de URL en `src/`             | Solo `base_url` y la URL de la especificación OpenAPI                                                      |
| TC-M16-010 | RNF-9   | P    | C     | Licencia del proyecto y de las dependencias de desarrollo y del extra instaladas             | MIT en el proyecto; dependencias con licencias de la lista permitida (MIT, BSD, Apache‑2.0, PSF, ISC, MPL‑2.0) |
| TC-M16-011 | RNF-10  | P    | E     | Ayuda de todos los comandos, mensajes de todo el catálogo, plantilla de `config.toml`, texto de `doctor`, `SKILL.md`, README y logs | En inglés según la heurística común                                   |
| TC-M16-012 | RNF-3   | P    | E     | Recorrer las opciones de todos los comandos del registro                                     | Ninguna opción acepta credenciales                                                                         |
| TC-M16-013 | AC-3.1  | P    | E     | Completitud: comandos del registro frente a la lista de §10.5                                | Coinciden uno a uno (métrica de §5: 100 % de operaciones con un comando)                                   |
| TC-M16-014 | AC-26.4 | P    | C     | Cada invocación de ejemplo del `SKILL.md` y del README                                       | argparse la acepta con el CLI final                                                                        |
| TC-M16-015 | AC-1.2  | P    | E     | Parametrizado final de comandos que no exigen `config.toml` (AC‑1.2 y A‑4)                   | Ninguno devuelve `CONFIG_NOT_FOUND`; todos los demás comandos que llaman al API sí                         |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]], más: comprobador de trazabilidad en modo estricto en verde (todos los TCs de todos los milestones con prueba), TCs manuales ejecutados y registrados, README revisado.

## Bitácora

| Fecha | Evento |
| ----- | ------ |
