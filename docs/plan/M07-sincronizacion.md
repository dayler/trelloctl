---
id: M07
title: Sincronización, resolución de identificadores y comandos cache
status: in_review
wave: 4
depends_on: [M02, M05, M06]
branch: feature/m07-sincronizacion
tags: [plan, milestone, trelloctl]
---

# M07 — Sincronización, resolución de identificadores y comandos `cache`

**Estado:** 🔵 En revisión · **Ola:** 4 · **Depende de:** M02, M05, M06 · **Rama:** `feature/m07-sincronizacion`

## Objetivo

Conectar caché y Trello: recargas completas, incrementales y parciales, política de rancio, resolución de todas las entidades (nombre → id) con reintento único, referencias rancias, selección de tablero por nombre y comandos `cache reload|status|clear`. Deja listos los servicios de aplicación que consumen los milestones de la ola 5.

## Lectura obligatoria

PRD RF-7, RF-22 completo, AC-1.5, AC-1.8, AC-1.9, AC-1.10, §10.6, §10.7, aclaraciones A-4, A-8, A-10, A-14, A-15, A-17, A-22 y A-24 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Sincronización en `app/resolver.py`, que es quien habla con caché y Trello según §10.3 y §10.7: recarga completa de AC-22.6 con paginación de 1000 por `before`, `--list`, `--card`, `--archived`; recarga automática por TTL con `cards/all?modifiedSince`; renovación de TTL según AC-22.4; lectura fresca de metadatos para movimientos (AC-22.11); política de rancio de AC-22.10; eliminación del caché de las tarjetas de listas desaparecidas (AC-22.14, A-17).
- Resolución en el mismo módulo: tablero (id, shortLink, URL, nombre contra `member_boards`), lista, etiqueta (`nombre:color`), miembro, tarjeta (AC-7.3), checklist, ítem, comentario (solo id); `AMBIGUOUS_*` con candidatos; `*_NOT_FOUND` tras recarga y un reintento; referencias rancias de AC-22.13 con `cards.deleted`; traducción de 401/403 sobre tableros a `BOARD_NOT_FOUND`.
- Valores de 8 alfanuméricos según A-14. Tableros archivados en la resolución por nombre según A-24. Localización de tarjetas sin tablero seleccionado según A-15 (id o URL), que reutilizan todos los comandos `card`. Tratamiento genérico de referencias rancias (AC-22.13) para todas las entidades, que los milestones posteriores no necesitan ampliar.
- `meta.cache` en toda lectura; modo `--no-cache` con el backend en memoria.
- Comandos `cache reload [--list|--card|--archived]`, `cache status`, `cache clear [--attachments]`; `config board set` por shortLink, URL y nombre.
- Rutas del Trello falso: `members/me/boards`, `boards/{id}` con anidados, `boards/{id}/cards/open|closed|all`, `lists/{id}/cards`, `cards/{id}`.

## Fuera de alcance

Comandos `board`, `card`, `inbox` (olas 5 y 6).

## Archivos

- **Crea:** `src/trelloctl/app/resolver.py`, `src/trelloctl/app/commands/cache.py` (incluidos `cache clear --attachments` y el espacio de adjuntos de `cache status`), `tests/fakes/trello/cards_read.py`, `tests/unit/app/test_resolver*`, `tests/acceptance/test_m07_*`.
- **Modifica:** `registry.py` (bloque `cache`), `app/commands/config.py` (`config board set` por nombre), `tests/fakes/trello/boards.py` (rutas anidadas y de tarjetas).
- **No toca:** `skills/`, `app/commands/skills.py` (M13, misma ola).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

F5: solo local; PR y CI pendientes de confirmación del usuario.

## Casos de prueba automatizados

