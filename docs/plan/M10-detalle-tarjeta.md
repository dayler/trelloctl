---
id: M10
title: Detalle de tarjeta — comentarios, etiquetas, miembros y checklists
status: in_review
wave: 6
depends_on: [M09]
branch: feature/m10-detalle-tarjeta
tags: [plan, milestone, trelloctl]
---

# M10 — Detalle de tarjeta: comentarios, etiquetas, miembros y checklists

**Estado:** 🔵 En revisión · **Ola:** 6 · **Depende de:** M09 · **Rama:** `feature/m10-detalle-tarjeta`

## Objetivo

Implementar `card comments`, `card comment add|edit|remove`, `card label add|remove|set`, `card member add|remove|set` y `card checklist *` con ítems.

## Lectura obligatoria

PRD RF-17 a RF-20, AC-7.2, AC-7.4, AC-12.4, AC-13.4, AC-16.4, RNF-6, aclaraciones A-3, A-11, A-13, A-15 y A-22 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Comentarios con límite, paginación, menciones, edición y borrado propios, recuento `badges.comments` en caché.
- Etiquetas y miembros de tarjeta idempotentes, `set` equivalente a `card update --labels|--members`, conjunto final en la respuesta.
- Checklists e ítems: listar desde caché con progreso, crear con ítems iniciales, renombrar, borrar con `--yes`, marcar y desmarcar (`--all`), devolver el checklist completo o el id borrado, `badges.checkItems*` en caché.
- Rutas del Trello falso: escritura de comentarios (con autoría por token), `idLabels`, `idMembers`, `checklists`, `checkItems`.

## Fuera de alcance

Adjuntos (M11).

## Archivos

