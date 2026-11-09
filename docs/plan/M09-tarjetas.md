---
id: M09
title: Tarjetas — crear, consultar, buscar, mover, copiar y actualizar
status: done
wave: 5
depends_on: [M02, M07]
branch: feature/m09-tarjetas
tags: [plan, milestone, trelloctl]
---

# M09 — Tarjetas: crear, consultar, buscar, mover, copiar y actualizar

**Estado:** 🟢 Completado · **Ola:** 5 · **Depende de:** M02, M07 · **Rama:** `feature/m09-tarjetas`

## Objetivo

Implementar el núcleo del grupo `card` y la búsqueda: `create`, `get`, `list`, `search`, `move`, `copy`, `update`, `done`, `undone`, `archive`, `unarchive`.

## Lectura obligatoria

PRD RF-12 a RF-16, AC-7.3, AC-7.4, AC-22.9, AC-22.11, AC-22.13, RNF-6, §10.7, aclaraciones A-3, A-5, A-11, A-13, A-14, A-15, A-20 y A-21 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Comandos anteriores con todas sus opciones, `--fresh`, `--limit`, `--fields`, `--include-archived`.
- `app/search.py`: comodines `*` y `?` con comparación sin distinción de mayúsculas también para caracteres no ASCII, campos `name`, `desc`, `checklists` en caché y `comments` vía `GET /search` con `partial=true`, eliminación de comodines y `meta.warnings`.
- Escritura con write-through en caché.
- Uso de la localización de tarjetas sin tablero seleccionado de M07 (A-15), incluidas las del Inbox (AC-28.7), en todos los comandos `card`.
- Búsqueda local con GLOB, incluidos los corchetes, y eliminación de `*`, `?`, `[` y `]` en la búsqueda de comentarios (A-20).
- Rutas del Trello falso: `POST /cards` (incluido `idCardSource` y `keepFromSource`), `PUT /cards/{id}`, `GET /cards/{id}/actions` (comentarios, solo lectura), `GET /search`.

## Fuera de alcance

Subgrupos `card comment|label|member|checklist` (M10) y `card attachments` (M11).

## Archivos

- **Crea:** `src/trelloctl/app/commands/card.py`, `src/trelloctl/app/search.py`, `tests/fakes/trello/cards_write.py`, `tests/fakes/trello/search.py`, `tests/fakes/trello/comments.py` (lectura, con paginación por `before`), `tests/unit/app/test_search*`, `tests/unit/app/commands/test_card*`, `tests/acceptance/test_m09_*`.
- **Modifica:** `registry.py` (bloque `card`).
- **No toca:** `infra/`, `domain/`, `app/resolver.py`, `board.py`, `lists.py`, `labels.py`, `activity.py`, `attachments.py` y los módulos del Trello falso de M08 y M11 (ola 5 en paralelo).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | 🟢 |

## Casos de prueba automatizados

