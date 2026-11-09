---
id: M08
title: Tablero — lecturas, listas, etiquetas y actividad
status: done
wave: 5
depends_on: [M02, M07]
branch: feature/m08-tablero
tags: [plan, milestone, trelloctl]
---

# M08 — Tablero: lecturas, listas, etiquetas y actividad

**Estado:** 🟢 Completado · **Ola:** 5 · **Depende de:** M02, M07 · **Rama:** `feature/m08-tablero`

## Objetivo

Implementar el grupo `board`: lecturas del tablero desde el caché, gestión de listas (incluido el movimiento por desplazamiento), etiquetas del tablero y actividad.

## Lectura obligatoria

PRD RF-8, RF-9, RF-10, RF-11, AC-13.4, AC-13.6, AC-14.2 a AC-14.5, AC-16.4, AC-22.7, AC-22.11, RNF-6, aclaraciones A-3, A-5 y A-22 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `board show|lists [--all]|labels|members|activity` y `--fresh` en las lecturas cacheadas.
- `board list create|rename|archive|unarchive|move|move-cards|archive-cards`.
- `board label create|update|remove --yes`.
- `--limit`, `--fields` y `meta.truncated` en `board activity`.
- Rutas del Trello falso: `lists`, `labels`, `boards/{id}/actions`, `lists/{id}/moveAllCards`, `lists/{id}/archiveAllCards`.

## Fuera de alcance

Comandos `card` (M09).

## Archivos

- **Crea:** `src/trelloctl/app/commands/board.py`, `lists.py`, `labels.py`, `activity.py`, `tests/fakes/trello/lists.py`, `labels.py`, `actions.py`, `tests/unit/app/commands/test_board*`, `test_lists*`, `test_labels*`, `test_activity*`, `tests/acceptance/test_m08_*`.
- **Modifica:** `registry.py` (bloque `board`).
- **No toca:** `infra/`, `domain/`, `app/resolver.py`, `card.py`, `search.py`, `attachments.py` y los módulos del Trello falso de M09 y M11 (ola 5 en paralelo). Si necesita un cambio en esos módulos, marca 🔴 y lo notifica al orquestador.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | 🟢 |

F5: solo local, PR y CI pendientes de confirmación del usuario.

## Casos de prueba automatizados

