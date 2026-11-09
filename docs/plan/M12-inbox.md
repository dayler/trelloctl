---
id: M12
title: Inbox personal
status: in_review
wave: 6
depends_on: [M09]
branch: feature/m12-inbox
tags: [plan, milestone, trelloctl]
---

# M12 — Inbox personal

**Estado:** 🔵 En revisión · **Ola:** 6 · **Depende de:** M09 · **Rama:** `feature/m12-inbox`

## Objetivo

Implementar `inbox list|add|move` y la aceptación de tarjetas del Inbox en los comandos `card`.

## Lectura obligatoria

PRD RF-28 completo, AC-7.3, AC-12.3, AC-12.4, AC-13.4, AC-22.9, §11 (riesgo del Inbox), aclaraciones A-3, A-11, A-13 y A-15 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Resolución del Inbox con `GET /members/me?fields=inbox`, caché con `boards.kind = "inbox"` y TTL de RF-22, `INBOX_UNAVAILABLE`.
- `inbox list` y `inbox add` sin tablero seleccionado; `inbox move` con tablero seleccionado y `--to` obligatorio; traslado de la fila de caché del Inbox al tablero destino.
- Verificación de que los comandos `card` aceptan tarjetas del Inbox (AC-28.7), ya implementado por la localización de tarjetas de M07 (A-15).
- Rutas del Trello falso: `members/me?fields=inbox`, tablero de tipo `inbox` con su lista, `PUT /cards/{id}` con `idBoard`.

## Fuera de alcance

Cualquier otra operación entre tableros (PRD §9).

## Archivos

- **Crea:** `src/trelloctl/app/commands/inbox.py`, `tests/fakes/trello/inbox.py`, `tests/unit/app/commands/test_inbox*`, `tests/acceptance/test_m12_*`.
- **Modifica:** `registry.py` (bloque `inbox`), `tests/fakes/trello/members.py` (campo `inbox`), `tests/fakes/trello/cards_write.py` (`PUT` con `idBoard`). No modifica `card.py` ni `app/resolver.py`: AC-28.7 lo cubre la localización de tarjetas de M07 y la resolución contra el Inbox usa el resolver genérico con el `board_id` del Inbox.
- **No toca:** `card.py`, `checklists.py` y los módulos del Trello falso de M10 (misma ola), `app/resolver.py`, `infra/`, `domain/`.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪ (solo local: PR y CI pendientes de confirmación del usuario) |

## Casos de prueba automatizados

| TC         | AC                | Tipo | Nivel | Escenario                                                                               | Resultado esperado                                                                                     |
| ---------- | ----------------- | ---- | ----- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| TC-M12-001 | AC-28.1 | P | E | Primer `inbox list` | `GET /members/me?fields=inbox`; fila en `boards` con `kind = "inbox"` |
| TC-M12-002 | AC-28.1, AC-22.15 | P | E | `cache status` tras `inbox list` | Incluye el Inbox |
| TC-M12-003 | AC-28.1 | N | E | Respuesta sin `inbox` en `list`, `add` y `move` | `INBOX_UNAVAILABLE`, salida 5 |
| TC-M12-004 | AC-28.1 | P | E | TTL del Inbox vencido; `inbox list --fresh` dentro del TTL | Recarga en ambos casos |
| TC-M12-005 | AC-28.2, AC-13.4 | P | E | `inbox list` con 60 tarjetas; luego `--limit 0`; luego `--fields id,name` | 50 con id, idShort, nombre, descripción, due y badges, `truncated = true`; luego 60; con `--fields`, solo esos campos |
| TC-M12-006 | AC-28.2 | P | E | Segundo `inbox list` dentro del TTL | 0 peticiones |
| TC-M12-007 | AC-28.2, AC-28.3 | P | E | `inbox list` e `inbox add` sin `--board`, sin `TRELLO_BOARD` y sin tablero por defecto | Funcionan |
| TC-M12-008 | AC-28.3 | P | E | `inbox add --name X --due 2026-11-15` con `--desc`, `--desc-file` y `--desc -` | `POST /cards` sobre `inbox.idList`; devuelve la tarjeta creada |
| TC-M12-009 | AC-28.3 | N | E | `inbox add --name X --list A`, `--labels bug`, `--members ana` | `USAGE` |
| TC-M12-010 | AC-28.3, AC-12.3 | B | E | `inbox add` con descripción de 16 385 caracteres | `USAGE` sin llamar al API |
| TC-M12-011 | AC-28.4 | P | E | `inbox move X --to "To Do"` | `PUT /cards/{id}` con `idBoard` e `idList`; la tarjeta sale del caché del Inbox y entra en el del tablero |
| TC-M12-012 | AC-28.4 | P | E | `inbox move X --to "To Do" --pos top` y `--pos 0` | Primera de la lista |
| TC-M12-013 | AC-28.4 | N | E | `inbox move X` sin `--to` | `USAGE` |
| TC-M12-014 | AC-28.4 | N | E | `inbox move X --to "To Do"` sin tablero seleccionado | `BOARD_NOT_CONFIGURED` |
| TC-M12-015 | AC-28.4, AC-7.3 | N | E | `inbox move` con el nombre de una tarjeta que no está en el Inbox | `CARD_NOT_FOUND` (se resuelve solo contra el Inbox) |
| TC-M12-016 | AC-28.4 | N | E | Lista destino inexistente | `LIST_NOT_FOUND`, 0 escrituras |
| TC-M12-017 | AC-28.4, A-3 | N | E | `--pos 9` en una lista de 3 tarjetas | `USAGE` sin escrituras |
| TC-M12-018 | AC-28.7 | P | E | `card get`, `card update --labels` y `card done` con el id de una tarjeta del Inbox, sin `--board` | Funcionan con el contexto del tablero Inbox |
| TC-M12-019 | AC-28.3, A-13 | P | E | Texto Markdown multilínea de fixture con negrita, lista, enlace y una mención `@ana` enviado con `inbox add --desc-file` | El Trello falso recibe la descripción byte a byte; `card get` de la tarjeta del Inbox la devuelve idéntica |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | Implementación de `inbox list|add|move`, bloque `inbox` del registro, Trello falso (`inbox.py`, `members.py`, `PUT` con `idBoard`) y 19 TCs automatizados (+ pruebas unitarias) |
| 2026-11-09 | F4 local: ruff check, ruff format --check, mypy y check_traceability OK; suite completa 806 passed + 1 skipped (ejecutada en 8 procesos con cobertura combinada: 99 %, sin fallo del umbral de 90 %) |
| 2026-11-09 | Nota: la fila `boards.kind = "inbox"` la pisa con `"board"` un `sync_metadata` genérico (`app/resolver.py`, fuera de alcance); `inbox` la vuelve a resolver con una llamada a `members/me` |
