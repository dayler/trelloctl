---
id: M04
title: Cliente HTTP de Trello
status: in_review
wave: 2
depends_on: [M01]
branch: feature/m04-cliente-http
tags: [plan, milestone, trelloctl]
---

# M04 — Cliente HTTP de Trello

**Estado:** 🔵 En revisión · **Ola:** 2 · **Depende de:** M01 · **Rama:** `feature/m04-cliente-http`

## Objetivo

Implementar `trello_client` sobre `urllib` + `ssl`: autenticación, raíz configurable, tiempo máximo, una sola petición por llamada, traducción de errores (429, 4xx, 5xx, red, TLS, timeout), descarga con cabecera OAuth y subida multipart.

## Lectura obligatoria

PRD RF-23, AC-1.5, AC-21.2, AC-21.5, AC-24.3, RNF-3, RNF-8, §10.7, §10.8 (TLS), §11 (rate limits), aclaración A-22 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `infra/trello_client.py`: transporte inyectable (el real usa `urllib`; las UT usan el stub), parámetros desde la configuración (`base_url`, `timeout_seconds`), key y token en la query salvo en descargas, que van en la cabecera `Authorization: OAuth …`.
- Traducción: 429 → `RATE_LIMITED` con límite cruzado, `remaining`, `interval_ms`, `retry_in_seconds` desde las cabeceras `x-rate-limit-api-{token,key}-*`; 401 de credenciales → `AUTH_INVALID`; otros 4xx → `API_ERROR`; 5xx → `API_ERROR`; DNS, conexión o TLS → `NETWORK_ERROR`; tiempo agotado → `TIMEOUT`; cuerpo original en `details.upstream`.
- Log de depuración por petición con el token enmascarado.
- Reescritura de URLs absolutas de descarga de Trello sobre `base_url`.
- Stub de transporte para UT en `tests/fakes/transport.py`; rutas de `members/me` en el Trello falso.

## Fuera de alcance

Traducción de 404 a `<ENTIDAD>_NOT_FOUND` y de 401/403 sobre tableros a `BOARD_NOT_FOUND` (capa de aplicación, M07).

## Archivos

- **Crea:** `src/trelloctl/infra/trello_client.py`, `tests/fakes/transport.py`, `tests/fakes/trello/members.py`, `tests/fakes/trello/attachments.py` (solo descarga autenticada y subida multipart; M11 lo completa), `tests/unit/infra/test_trello_client*`, `tests/acceptance/test_m04_*`.
- **No toca:** `infra/settings.py` (M03), `domain/` (M02), en la misma ola. Recibe la configuración como valores, no importa `settings`.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias (con stub de transporte)                      | 🟢      |
| F3   | TCs automatizados (contra el Trello falso)                      | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC               | Tipo | Nivel | Escenario                                                                         | Resultado esperado                                                                                         |
| ---------- | ---------------- | ---- | ----- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| TC-M04-001 | AC-23.6 | P | C | `base_url` apuntando al Trello falso | Todas las peticiones llegan a esa raíz |
| TC-M04-002 | AC-23.1, AC-23.3 | N | C | El Trello falso responde 500 | `API_ERROR`; el registro muestra exactamente 1 petición |
| TC-M04-003 | AC-23.2 | N | C | 429 con cuerpo `API_TOKEN_LIMIT_EXCEEDED` y cabeceras de token | `RATE_LIMITED`, mensaje `Too many requests`, límite, `remaining`, `interval_ms`, `retry_in_seconds`; 1 petición |
| TC-M04-004 | AC-23.2 | N | C | 429 con `API_KEY_LIMIT_EXCEEDED` | Detalles calculados desde las cabeceras de key |
| TC-M04-005 | AC-23.2 | N | C | 429 con `API_TOKEN_DB_LIMIT_EXCEEDED` | `RATE_LIMITED` con ese límite |
| TC-M04-006 | AC-23.2, A-22 | B | C | 429 sin cabeceras `x-rate-limit-*` | `RATE_LIMITED` con `remaining`, `interval_ms` y `retry_in_seconds` nulos, sin excepción |
| TC-M04-007 | AC-23.3 | N | C | `base_url` a un puerto local cerrado | `NETWORK_ERROR` |
| TC-M04-008 | AC-23.3 | N | C | Fallo de DNS y error TLS simulados en el stub | `NETWORK_ERROR` en ambos |
| TC-M04-009 | AC-23.4 | N | C | Retardo del Trello falso mayor que `timeout_seconds = 1` | `TIMEOUT`; 1 petición |
| TC-M04-010 | AC-23.3 | N | C | 400, 404 y 422 genéricos | `API_ERROR` con el estado HTTP en `details` |
| TC-M04-011 | AC-1.5 | N | C | 401 `invalid key`; 401 `invalid token`; 401 en `GET /members/me` | `AUTH_INVALID` sin reintento |
| TC-M04-012 | AC-1.5 | N | C | 401 y 403 sobre un recurso con credenciales válidas | `API_ERROR` (la capa de aplicación decide `BOARD_NOT_FOUND`) |
| TC-M04-013 | AC-23.5 | P | C | Error con cuerpo JSON y con cuerpo de texto | `details.upstream` conserva el cuerpo original |
| TC-M04-014 | AC-23.5 | N | C | Errores 401, 429 y 500 con log en `debug` | Ni respuesta ni log contienen la key ni el token en claro |
| TC-M04-015 | AC-24.3 | P | C | Petición con nivel `debug` | Línea con método, ruta, estado y duración, token enmascarado |
| TC-M04-016 | AC-21.2 | P | C | Descarga de un adjunto | Cabecera `Authorization: OAuth oauth_consumer_key="…", oauth_token="…"`; ni key ni token en la query |
| TC-M04-017 | AC-21.2 | N | C | El Trello falso recibe una descarga con credenciales en la query | La rechaza (401), como Trello |
| TC-M04-018 | RNF-8 | P | C | URLs de descarga absolutas con host `api.trello.com` y `trello.com`, con `base_url` local | Se reescribe sobre `base_url`; ningún otro host contactado |
| TC-M04-019 | AC-21.5 | P | C | Subida multipart de un archivo con nombre | El Trello falso recibe `multipart/form-data` con el archivo y el nombre |
| TC-M04-020 | AC-23.3 | B | C | 200 con JSON inválido | `API_ERROR` con el cuerpo en `details.upstream` |
| TC-M04-021 | RNF-3 | P | C | Inspeccionar la construcción de peticiones | Las credenciales solo salen de la configuración, nunca de argumentos de la invocación |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1-F3: cliente HTTP, stub de transporte, rutas `members` y `attachments` del Trello falso; 21 TCs automatizados y verificados uno a uno |
| 2026-11-09 | Añadido `tests/acceptance/conftest.py` (recolección de basura silenciosa tras cada prueba): fugas preexistentes de `HTTPError` en pruebas de M00 provocaban un fallo intermitente de `test_rotation_failure_warns_and_writing_continues` (M01) al recoger la basura; fuera del alcance declarado, anotado como desviación |
| 2026-11-09 | F4: ruff check, ruff format --check, mypy, pytest (cobertura global ≥ 90 %) y trazabilidad en verde en local. F5 (PR y CI) queda ⚪: solo local, PR y CI pendientes de confirmación del usuario |