| TC         | AC                 | Tipo | Nivel | Escenario                                                                           | Resultado esperado                                                                             |
| ---------- | ------------------ | ---- | ----- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| TC-M08-001 | AC-8.1 | P | E | `board show` sobre el tablero por defecto | id, shortLink, nombre, url, `is_default = true`, recuentos de listas, tarjetas y etiquetas |
| TC-M08-002 | AC-8.1 | B | E | `board show` con `--board` distinto del por defecto | `is_default = false` |
| TC-M08-003 | AC-8.2 | P | E | `board lists` | Listas abiertas por posición con índice 0-based, id, nombre y número de tarjetas |
| TC-M08-004 | AC-8.2 | P | E | `board lists --all` | Incluye archivadas con `closed = true` |
| TC-M08-005 | AC-8.3 | P | E | `board labels` y `board members` | id, nombre y color (también etiquetas sin nombre); id, username y nombre completo |
| TC-M08-006 | AC-8.4 | P | E | Segunda lectura dentro del TTL | 0 peticiones; `meta.cache.hit = true` |
| TC-M08-007 | AC-8.4 | B | E | Metadatos frescos y snapshot de tarjetas vencido | `board lists` recarga solo las tarjetas para los recuentos |
| TC-M08-008 | AC-13.6 | P | E | `board show`, `lists`, `labels` y `members` con `--fresh` dentro del TTL (parametrizado) | Lee de Trello, actualiza el caché y renueva el TTL |
| TC-M08-009 | AC-2.1 | P | E | `config init`, credenciales y `board show` | HOME contiene `config.toml`, `logs/`, `cache.db` y `attachments/` |
| TC-M08-010 | AC-9.1 | P | E | `board list create --name X` y con `--pos top` y `--pos 1` | Al final por defecto; primera; segunda entre las abiertas |
| TC-M08-011 | AC-9.1, A-3 | N | E | `--pos 9` con 4 listas abiertas | `USAGE` sin escrituras |
| TC-M08-012 | AC-9.2 | P | E | `board list rename "To Do" --name Backlog` | `PUT /lists/{id}`; caché actualizado |
| TC-M08-013 | AC-9.3 | P | E | `board list archive` y `unarchive` | `PUT /lists/{id}/closed`; las tarjetas de la lista no se archivan |
| TC-M08-014 | RNF-6 | B | E | Archivar una lista ya archivada | `ok: true`, sin error |
| TC-M08-015 | AC-9.4, AC-22.11 | P | E | `board list move Doing --right 1` con metadatos frescos en caché | 1 lectura completa de metadatos y 1 escritura; nuevo índice correcto; TTL renovado |
| TC-M08-016 | AC-9.4 | P | E | `board list move Done --to-index 0` | Pasa a ser la primera |
| TC-M08-017 | AC-9.4, AC-14.3 | N | E | `board list move` fuera de rango sin y con `--clamp` | `MOVE_OUT_OF_RANGE` sin escritura; con `--clamp`, al extremo |
| TC-M08-018 | AC-14.4 | N | E | `--to-index` y `--left` a la vez | `USAGE` |
| TC-M08-019 | AC-14.5 | P | E | `board list move --right 1 --dry-run` | Origen, destino e índices; 0 escrituras |
| TC-M08-020 | AC-9.5, AC-22.7 | P | E | `board list move-cards A --to B` | Devuelve cuántas movió; snapshot de tarjetas rancio |
| TC-M08-021 | AC-9.5 | P | E | `board list archive-cards A` | Todas las abiertas de A archivadas en el Trello falso; snapshot rancio (la restauración con `card unarchive` la cubre TC-M09-049) |
| TC-M08-022 | AC-9.5 | N | E | `move-cards` hacia una lista inexistente | `LIST_NOT_FOUND` sin escritura |
| TC-M08-023 | AC-10.1 | P | E | `board label create` con `green`, `sky_light`, `black_dark` y `none` | Creadas con ese color |
| TC-M08-024 | AC-10.1 | N | E | `--color magenta` | `USAGE` sin llamar al API |
| TC-M08-025 | AC-10.2 | P | E | `board label update` solo con `--name`; luego solo con `--color` | El `PUT` lleva solo el campo indicado |
| TC-M08-026 | AC-10.3 | P | E | `board label remove bug --yes` con la etiqueta en 3 tarjetas | `DELETE /labels/{id}`; devuelve 3; el id desaparece de `cards.id_labels` |
| TC-M08-027 | AC-16.4 | N | E | `board label remove bug` sin `--yes` | `CONFIRMATION_REQUIRED`, salida 6, 0 escrituras |
| TC-M08-028 | AC-11.1, AC-13.4 | P | E | `board activity` con 80 acciones | 50, más recientes primero, `meta.truncated = true` |
| TC-M08-029 | AC-11.1, AC-13.4 | B | E | `board activity --limit 0` con 1500 acciones | Pagina con `before` hasta agotar; 1500 acciones; `meta.truncated = false` |
| TC-M08-030 | AC-11.1 | P | E | `--since` y `--before` | Se envían a Trello y filtran |
| TC-M08-031 | AC-11.1 | N | E | `--since ayer` | `USAGE` |
| TC-M08-032 | AC-11.2 | P | E | Acciones de tarjeta, de lista y de comentario | Normalizadas con id, tipo, fecha, miembro, tarjeta y lista si aplican, y resumen de `data` |
| TC-M08-033 | AC-11.3 | P | E | `--type createCard,commentCard` | Solo esos tipos |
| TC-M08-034 | AC-11.3 | N | E | `--type fooBar` | `USAGE` |
| TC-M08-035 | AC-11.4 | P | E | Dos `board activity` seguidos | Ambos consultan Trello; `cache.db` sin filas nuevas |
| TC-M08-036 | AC-13.4 | P | E | `board activity --fields id,type` | Cada elemento solo con esos campos |
| TC-M08-037 | AC-13.4, A-5 | N | E | `--fields id,nope` | `USAGE` |
| TC-M08-038 | AC-7.5 | N | E | `board list rename Inexistente --name X` | Recarga de metadatos y `LIST_NOT_FOUND` |
| TC-M08-039 | AC-9.3 | N | E | `board list delete Doing` | El comando no existe: `USAGE` |
| TC-M08-040 | AC-22.10 | N | E | `board lists` con el TTL vencido y el Trello falso caído | `ok: true` con los datos cacheados, `meta.cache.stale = true`, salida 9 |
| TC-M08-041 | AC-14.3, A-22 | B | E | `board list move` de la última lista con `--right 1 --clamp` | Sin escritura; origen igual a destino |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | Implementación: `app/commands/board.py` (`show`, `lists`, `labels`, `members` y los ayudantes de escritura al caché), `lists.py`, `labels.py`, `activity.py`, bloque `board` del registro y rutas del Trello falso `lists.py`, `labels.py`, `actions.py`. Las escrituras actualizan el caché con la respuesta de Trello sin renovar el TTL (`replace_metadata` con la marca de tiempo previa; `upsert_card` para quitar una etiqueta de las tarjetas cacheadas, conservando adjuntos y checklists). Campos de salida en snake_case (`short_link`, `is_default`, `full_name`). `--color none` se envía a Trello como `null`. |
| 2026-11-09 | F3: 41 de 41 TCs automatizados (`test_m08_reads.py`, `test_m08_lists.py`, `test_m08_labels.py`, `test_m08_activity.py`, con `m08_support.py`), cada uno verificado por separado con una sola iteración. Unitarias en `tests/unit/app/commands/test_board_unit.py`, `test_lists_unit.py`, `test_labels_unit.py`, `test_activity_unit.py`. |
| 2026-11-09 | Desviación: `tests/fakes/trello/core.py` (no declarado en Modifica). Las rutas nuevas `PUT /lists/{id}`, `PUT /labels/{id}`, `GET /lists/{id}` y `GET /labels/{id}` tapaban las rutas que `test_m07_resolution.py` registra en el Trello falso para simular referencias rancias (10 pruebas de M07 fallaban). Cambio mínimo: las rutas añadidas por una prueba después de cargar los módulos van primero (`Router.override`), de modo que una prueba puede sustituir una ruta de un módulo; el orden entre módulos no cambia. M09 y M11 pueden necesitar lo mismo. |
| 2026-11-09 | F4: `uv sync --all-extras` OK; `ruff check` OK; `ruff format --check` OK; `mypy` OK (28 archivos); `pytest` 515 pasadas, 1 omitida, cobertura 98,76 % (activity.py 100 %, board.py 100 %, labels.py 100 %, lists.py 99 %); `check_traceability.py` OK. |
| 2026-11-09 | F5: PR #11 fusionado en `develop` con merge commit, CI en verde en Windows, Linux y macOS con Python 3.12, 3.13 y 3.14. Estado: `done`. |
