---
id: M05
title: Caché SQLite — almacén, TTL y modo sin caché
status: pending
wave: 3
depends_on: [M01, M02]
branch: feature/m05-cache
tags: [plan, milestone, trelloctl]
---

# M05 — Caché SQLite: almacén, TTL y modo sin caché

**Estado:** ⚪ Pendiente · **Ola:** 3 · **Depende de:** M01, M02 · **Rama:** `feature/m05-cache`

## Objetivo

Implementar el almacén `cache.db`: esquema de §10.6, WAL, frescura por TTL, bandera de rancio, reemplazo transaccional de snapshots, estadísticas, vaciado y un backend en memoria para `--no-cache`. La sincronización con Trello es de M07.

## Lectura obligatoria

PRD RF‑22 (AC‑22.1, 22.2, 22.3, 22.7, 22.8, 22.14, 22.15), §10.6 completo, aclaración A‑8 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `infra/cache.py`: creación y versión de esquema, una conexión por proceso con `busy_timeout` 5 s y transacciones cortas, API de lectura y escritura por tabla con `board_id` primero en la clave, marcas `*_fetched_at`, `cards_stale`, `deleted`, `archived`, reemplazo del snapshot de un tablero en una sola transacción, comprobación de integridad, estadísticas por tablero y vaciado.
- Reloj inyectable para las pruebas de TTL.
- Backend en memoria con la misma interfaz, que no crea ni toca `cache.db`.
- API completa para los milestones de la ola 5 en adelante, que no modifican este módulo.

## Fuera de alcance

Recargas desde Trello, resolución de nombres y comandos `cache *` (M07).

## Archivos

- **Crea:** `src/trelloctl/infra/cache.py`, `tests/unit/infra/test_cache*`, `tests/acceptance/test_m05_*`.
- **No toca:** `cli.py`, `registry.py` (M06, misma ola).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados                                               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | ⚪      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC       | Tipo | Nivel | Escenario                                                                                | Resultado esperado                                                                            |
| ---------- | -------- | ---- | ----- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| TC-M05-001 | AC-22.1  | P    | C     | Abrir el caché por primera vez                                                           | `cache.db` en HOME, `journal_mode = wal`, `busy_timeout = 5000`                               |
| TC-M05-002 | AC-22.1  | P    | C     | Introspección de claves primarias                                                        | Toda tabla por tablero tiene `board_id` como primera columna de su clave                      |
| TC-M05-003 | AC-22.2  | P    | C     | Introspección del esquema                                                                | Tablas y columnas de §10.6 exactamente; `schema` guarda la versión                            |
| TC-M05-004 | AC-22.2  | N    | C     | Buscar tablas para comentarios o actividad                                               | No existen                                                                                    |
| TC-M05-005 | AC-22.3  | P    | C     | Reloj avanzado más allá de cada TTL                                                      | Metadatos y tableros del usuario vencen con `metadata_ttl`; snapshots y tarjetas con `cards_ttl` |
| TC-M05-006 | AC-22.3  | B    | C     | Tarjeta refrescada sola con el snapshot vencido, y al revés                              | La frescura de la tarjeta usa su propio `fetched_at`                                          |
| TC-M05-007 | AC-22.3  | B    | C     | Edad exactamente igual al TTL (A‑8)                                                      | Vencido                                                                                       |
| TC-M05-008 | AC-22.7  | P    | C     | Marcar rancio el snapshot de tarjetas                                                    | `cards_stale = 1`, `cards_fetched_at` intacto                                                 |
| TC-M05-009 | AC-22.1  | P    | C     | Fallo simulado a mitad del reemplazo de un snapshot                                      | El snapshot anterior queda intacto                                                            |
| TC-M05-010 | AC-22.1  | B    | C     | Dos procesos escribiendo a la vez                                                        | Sin `database is locked` dentro de `busy_timeout`; ambas escrituras persisten                 |
| TC-M05-011 | AC-22.8  | P    | C     | Operar con el backend en memoria                                                         | `cache.db` no se crea; si existía, su contenido y fecha no cambian                            |
| TC-M05-012 | AC-22.14 | P    | C     | Guardar una tarjeta que llega con `closed = true`                                        | Queda con `archived = true`                                                                   |
| TC-M05-013 | AC-22.14 | P    | C     | Marcar rancias las tarjetas de una lista que ya no existe                                | Solo esas tarjetas quedan rancias                                                             |
| TC-M05-014 | AC-22.15 | P    | C     | Estadísticas con dos tableros y adjuntos descargados                                     | Antigüedad y tamaño de cada snapshot por tablero y bytes en `attachments/`                    |
| TC-M05-015 | AC-22.15 | P    | C     | Vaciar el caché sin y con adjuntos                                                       | Tablas vacías y adjuntos intactos; con adjuntos, archivos y directorios vacíos borrados       |
| TC-M05-016 | AC-5.2   | N    | C     | `cache.db` corrupto                                                                      | La comprobación de integridad informa del fallo sin excepción                                 |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