| TC         | AC                | Tipo | Nivel | Escenario                                                                                    | Resultado esperado                                                                                  |
| ---------- | ----------------- | ---- | ----- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| TC-M09-001 | AC-12.1 | P | E | `card create --list "To Do" --name X` | Al final de la lista; tarjeta completa en `data`; insertada en caché |
| TC-M09-002 | AC-12.2 | P | E | Con `--desc`, `--due`, `--labels bug,urgent`, `--members ana`, `--pos top`, `--pos 2` | El `POST` lleva todos los campos e ids resueltos; posición correcta |
| TC-M09-003 | AC-12.2, A-3 | N | E | `--pos 7` en una lista con 3 tarjetas | `USAGE` sin escrituras |
| TC-M09-004 | AC-12.3 | B | E | Descripción de 16 384 caracteres; luego de 16 385 en `create` y en `update` | La primera se crea; las otras dos `USAGE` sin llamar al API |
| TC-M09-005 | AC-12.3 | P | E | Descripción con Markdown y HTML | Se envía literal, sin transformar |
| TC-M09-006 | AC-12.4 | P | E | `--desc-file` y `--desc -` con stdin UTF-8 no ASCII | Contenido exacto en el `POST` |
| TC-M09-007 | AC-12.4 | N | E | `--desc` y `--desc-file` a la vez | `USAGE` |
| TC-M09-008 | AC-12.4, A-11 | N | E | `--desc-file` inexistente | `USAGE` |
| TC-M09-009 | AC-12.5 | N | E | Una etiqueta inexistente; luego un miembro inexistente | `LABEL_NOT_FOUND`; `MEMBER_NOT_FOUND`; 0 `POST /cards` |
| TC-M09-010 | AC-12.1 | N | E | Lista inexistente | `LIST_NOT_FOUND` |
| TC-M09-011 | AC-12.2 | N | E | `--due mañana` | `USAGE` |
| TC-M09-012 | AC-13.1 | P | E | `card get <id>` | Lista, etiquetas, miembros, due, `dueComplete`, url, última actividad y `badges`; 0 peticiones con caché fresco |
| TC-M09-013 | AC-13.1 | P | E | `card get --full` | Añade checklists y adjuntos del caché y comentarios con 1 petición |
| TC-M09-014 | AC-13.1 | P | E | `--with checklists`; luego `--with comments` | 0 peticiones; 1 petición |
| TC-M09-015 | AC-13.1 | N | E | `--full` y `--with` a la vez; `--with foo` | `USAGE` en ambos |
| TC-M09-016 | AC-7.3 | P | E | `card get` por id, shortLink, URL, `idShort` y nombre | Misma tarjeta |
| TC-M09-017 | AC-7.3 | B | E | Tarjeta con nombre `123` y otra con `idShort` 123 | `card get 123` devuelve la del `idShort`; `card search 123` encuentra la del nombre |
| TC-M09-018 | AC-22.9 | P | E | `card get <id>` ausente del caché | 1 lectura; tarjeta insertada |
| TC-M09-019 | AC-22.9 | N | E | `card get 999` inexistente | Recarga incremental y `CARD_NOT_FOUND` |
| TC-M09-020 | AC-7.4 | N | E | `card move Fix --right 1` con dos tarjetas `Fix` | `AMBIGUOUS_CARD` con la lista de cada candidata; 0 escrituras |
| TC-M09-021 | AC-13.2 | P | E | `card list` con `--list`, `--label`, `--member`, `--due-before`, `--due-after`, `--done`; repetir con `--not-done` | Solo las abiertas que cumplen todos los filtros; 0 peticiones |
| TC-M09-022 | AC-13.2 | P | E | `card list` sin filtros | Todas las abiertas del tablero |
| TC-M09-023 | AC-13.2 | N | E | `--done` y `--not-done` a la vez | `USAGE` |
| TC-M09-024 | AC-13.3 | P | E | `card search deploy` | Coincidencias en `name` y `desc` equivalentes a `*deploy*`, `matched` por resultado, 0 peticiones |
| TC-M09-025 | AC-13.3 | P | E | Patrones `core-7?` y `core-78*` | `?` es un carácter; `*` cualquier secuencia |
| TC-M09-026 | AC-13.3 | B | E | `card search ÁRBOL` con una tarjeta `árbol` y `card search Deploy` con `deploy` | Coinciden sin distinción de mayúsculas, también en no ASCII |
| TC-M09-027 | AC-13.3 | P | E | `--in checklists` | Coincide por nombre de checklist o de ítem; `matched = ["checklists"]` |
| TC-M09-028 | AC-13.3 | P | E | `--in comments` | 1 `GET /search` con `comment:`, restringido al tablero y `partial=true`; id del comentario en el resultado |
| TC-M09-029 | AC-13.3 | B | E | `card search "core-78*" --in name,comments` | La consulta a Trello va sin comodines; `meta.warnings` lo indica; la búsqueda local usa el patrón completo |
| TC-M09-030 | AC-13.3, A-5 | N | E | `card search "*" --in comments` | `USAGE` sin llamar al API |
| TC-M09-031 | AC-13.3, A-5 | N | E | `--in labels` | `USAGE` |
| TC-M09-032 | AC-13.4 | P | E | 120 tarjetas: `card list` y `card search` sin límite, con `--limit 10`, `--limit 0` y `--fields id,name` | 50 con `truncated = true`; 10; 120 con `truncated = false`; solo esos campos |
| TC-M09-033 | AC-13.4 | B | E | Exactamente 50 resultados; luego 51 | `truncated = false`; `truncated = true` |
| TC-M09-034 | AC-13.5 | P | E | `card list --include-archived` sin snapshot de archivadas | Recarga de cerradas; incluye `archived = true` |
| TC-M09-035 | AC-13.5 | B | E | El mismo comando con el snapshot de archivadas fresco; `card search` sin la opción | 0 peticiones; las archivadas no aparecen |
| TC-M09-036 | AC-13.6 | P | E | `card get`, `card list` y `card search` con `--fresh` dentro del TTL | Leen de Trello y actualizan el caché |
| TC-M09-037 | AC-14.1 | P | E | `card move X --to Done`; luego con `--pos top` | Al final de Done; primera |
| TC-M09-038 | AC-14.2, AC-22.11 | P | E | `card move X --right 2` con metadatos frescos en caché | 1 lectura completa de metadatos, 1 `PUT`; respuesta con `from`, `to` y tarjeta |
| TC-M09-039 | AC-14.2 | P | E | `card move X --left` | Una lista a la izquierda |
| TC-M09-040 | AC-14.3 | N | E | `--right 5` fuera de rango; luego con `--clamp` | `MOVE_OUT_OF_RANGE` con detalles y 0 `PUT`; con `--clamp`, última lista |
| TC-M09-041 | AC-14.4 | N | E | `--to Done --right 1` | `USAGE` |
| TC-M09-042 | AC-14.5 | P | E | `card move X --right 1 --dry-run` | Origen, destino e índices; 0 escrituras |
| TC-M09-043 | AC-15.1 | P | E | `card copy X --to Done`; luego con `--name Y` y `--pos top` | `POST /cards` con `idCardSource`; conserva el nombre; luego con Y en primera posición |
| TC-M09-044 | AC-15.2 | P | E | `--keep none` y `--keep checklists,labels`; sin `--keep` | `keepFromSource` correspondiente; por defecto `all` |
| TC-M09-045 | AC-15.2 | N | E | `--keep votes` | `USAGE` |
| TC-M09-046 | AC-15.3 | P | E | `card copy` seguido de `card get` de la copia | Tarjeta nueva completa en la respuesta y en caché (0 peticiones en el `get`) |
| TC-M09-047 | AC-16.1 | P | E | `card update X` con `--name`, `--labels`, `--members`, `--due`, `--done` y `--pos top`, de uno en uno | El `PUT` lleva solo los campos indicados; conjuntos reemplazados |
| TC-M09-048 | AC-16.1 | N | E | `--due` y `--no-due` a la vez; `--done` y `--not-done` a la vez | `USAGE` |
| TC-M09-049 | AC-16.3, AC-9.5 | P | E | `card archive X` y `card unarchive X`; `card unarchive` de una tarjeta sembrada como archivada en el Trello falso | Archivada y restaurada en su lista |
| TC-M09-050 | AC-16.1 | P | E | `card update X --no-due` | `due` vacío en Trello y en caché |
| TC-M09-051 | AC-16.2 | P | E | `card done X` y `card undone X` sobre una tarjeta sin due | `dueComplete` true y luego false |
| TC-M09-052 | RNF-6 | B | E | Archivar una tarjeta archivada; `card done` sobre una completada | `ok: true`, sin error |
| TC-M09-053 | AC-16.4 | N | E | `card delete X` | El comando no existe: `USAGE` |
| TC-M09-054 | AC-22.13 | N | E | `card update <id> --name Y` sobre una tarjeta borrada en Trello | `CARD_NOT_FOUND`; `cards.deleted` marcado |
| TC-M09-055 | AC-22.11 | P | E | Escritura con el TTL de tarjetas vencido | No recarga el snapshot; actualiza la fila con `fetched_at` de la respuesta |
| TC-M09-056 | AC-12.3, AC-12.4, AC-16.1, A-13 | P | E | Texto Markdown multilínea de fixture con negrita, lista, enlace y una mención `@ana` enviado con `card create --desc-file` y después con `card update --desc -` (stdin) | El Trello falso recibe la descripción byte a byte en cada escritura; `card get` y `card get --fresh` la devuelven idéntica |
| TC-M09-057 | AC-13.3, A-20 | B | E | `card search "fix[12]"` y `card search "fix[12]" --in comments` | Local: coincide con `fix1` y `fix2`; en comentarios la consulta va sin corchetes y `meta.warnings` lo indica |
| TC-M09-058 | AC-13.5 | P | E | `card search deploy --include-archived` sin snapshot de archivadas; repetir con ese snapshot vencido | Recarga las cerradas en ambos casos e incluye las archivadas con `archived = true` |
| TC-M09-059 | AC-28.7, A-15 | P | E | `card get <id>` y `card get <url>` de una tarjeta de otro tablero sin tablero seleccionado; `card get 12` y `card get Refactor` sin tablero seleccionado | Las dos primeras funcionan con el contexto del tablero de la tarjeta; las otras dos, `BOARD_NOT_CONFIGURED` |
| TC-M09-060 | AC-14.1, AC-14.2, A-21 | B | E | `card move X --to <su lista> --pos 0`; `card move` de una tarjeta en lista archivada con `--right 1` | X queda la primera; `MOVE_OUT_OF_RANGE` con `from_index` nulo y sin escrituras |
| TC-M09-061 | AC-7.3, A-14 | B | E | Una tarjeta llamada `Refactor` y otra cuyo shortLink es `Refactor`; `card get Refactor` | Devuelve la del nombre |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1 y F2: `app/search.py`, `app/commands/card.py`, bloque `card` del registro y pruebas unitarias (búsqueda, argumentos, vistas y flujos de error) |
| 2026-11-09 | F3: 61 de 61 TCs automatizados (nivel E) con Trello falso ampliado: `cards_write.py`, `comments.py`, `search.py`; verificados uno a uno por su marcador |
| 2026-11-09 | Desviación: `--left` y `--right` exigen el valor N en la CLI (el registro no admite opciones con valor opcional); TC-M09-039 usa `--left 1` |
| 2026-11-09 | Desviación: `tests/fakes/trello/core.py` (M00) ahora sustituye una ruta registrada con el mismo método y forma de ruta; sin ello las pruebas de M07 que registran `PUT /cards/{id}` quedaban tapadas por la ruta de `cards_write.py` |
| 2026-11-09 | F4: ruff check, ruff format --check, mypy, pytest con cobertura y trazabilidad en verde (ver informe final) |
| 2026-11-09 | F5: PR #13 fusionado en `develop` con merge commit, CI en verde en Windows, Linux y macOS con Python 3.12, 3.13 y 3.14. Estado: `done`. |