| TC         | AC                | Tipo | Nivel | Escenario                                                                                         | Resultado esperado                                                                                     |
| ---------- | ----------------- | ---- | ----- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| TC-M07-001 | AC-22.6 | P | E | `cache reload` | Exactamente 3 lecturas (tableros del usuario, metadatos, tarjetas abiertas); snapshot reemplazado; TTL de las tres colecciones renovado |
| TC-M07-002 | AC-22.6 | B | E | Tablero con 2500 tarjetas abiertas | 3 páginas de 1000 con `before`; 2500 tarjetas en caché |
| TC-M07-003 | AC-22.6 | B | E | Tablero con exactamente 1000 tarjetas | Segunda página vacía y fin de la paginación |
| TC-M07-004 | AC-22.6, AC-22.4 | P | E | `cache reload --list <nombre>` | Solo se recargan las tarjetas de esa lista; el TTL del snapshot no se renueva |
| TC-M07-005 | AC-22.6, AC-22.4 | P | E | `cache reload --card <id>` | Tarjeta con adjuntos y checklists; `fetched_at` propio; TTL del snapshot sin renovar |
| TC-M07-006 | AC-22.6 | P | E | `cache reload --archived` | Tarjetas cerradas con `archived = true`; `archived_fetched_at` fijado |
| TC-M07-007 | AC-22.6 | N | E | `cache reload --list X --card Y` | `USAGE` |
| TC-M07-008 | AC-22.5 | P | C | Lectura con el TTL de tarjetas vencido | `cards/all?modifiedSince=<última recarga>`; cambios abiertos y archivados aplicados |
| TC-M07-009 | AC-22.5 | B | C | Tarjeta borrada definitivamente en Trello | Sigue tras la recarga incremental; desaparece tras `cache reload` |
| TC-M07-010 | AC-22.4 | P | C | Recarga automática seguida de una actualización write-through de una fila | La recarga renueva el TTL; la escritura no |
| TC-M07-011 | AC-22.7 | P | C | Snapshot marcado rancio dentro de su TTL | La siguiente lectura recarga con `modifiedSince` igual a la fecha previa |
| TC-M07-012 | AC-22.10 | N | C | TTL vencido y recarga fallida por `NETWORK_ERROR`, `TIMEOUT`, `RATE_LIMITED` o 5xx (parametrizado) | `ok: true`, datos cacheados, `meta.cache.stale = true`, `meta.cache.error`, salida 9 |
| TC-M07-013 | AC-22.10 | N | C | TTL vencido y recarga con 401 `invalid token`; con 400 | `AUTH_INVALID` salida 4; `API_ERROR` salida 7; sin datos rancios |
| TC-M07-014 | AC-22.10, A-10 | B | C | Recarga fallida por red sin datos cacheados | Error original (`NETWORK_ERROR`), no `CACHE_STALE` |
| TC-M07-015 | AC-22.10 | N | C | Escritura con el TTL vencido y Trello caído | Falla con el error de la llamada; nunca usa datos rancios |
| TC-M07-016 | AC-22.8 | P | C | Lectura con caché fresco, vencido y con `--no-cache` | `meta.cache` con `hit`, `age_seconds`, `stale`; con `--no-cache`, `hit = false` y `cache.db` intacto |
| TC-M07-017 | AC-1.9, AC-7.1 | P | E | `--board` por id, shortLink, URL y nombre en mayúsculas distintas | Mismo tablero en los cuatro casos |
| TC-M07-018 | AC-1.9 | B | E | `--board` por id o URL | 0 llamadas a `members/me/boards` |
| TC-M07-019 | AC-1.9, AC-7.4 | N | E | Nombre que coincide con dos tableros | `AMBIGUOUS_BOARD` con `details.candidates` |
| TC-M07-020 | AC-7.5, AC-22.12 | N | E | Nombre de tablero inexistente | Una recarga de `member_boards` y `BOARD_NOT_FOUND` con el valor recibido |
| TC-M07-021 | AC-1.8 | P | E | `TRELLO_BOARD` sin `--board`; luego ambos | Se usa `TRELLO_BOARD`; después gana `--board` |
| TC-M07-022 | AC-1.10 | P | E | `config board set` con un shortLink, una URL y un nombre | Se resuelve y se guarda el id |
| TC-M07-023 | AC-1.2 | N | E | `config board set` con nombre, shortLink o URL sin credenciales | `AUTH_MISSING`, salida 4 |
| TC-M07-024 | AC-1.5 | N | E | 401 y 403 sobre el tablero con credenciales válidas | `BOARD_NOT_FOUND`, salida 5 |
| TC-M07-025 | AC-7.2 | P | C | Resolver lista por id y por nombre sin distinción de mayúsculas | Mismo id |
| TC-M07-026 | AC-7.2 | P | C | Resolver etiqueta (id, nombre, `nombre:color`), miembro (id, username), checklist e ítem por nombre dentro de su contenedor, comentario por id | Ids correctos |
| TC-M07-027 | AC-7.2 | N | C | Comentario dado por texto | `COMMENT_NOT_FOUND` |
| TC-M07-028 | AC-7.4 | N | C | Nombre ambiguo de lista, etiqueta, miembro, checklist, ítem y tarjeta (parametrizado) | `AMBIGUOUS_<ENTIDAD>` con id, nombre y contexto (lista en tarjetas, color en etiquetas); ninguna escritura |
| TC-M07-029 | AC-7.4 | B | C | Etiqueta `bug` en rojo y en azul; resolver `bug:red` | Resuelve la roja |
| TC-M07-030 | AC-7.5, AC-22.12 | N | C | Nombre inexistente de cada entidad | Recarga de la colección correspondiente, un reintento y `<ENTIDAD>_NOT_FOUND` con el valor |
| TC-M07-031 | AC-22.12 | P | C | Nombre ausente que aparece tras la recarga | Resuelve sin error |
| TC-M07-032 | AC-22.9 | P | C | `idShort` ausente que aparece tras la recarga incremental | Resuelve |
| TC-M07-033 | AC-22.9 | N | C | `idShort` ausente también tras la recarga | `CARD_NOT_FOUND` |
| TC-M07-034 | AC-22.13 | P | C | Id obtenido de un nombre que devuelve 404 o 400 de id inválido al escribir, para lista, etiqueta, checklist, ítem y tarjeta (parametrizado), con rutas de escritura y de confirmación registradas por la propia prueba en el Trello falso | Recarga, nueva resolución y un único reintento |
| TC-M07-035 | AC-22.13 | N | C | Id dado por el usuario que devuelve 404 o 400 de id inválido al escribir, para cada entidad (parametrizado), con rutas de escritura y de confirmación registradas por la propia prueba en el Trello falso | Lectura de confirmación, `cards.deleted` marcado si es una tarjeta, `<ENTIDAD>_NOT_FOUND` sin reintentar la escritura |
| TC-M07-036 | AC-22.13 | B | C | Id ya marcado `deleted` | `CARD_NOT_FOUND` con 0 peticiones |
| TC-M07-037 | AC-22.14, A-17 | P | C | Lista borrada en Trello y recarga de metadatos | Sus tarjetas desaparecen del caché; pedir una por id la trae con `GET /cards/{id}` y la guarda con su lista real |
| TC-M07-038 | AC-22.11 | P | C | Servicio de lectura fresca de metadatos para movimientos | Llamada completa de AC-22.6 aunque el TTL no haya vencido; renueva el TTL |
| TC-M07-039 | AC-22.15 | P | E | `cache status` con dos tableros cacheados | Antigüedad y tamaño de cada snapshot por tablero y espacio de adjuntos |
| TC-M07-040 | AC-22.15, AC-1.2, A-4 | P | E | `cache status` y `cache clear` sin `config.toml` | Funcionan; `cache clear` vacía `cache.db` y conserva adjuntos; `--attachments` los borra |
| TC-M07-041 | AC-22.8 | P | C | `--no-cache` con `--board` por nombre | Resolución en memoria; `cache.db` no se crea |
| TC-M07-042 | AC-22.5 | P | C | Metadatos y tableros del usuario con el TTL vencido | Se recargan con su llamada completa antes de resolver |
| TC-M07-043 | AC-1.9 | B | E | Dos `cache reload --list A` seguidos con `--board` por nombre dentro del TTL | El segundo hace 0 llamadas a `members/me/boards` |
| TC-M07-044 | AC-1.9, A-14 | B | E | `--board Personal` con un tablero llamado `Personal` y otro cuyo shortLink es `Personal`; luego solo existe el segundo | Gana el nombre; sin coincidencia de nombre, resuelve por shortLink; sin llamadas extra |
| TC-M07-045 | AC-1.9, A-24 | N | E | `--board Viejo` donde `Viejo` solo existe archivado, sin y con `--include-archived-boards` | Sin la opción, `BOARD_NOT_FOUND` tras recargar `member_boards`; con ella, resuelve el archivado |
| TC-M07-046 | AC-1.9, AC-7.4, A-24 | B | E | `Proyecto` activo y `Proyecto` archivado, sin y con `--include-archived-boards` | Sin la opción, resuelve el activo; con ella, `AMBIGUOUS_BOARD` con los dos candidatos |
| TC-M07-047 | AC-1.9, A-24 | P | E | Tablero archivado dado por id, shortLink y URL sin la opción | Se resuelve en los tres casos |
| TC-M07-048 | AC-1.10, A-24 | P | E | `--include-archived-boards config board set Viejo` con `Viejo` archivado | Guarda su id |
| TC-M07-049 | AC-1.9, A-14 | B | E | `--board <shortLink>` con `member_boards` vacío; repetir dentro del TTL | 1 lectura de `members/me/boards` (A-14 busca primero por nombre); la segunda invocación, 0 |
| TC-M07-050 | AC-22.13, A-22 | N | C | Id dado por el usuario con 400 al escribir y lectura de confirmación que encuentra la entidad | Error original de la escritura (`API_ERROR`) sin reintento |
| TC-M07-051 | AC-28.7, A-15 | P | C | Localizar sin tablero seleccionado una tarjeta por id, por URL, por shortLink suelto, por idShort y por nombre | id: `GET /cards/{id}` con el contexto de su tablero; URL: `GET /cards/{shortLink}`; los otros tres, `BOARD_NOT_CONFIGURED` |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | Implementación: `app/resolver.py` (sincronización completa, incremental y parcial; política de rancio; resolución de todas las entidades; referencias rancias con `guarded_write`; `locate_card`; `meta.cache`), `app/commands/cache.py` (`reload`, `status`, `clear`), bloque `cache` del registro, `config board set` por shortLink, URL y nombre, rutas del Trello falso (`members/me/boards`, `boards/{id}` anidado, `cards/open\|closed\|all`, `lists/{id}/cards`, `cards/{id}`). |
| 2026-11-09 | F3: 51 de 51 TCs automatizados (`test_m07_sync.py`, `test_m07_boards.py`, `test_m07_resolution.py`), cada uno verificado por separado con una sola iteración. Unitarias en `tests/unit/app/test_resolver_unit.py`. |
| 2026-11-09 | Desviaciones: (1) `tests/acceptance/test_m06_cli.py` TC-M06-022: `config board set not-an-id` pasa de `USAGE` a `BOARD_NOT_FOUND` porque todo valor que no es un id se resuelve como nombre (AC-1.10). (2) El renovado de `cards_fetched_at` tras una recarga incremental usa `Cache._tx()` desde el resolver, porque `infra/cache.py` no expone un método público y no está en la lista Modifica. (3) El texto de ayuda del parámetro `board` de `config board set` en el bloque `config` del registro sigue diciendo «board id»; está fuera del bloque `cache`. (4) `tests/acceptance/m07_support.py` (utilidades compartidas) no estaba en la lista Crea. |
| 2026-11-09 | F4: `uv sync --all-extras` OK; `ruff check` OK; `ruff format --check` OK; `mypy` OK (23 archivos); `pytest` 412 pasadas, 1 omitida, cobertura 98,63 % (resolver.py 98 %); `check_traceability.py` OK. |
