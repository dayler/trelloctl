---
id: M11
title: Adjuntos — listar, descargar, subir y limpiar
status: pending
wave: 5
depends_on: [M07]
branch: feature/m11-adjuntos
tags: [plan, milestone, trelloctl]
---

# M11 — Adjuntos: listar, descargar, subir y limpiar

**Estado:** ⚪ Pendiente · **Ola:** 5 · **Depende de:** M07 · **Rama:** `feature/m11-adjuntos`

## Objetivo

Implementar `card attachments list|download|add|clear` y la parte de adjuntos de `cache clear --attachments` y `cache status`.

## Lectura obligatoria

PRD RF‑21 completo, AC‑2.2, AC‑22.15, §10.6 (directorio `attachments/`), §11 (riesgo de descargas), aclaraciones A‑4, A‑9 y A‑11 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Listado desde caché con ruta local.
- Descarga por `--id` o `--all` en serie con cabecera OAuth, sincronización (nuevos, cambiados, iguales, huérfanos), `skipped` para enlaces, `RATE_LIMITED` con `error.details.partial`, `ATTACHMENT_GONE`, `ATTACHMENT_NOT_FOUND`, nombres de archivo saneados (A‑9).
- Subida multipart o enlace, `ATTACHMENT_TOO_LARGE` antes de llamar al API.
- Limpieza local sin llamadas al API y sin exigir `config.toml` (A‑4).
- Rutas del Trello falso: `cards/{id}/attachments` (lista, alta) y descarga autenticada por cabecera.

## Fuera de alcance

Nada más del grupo `card`.

## Archivos

- **Crea:** `src/trelloctl/app/commands/attachments.py`, `tests/fakes/trello/attachments.py`, `tests/unit/app/commands/test_attachments*`, `tests/acceptance/test_m11_*`.
- **Modifica:** `registry.py` (bloque `card attachments`).
- **No toca:** `card.py`, `board.py`, `lists.py`, `labels.py`, `activity.py`, `doctor.py`, `infra/`, `domain/`, `app/resolver.py` (ola 5 en paralelo).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados                                               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | ⚪      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC                | Tipo | Nivel | Escenario                                                                                       | Resultado esperado                                                                                      |
| ---------- | ----------------- | ---- | ----- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| TC-M11-001 | AC-21.1           | P    | E     | `card attachments list X` con un archivo y un enlace                                            | id, nombre, bytes, mimeType, fecha, url, `isUpload`, ruta local nula; 0 peticiones                     |
| TC-M11-002 | AC-21.2           | P    | E     | `download --id <id>`                                                                            | Archivo en `<HOME>/attachments/<board>/<card>/<id>_<nombre>`; cabecera OAuth; ruta local en el listado  |
| TC-M11-003 | AC-21.2           | P    | E     | `download --id` de un enlace externo                                                            | `skipped` con su url; 0 peticiones de descarga                                                          |
| TC-M11-004 | AC-21.3           | P    | E     | `download --all` con un adjunto nuevo, uno igual, uno cambiado y un local huérfano              | 1 lectura de la lista; descarga nuevo y cambiado; conserva el igual; borra el huérfano; respuesta con las 4 categorías |
| TC-M11-005 | AC-21.3           | B    | E     | Adjunto con los mismos bytes y fecha distinta                                                   | Se descarga de nuevo                                                                                    |
| TC-M11-006 | AC-21.3           | N    | E     | 429 en la segunda de tres descargas                                                             | `RATE_LIMITED`, salida 7; `error.details.partial` con lo hecho; la tercera no se intenta                |
| TC-M11-007 | AC-21.4           | N    | E     | `download --id` de un adjunto borrado en Trello con copia local                                 | Copia local borrada; `ATTACHMENT_GONE`                                                                  |
| TC-M11-008 | AC-21.4           | N    | E     | `download --id` desconocido                                                                     | `ATTACHMENT_NOT_FOUND`                                                                                  |
| TC-M11-009 | AC-21.2           | N    | E     | `download` sin `--all` ni `--id`; con ambos                                                     | `USAGE`                                                                                                 |
| TC-M11-010 | AC-21.2           | B    | E     | Adjunto llamado `../../x` y otro `a:b*?.txt` (A‑9)                                              | Archivos saneados dentro del directorio de la tarjeta; nada fuera de `attachments/`                     |
| TC-M11-011 | AC-21.5           | P    | E     | `add X --file informe.pdf --name Informe`                                                       | `POST` multipart; adjunto en caché                                                                      |
| TC-M11-012 | AC-21.5           | P    | E     | `add X --url https://example.com --name Enlace`                                                 | Enlace adjunto; en caché                                                                                |
| TC-M11-013 | AC-21.5           | N    | E     | Archivo de `max_upload_bytes + 1`                                                               | `ATTACHMENT_TOO_LARGE`, salida 6, 0 peticiones                                                          |
| TC-M11-014 | AC-21.5           | B    | E     | Archivo de exactamente `max_upload_bytes`                                                       | Se sube                                                                                                 |
| TC-M11-015 | AC-21.5           | N    | E     | `--file` y `--url` a la vez; `--file` inexistente (A‑11)                                        | `USAGE`                                                                                                 |
| TC-M11-016 | AC-21.6           | P    | E     | `card attachments clear X`; luego `clear Y --id <id>`                                           | Archivos borrados, ruta local nula, directorios vacíos eliminados, 0 peticiones                         |
| TC-M11-017 | AC-21.6, AC-22.15 | P    | E     | `cache clear --attachments` con adjuntos de dos tableros                                        | Todos borrados y `attachments/` sin subdirectorios vacíos                                               |
| TC-M11-018 | AC-21.6, AC-1.2   | P    | E     | `card attachments clear <id> --board <id>` sin `config.toml` (A‑4)                              | Funciona                                                                                                |
| TC-M11-019 | AC-22.15          | P    | E     | `cache status` tras descargar 3 archivos                                                        | Espacio de adjuntos igual a la suma de sus bytes                                                        |
| TC-M11-020 | AC-2.2            | N    | E     | Un archivo ajeno dentro de `attachments/<board>/<card>/`                                        | `clear` no lo borra                                                                                     |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