- **Crea:** `src/trelloctl/app/commands/checklists.py`, `tests/fakes/trello/comments_write.py`, `checklists.py`, `card_relations.py`, `tests/unit/app/commands/test_checklists*`, `tests/acceptance/test_m10_*`.
- **Modifica:** `src/trelloctl/app/commands/card.py` (subgrupos `comment`, `label`, `member`), `registry.py` (bloques `card comment`, `card label`, `card member` y `card checklist`).
- **No toca:** `inbox.py` (M12, misma ola), `infra/`, `domain/`, `app/resolver.py`.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC               | Tipo | Nivel | Escenario                                                                                   | Resultado esperado                                                                          |
| ---------- | ---------------- | ---- | ----- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| TC-M10-001 | AC-17.1, AC-13.4 | P | E | `card comments X` con 1200 comentarios; luego `--limit 0`; luego `--fields id,text` | 50 con id, autor, fecha y texto y `truncated = true`; luego los 1200 en dos páginas; con `--fields`, solo esos campos |
| TC-M10-002 | AC-17.1 | P | E | `card comments X` dos veces | Ambas consultan Trello (los comentarios no se cachean) |
| TC-M10-003 | AC-17.2 | P | E | `card comment add X --text "hola @ana"` | Devuelve el id; el texto llega literal con la mención |
| TC-M10-004 | AC-17.2, AC-12.4 | P | E | `--text-file` y `--text -` con stdin UTF-8 | Contenido exacto |
| TC-M10-005 | AC-12.4, A-11 | N | E | `--text` y `--text-file` a la vez; `--text-file` inexistente | `USAGE` en ambos |
| TC-M10-006 | AC-17.3 | P | E | `card comment edit X <id> --text nuevo` sobre un comentario propio | `PUT /cards/{id}/actions/{idAction}/comments` con el texto |
| TC-M10-007 | AC-17.3 | P | E | `card comment remove X <id> --yes` sobre uno propio | Borrado |
| TC-M10-008 | AC-17.3 | N | E | Editar o borrar un comentario de otro usuario | `API_ERROR` con el cuerpo de Trello en `details.upstream` |
| TC-M10-009 | AC-16.4 | N | E | `card comment remove X <id>` sin `--yes` | `CONFIRMATION_REQUIRED`, 0 escrituras |
| TC-M10-010 | AC-7.2 | N | E | `card comment edit X "texto del comentario"` | `COMMENT_NOT_FOUND` |
| TC-M10-011 | AC-17.4 | P | E | `add`, `remove` y `card get X` posterior | `badges.comments` actualizado en caché sin recargar |
| TC-M10-012 | AC-18.1, RNF-6 | P | E | `card label add X bug,urgent` con `bug` ya presente | Solo se añade `urgent`; `bug` no genera petición |
| TC-M10-013 | AC-18.1 | P | E | `card label remove X bug,ux` con `ux` ausente | Solo se quita `bug` |
| TC-M10-014 | AC-18.1 | P | E | `card label set X a,b` frente a `card update X --labels a,b` | Mismo efecto y mismo cuerpo de petición |
| TC-M10-015 | AC-18.2 | P | E | Cualquiera de las tres operaciones | La respuesta trae el conjunto final de etiquetas |
| TC-M10-016 | AC-18.1, AC-7.5, A-22 | N | E | `card label add X bug,nope` | `LABEL_NOT_FOUND`; no se añade ninguna |
| TC-M10-017 | AC-19.1 | P | E | `card member add X ana,luis` con `ana` asignada | Solo `POST /cards/{id}/idMembers` para `luis` |
| TC-M10-018 | AC-19.1 | P | E | `card member remove X ana,pepe` con `pepe` ausente; `card member set X luis` | Solo `DELETE` de `ana`; `set` equivale a `card update --members luis` |
| TC-M10-019 | AC-19.2 | P | E | Cualquiera de las tres operaciones | La respuesta trae el conjunto final de miembros |
| TC-M10-020 | AC-19.1, AC-7.5 | N | E | `card member add X desconocido` | `MEMBER_NOT_FOUND` |
| TC-M10-021 | AC-20.1 | P | E | `card checklist list X`, sin y con `--fresh` | Checklists con id, nombre, posición, progreso `checked/total` e ítems; 0 peticiones sin `--fresh`, 1 lectura con `--fresh` |
| TC-M10-022 | AC-20.2 | P | E | `card checklist add X --name QA --items a,b,c --pos top` | Checklist creado primero con 3 ítems |
| TC-M10-023 | AC-20.2 | P | E | `--items-file` con un ítem por línea | Un ítem por línea |
| TC-M10-024 | AC-20.2, A-22 | B | E | `--items-file` con líneas vacías y finales CRLF | Se ignoran las vacías; ningún ítem termina en `\r` |
| TC-M10-025 | AC-12.4 | N | E | `--items` y `--items-file` a la vez | `USAGE` |
| TC-M10-026 | AC-20.3 | P | E | `card checklist rename X QA --name Pruebas`; `card checklist remove X Pruebas --yes` | Renombrado; borrado con sus ítems y la respuesta trae el id borrado |
| TC-M10-027 | AC-16.4 | N | E | `card checklist remove X QA` sin `--yes` | `CONFIRMATION_REQUIRED` |
| TC-M10-028 | AC-20.4 | P | E | `card checklist item add X QA --name e2e --pos 0 --checked` | Ítem primero y marcado |
| TC-M10-029 | AC-20.4, A-3 | N | E | `--pos 9` en un checklist de 3 ítems | `USAGE` sin escrituras |
| TC-M10-030 | AC-20.5 | P | E | `item check X QA a,b` con `a` ya marcado | Solo `PUT /cards/{id}/checkItem/{id}` para `b` |
| TC-M10-031 | AC-20.5 | P | E | `item check X QA --all`; luego `item uncheck X QA --all` | Todos marcados; todos desmarcados; los que ya estaban no generan petición |
| TC-M10-032 | AC-20.5 | N | E | Ítems y `--all` a la vez | `USAGE` |
| TC-M10-033 | AC-20.6 | P | E | `item rename X QA a --name a2`; `item remove X QA a2,b --yes` | Renombrado; ambos borrados con sus ids en la respuesta |
| TC-M10-034 | AC-16.4 | N | E | `item remove X QA a` sin `--yes` | `CONFIRMATION_REQUIRED` |
| TC-M10-035 | AC-20.7 | P | E | Cada escritura de checklist o ítem seguida de `card get X` | Respuesta con el checklist completo; `badges.checkItems` y `checkItemsChecked` al día |
| TC-M10-036 | AC-7.4 | N | E | Dos checklists `QA` en la tarjeta; dos ítems `e2e` en un checklist | `AMBIGUOUS_CHECKLIST`; `AMBIGUOUS_CHECKITEM`, con candidatos |
| TC-M10-037 | AC-7.5 | N | E | Checklist inexistente; ítem inexistente | `CHECKLIST_NOT_FOUND`; `CHECKITEM_NOT_FOUND` |
| TC-M10-038 | AC-7.2 | B | E | Ítem `e2e` en los checklists QA y Release; `item check X QA e2e` | Se marca solo el de QA |
| TC-M10-039 | AC-17.2, AC-17.3, A-13 | P | E | Texto Markdown multilínea de fixture con negrita, lista, enlace y una mención `@ana` enviado con `card comment add --text-file` y después con `card comment edit --text -` (stdin) | El Trello falso recibe el texto byte a byte en cada escritura; `card comments` lo devuelve idéntico |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1 y F2: `app/commands/checklists.py`, subgrupos `comments`, `comment`, `label` y `member` en `app/commands/card.py`, bloques `card comment`, `card label`, `card member` y `card checklist` del registro y pruebas unitarias |
| 2026-11-09 | F3: 39 de 39 TCs automatizados (nivel E) con Trello falso ampliado: `comments_write.py`, `card_relations.py`, `checklists.py`; verificados uno a uno por su marcador |
| 2026-11-09 | Nota: `card checklist item check` y `uncheck` declara `items` como posicional opcional (`nargs="?"`) para admitir `--all`; las respuestas de checklist añaden `progress`, `checked` y `total` |
| 2026-11-09 | F4: ruff check, ruff format --check, mypy, pytest con cobertura y trazabilidad en verde (ver informe final) |
| 2026-11-09 | F5 pendiente: solo local, PR y CI a la espera de confirmación del usuario |
