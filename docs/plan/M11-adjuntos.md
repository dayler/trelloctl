---
id: M11
title: Adjuntos — listar, descargar, subir y limpiar
status: done
wave: 5
depends_on: [M07]
branch: feature/m11-adjuntos
tags: [plan, milestone, trelloctl]
---

# M11 — Adjuntos: listar, descargar, subir y limpiar

**Estado:** 🟢 Completado · **Ola:** 5 · **Depende de:** M07 · **Rama:** `feature/m11-adjuntos`

## Objetivo

Implementar `card attachments list|download|add|clear`. `cache clear --attachments` y el espacio de adjuntos de `cache status` los implementan M05 y M07; aquí solo se prueba su integración con las descargas.

## Lectura obligatoria

PRD RF-21 completo, AC-2.2, AC-22.15, §10.6 (directorio `attachments/`), §11 (riesgo de descargas), aclaraciones A-4, A-9, A-11 y A-15 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Listado desde caché con ruta local.
- Descarga por `--id` o `--all` en serie con cabecera OAuth, sincronización (nuevos, cambiados, iguales, huérfanos), `skipped` para enlaces, `RATE_LIMITED` con `error.details.partial`, `ATTACHMENT_GONE`, `ATTACHMENT_NOT_FOUND`, nombres de archivo saneados (A-9).
- Subida multipart o enlace, `ATTACHMENT_TOO_LARGE` antes de llamar al API.
- Limpieza local sin llamadas al API y sin exigir `config.toml` (A-4).
- Rutas del Trello falso: `cards/{id}/attachments` (lista, alta) y descarga autenticada por cabecera.

## Fuera de alcance

Nada más del grupo `card`.

## Archivos

- **Crea:** `src/trelloctl/app/commands/attachments.py`, `tests/unit/app/commands/test_attachments*`, `tests/acceptance/test_m11_*`.
- **Modifica:** `registry.py` (bloque `card attachments`), `tests/fakes/trello/attachments.py` (lo crea M04; aquí se completan listado y alta).
- **No toca:** `card.py`, `board.py`, `lists.py`, `labels.py`, `activity.py`, `search.py`, `app/commands/cache.py`, `infra/`, `domain/`, `app/resolver.py` y los módulos del Trello falso de M08 y M09 (ola 5 en paralelo).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | 🟢 |

## Casos de prueba automatizados

| TC         | AC                | Tipo | Nivel | Escenario                                                                                       | Resultado esperado                                                                                      |
| ---------- | ----------------- | ---- | ----- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| TC-M11-001 | AC-21.1 | P | E | `card attachments list X` con un archivo y un enlace, sin y con `--fresh` | id, nombre, bytes, mimeType, fecha, url, `isUpload`, ruta local nula; 0 peticiones sin `--fresh` |
| TC-M11-002 | AC-21.2 | P | E | `download --id <id>` | Archivo en `<HOME>/attachments/<board>/<card>/<id>_<nombre>`; cabecera OAuth; ruta local en el listado |
| TC-M11-003 | AC-21.2 | P | E | `download --id` de un enlace externo | `skipped` con su url; 0 peticiones de descarga |
| TC-M11-004 | AC-21.3 | P | E | `download --all` con un adjunto nuevo, uno igual, uno cambiado y un local huérfano | 1 lectura de la lista; descarga nuevo y cambiado; conserva el igual; borra el huérfano; respuesta con las 4 categorías |
| TC-M11-005 | AC-21.3 | B | E | Adjunto con los mismos bytes y fecha distinta | Se descarga de nuevo |
| TC-M11-006 | AC-21.3 | N | E | 429 en la segunda de tres descargas | `RATE_LIMITED`, salida 7; `error.details.partial` con lo hecho; la tercera no se intenta |
| TC-M11-007 | AC-21.4 | N | E | `download --id` de un adjunto borrado en Trello con copia local | Copia local borrada; `ATTACHMENT_GONE` |
| TC-M11-008 | AC-21.4 | N | E | `download --id` desconocido | `ATTACHMENT_NOT_FOUND` |
| TC-M11-009 | AC-21.2 | N | E | `download` sin `--all` ni `--id`; con ambos | `USAGE` |
| TC-M11-010 | AC-21.2, A-9 | B | E | Adjunto llamado `../../x` y otro `a:b*?.txt` | Archivos saneados dentro del directorio de la tarjeta; nada fuera de `attachments/` |
| TC-M11-011 | AC-21.5 | P | E | `add X --file informe.pdf --name Informe` | `POST` multipart; adjunto en caché |
| TC-M11-012 | AC-21.5 | P | E | `add X --url https://example.com --name Enlace` | Enlace adjunto; en caché |
| TC-M11-013 | AC-21.5 | N | E | Archivo de `max_upload_bytes + 1` | `ATTACHMENT_TOO_LARGE`, salida 6, 0 peticiones |
| TC-M11-014 | AC-21.5 | B | E | Archivo de exactamente `max_upload_bytes` | Se sube |
| TC-M11-015 | AC-21.5, A-11 | N | E | `--file` y `--url` a la vez; `--file` inexistente | `USAGE` |
| TC-M11-016 | AC-21.6 | P | E | `card attachments clear X`; luego `clear Y --id <id>` | Archivos borrados, ruta local nula, directorios vacíos eliminados, 0 peticiones |
| TC-M11-017 | AC-21.6, AC-22.15 | P | E | `cache clear --attachments` con adjuntos de dos tableros | Todos borrados y `attachments/` sin subdirectorios vacíos |
| TC-M11-018 | AC-21.6, AC-1.2, A-4 | P | E | `--board <id> card attachments clear <id>` sin `config.toml` | Funciona |
| TC-M11-019 | AC-22.15 | P | E | `cache status` tras descargar 3 archivos | Espacio de adjuntos igual a la suma de sus bytes |
| TC-M11-020 | AC-2.2 | N | E | Un archivo ajeno dentro de `attachments/<board>/<card>/` | `clear` no lo borra |
| TC-M11-021 | AC-21.2 | N | E | Descarga con un retardo mayor que `timeout_seconds` y con `--log-level debug` | `TIMEOUT` sin reintento; en debug, una línea de log por descarga con el token enmascarado |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1-F3: `app/commands/attachments.py`, bloque `card attachments` de `registry.py`, rutas `GET /cards/{id}/attachments` y alta con `date`/`mimeType`/`url` en el Trello falso; 21 TCs automatizados (nivel E) y verificados uno a uno; pruebas unitarias del módulo. |
| 2026-11-09 | Decisiones: `download --all` y `--id` hacen una lectura `GET /cards/{id}` antes de descargar; "cambiado" se decide comparando bytes y fecha de la fila cacheada previa con la recién leída, más tamaño del archivo local; `add` refresca la tarjeta (una lectura extra) para meter el adjunto en caché porque `infra/cache.py` no ofrece alta individual; `clear` localiza la tarjeta solo en el caché (sin API). |
| 2026-11-09 | F4: ruff check, ruff format --check, mypy, pytest con cobertura y `check_traceability.py` en verde (ver informe). F5 sigue ⚪: solo local, PR y CI pendientes de confirmación del usuario. |
| 2026-11-09 | F5: PR #12 fusionado en `develop` con merge commit, CI en verde en Windows, Linux y macOS con Python 3.12, 3.13 y 3.14. Estado: `done`. |
