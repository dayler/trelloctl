---
id: M16
title: Cierre — requisitos no funcionales, completitud y release candidata
status: in_review
wave: 8
depends_on: [M14, M15]
branch: feature/m16-cierre
tags: [plan, milestone, trelloctl]
---

# M16 — Cierre: requisitos no funcionales, completitud y release candidata

**Estado:** 🔵 En revisión · **Ola:** 8 · **Depende de:** M14, M15 (y por transitividad todos) · **Rama:** `feature/m16-cierre`

## Objetivo

Verificar sobre el producto completo los requisitos no funcionales y las métricas del PRD, cerrar la trazabilidad, completar el README (en español, única excepción fuera de `docs/`) y dejar `develop` listo para una release.

## Lectura obligatoria

PRD §5, §8 (RNF-1 a RNF-10), RF-27, §10.5 (lista de comandos), [[01-estrategia-de-pruebas]], [[tc-manuales-trello]], aclaración A-4 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Pruebas de RNF transversales y de completitud de la CLI frente a §10.5.
- README en español (única excepción al idioma inglés fuera de `docs/`; el nombre de comandos, opciones y códigos de error se mantienen tal cual): instalación (`pipx`, `uv tool install`), configuración mínima, credenciales, selección de tablero, ejemplos, MCP experimental, licencia.
- Revisión de licencias de las dependencias de desarrollo y del extra.
- Los TCs manuales de [[tc-manuales-trello]] no son del subagente: los ejecuta el responsable después del merge de M16 y antes de fusionar `develop` en `main` (paso 8 del protocolo del orquestador).

## Fuera de alcance

Publicación en PyPI y merge a `main` (decisión del responsable tras este milestone).

## Archivos

- **Crea:** `tests/acceptance/test_m16_*`.
- **Modifica:** `README.md`, `src/trelloctl/skills/trelloctl/SKILL.md` (ejemplos que diverjan del CLI final).
- **No toca:** `docs/PRD.md`, `docs/plan/` salvo su propio archivo.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación (README y ajustes de cierre)                     | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad estricta | 🟢 |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪ (solo local: PR y CI pendientes de confirmación del usuario) |

## Casos de prueba automatizados

| TC         | AC      | Tipo | Nivel | Escenario                                                                                   | Resultado esperado                                                                                         |
| ---------- | ------- | ---- | ----- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| TC-M16-001 | AC-27.1 | P | C | Análisis estático de ramas por plataforma (`sys.platform`, `os.name`, `platform.system`) | Solo en la resolución de HOME, la configuración de consola y los permisos de archivo |
| TC-M16-002 | AC-27.2 | P | C | Análisis estático final de `src/` | Ninguna invocación de shell ni de procesos externos |
| TC-M16-003 | RNF-1 | P | S | Construir el wheel sin aislamiento de build y sin red, e instalarlo sin extras en un entorno limpio | Metadatos sin `Requires-Dist` fuera del extra `mcp`; el entorno solo contiene `trelloctl`; `--version` funciona |
| TC-M16-004 | RNF-4 | P | E | Todos los comandos JSON del registro (éxito y error) | `meta.schema_version = "1"` en todos |
| TC-M16-005 | RNF-5 | P | E | Un caso por código del catálogo provocado de extremo a extremo | Código de salida igual al de la tabla de §10.5 |
| TC-M16-006 | RNF-6 | P | E | Repetir cada operación idempotente (añadir etiqueta o miembro presente, archivar archivada, marcar ítem marcado) | Segunda ejecución `ok: true` sin escritura |
| TC-M16-007 | RNF-7 | P | E | Un comando representativo por grupo con fechas en la respuesta | ISO-8601 en UTC |
| TC-M16-008 | RNF-8 | P | C | Registro de conexiones de los TCs de M16 y análisis estático de literales de URL en `src/` | Solo `base_url` y la URL de la especificación OpenAPI |
| TC-M16-009 | RNF-9 | P | C | Licencia del proyecto y de las dependencias de desarrollo y del extra instaladas | MIT en el proyecto; dependencias con licencias de la lista permitida (MIT, BSD, Apache-2.0, PSF, ISC, MPL-2.0) |
| TC-M16-010 | RNF-10 | P | E | Ayuda de todos los comandos, mensajes de todo el catálogo, plantilla de `config.toml`, texto de `doctor`, `SKILL.md` y logs; el README queda excluido (en español por decisión del responsable) | En inglés según la heurística común |
| TC-M16-011 | RNF-3 | P | E | Recorrer las opciones de todos los comandos del registro | Ninguna opción acepta credenciales |
| TC-M16-012 | §5 | P | E | Completitud: comandos del registro frente a la lista de §10.5 | Coinciden uno a uno (métrica de §5: 100 % de operaciones con un comando) |
| TC-M16-013 | AC-26.4 | P | C | Cada invocación de ejemplo del `SKILL.md` y del README | Ejecutada contra el Trello falso con datos semilla equivalentes, termina con el mismo `ok` y el mismo código de error que muestra el ejemplo |
| TC-M16-014 | AC-1.2 | P | E | Parametrizado final de comandos que no exigen `config.toml` (AC-1.2 y A-4); `mcp serve` se rige por A-25 | Ninguno devuelve `CONFIG_NOT_FOUND`; todos los demás comandos que llaman al API sí |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]], más: comprobador de trazabilidad en verde con todos los milestones `done` (todos los TCs de todos los milestones con prueba), README revisado.

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1 hecha: README reescrito en español (instalación, configuración, credenciales, tablero, ejemplos, MCP experimental, licencia) y ejemplos del SKILL.md alineados con la salida real (`matched`/`comment_ids`, `card move` con `from`/`to`/`card`, envolturas `lists`/`labels`/`members`/`actions`, `card comment add --text`, `card get --full`) |
| 2026-11-09 | F2 y F3 hechas: 14 TCs automatizados (`test_m16_static.py`, `test_m16_wheel.py`, `test_m16_codes.py`, `test_m16_behaviour.py`, apoyo en `m16_support.py`, prueba unitaria del extractor de ejemplos); cada TC verificado por separado y en verde |
| 2026-11-09 | Desviación TC-M16-006: `card archive`, `card done` y `board list archive` repetidos devuelven `ok: true` y no cambian el estado, pero reenvían el mismo PUT (el código de M08/M09 no lo omite); solo etiqueta, miembro e ítem presentes se omiten sin escritura. La prueba exige exactamente eso; omitir también el PUT exigiría tocar `card.py` y `lists.py`, fuera del alcance de M16 |
| 2026-11-09 | Hallazgo (sin tocar `cli.py`): el mensaje `USAGE` de argparse repite los argumentos no reconocidos, por ejemplo el valor tras `--token`; y la ayuda general dice que MCP es experimental pero no nombra `trelloctl[mcp]` (sí lo hacen `mcp serve --help`, el SKILL y el README) |
| 2026-11-09 | F4: `uv sync --all-extras` OK; `ruff check .` OK; `ruff format --check .` OK (156 archivos); `mypy` OK (35 archivos); `pytest` con cobertura: 1120 passed, 1 skipped (permisos POSIX), cobertura 98,62 % (umbral 90 %); `tools/check_traceability.py`: OK |
| 2026-11-09 | Corrección posterior en rama `feature/fix-idempotency-redaction`: `card archive/unarchive/done/undone` y `board list archive/unarchive` ya no envían escritura si el estado coincide (RNF-6); TC-M16-006 exige cero escrituras en todas las operaciones repetidas. `cli.py` sustituye por `***` el valor de las opciones con aspecto de credencial en el mensaje `USAGE`; TC-M16-011 comprueba que no aparece en stdout, stderr ni log. Ningún TC de M08/M09 esperaba el PUT repetido |
