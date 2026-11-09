---
id: M03
title: Configuración — lectura, precedencia y escritor de [board]
status: done
wave: 2
depends_on: [M01]
branch: feature/m03-configuracion
tags: [plan, milestone, trelloctl]
---

# M03 — Configuración: lectura, precedencia y escritor de `[board]`

**Estado:** 🟢 Completado · **Ola:** 2 · **Depende de:** M01 · **Rama:** `feature/m03-configuracion`

## Objetivo

Obtener la configuración efectiva (flags > entorno > `config.toml` > valores por defecto), validar tipos, seleccionar el tablero de la invocación y reescribir solo la sección `[board]` con copia de seguridad.

## Lectura obligatoria

PRD RF-1 completo, §10.5 (plantilla y configuración mínima), §10.1 (escritor limitado), aclaraciones A-7, A-16 y A-22 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `infra/settings.py`: carga con `tomllib`, valores por defecto de la plantilla, variables `TRELLOCTL_HOME`, `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_BOARD`, validación de tipos con la clave TOML de cada problema, errores `CONFIG_NOT_FOUND`, `CONFIG_INVALID` (con línea y columna), `AUTH_MISSING`, `BOARD_NOT_CONFIGURED`, vista efectiva con el token enmascarado.
- Usa `infra/paths.py` (M01) para resolver HOME.
- Selección de tablero: `--board` > `TRELLO_BOARD` > `[board].default` (la resolución del valor la hace M07).
- Escritor de `[board]`: fija o elimina `default`, conserva el resto línea a línea (incluidos comentarios y finales de línea) y deja `config.toml.bak`.

## Fuera de alcance

Comandos `config *` (M06), resolución de tableros por nombre (M07).

## Archivos

- **Crea:** `src/trelloctl/infra/settings.py`, `tests/unit/infra/test_settings*`, `tests/acceptance/test_m03_*`.
- **No toca:** `infra/trello_client.py` (M04), `domain/` (M02), en la misma ola.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | 🟢 |

## Casos de prueba automatizados

| TC         | AC      | Tipo | Nivel | Escenario                                                                                 | Resultado esperado                                                                                     |
| ---------- | ------- | ---- | ----- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| TC-M03-001 | AC-1.1 | P | C | Sin `TRELLOCTL_HOME` | HOME = directorio del usuario + `.trelloctl` |
| TC-M03-002 | AC-1.1 | P | C | Con `TRELLOCTL_HOME` apuntando a un directorio temporal | HOME = ese directorio |
| TC-M03-003 | AC-1.2 | N | C | `config.toml` inexistente al cargar para un comando que habla con Trello | `CONFIG_NOT_FOUND` |
| TC-M03-004 | AC-1.4 | P | C | `api_key`, `token` y tablero definidos en archivo, entorno y flag | Gana el flag de tablero; el entorno gana al archivo en credenciales; el archivo gana al valor por defecto |
| TC-M03-005 | AC-1.5 | N | C | Sin `api_key` ni `token` en archivo ni entorno | `AUTH_MISSING` |
| TC-M03-006 | AC-1.5, A-22 | B | C | Plantilla recién creada: `api_key = ""` | Cadena vacía cuenta como ausente: `AUTH_MISSING` |
| TC-M03-007 | AC-1.5 | P | C | Credenciales solo en el entorno | Carga correcta |
| TC-M03-008 | AC-1.6 | P | C | Vista efectiva | El token sale enmascarado y nunca completo |
| TC-M03-009 | AC-1.6, A-22 | N | C | `timeout_seconds = "x"`, TTL negativo, `level = "loud"` | `CONFIG_INVALID` con un problema por clave TOML (`http.timeout_seconds`, …) |
| TC-M03-010 | AC-1.7 | N | C | TOML malformado | `CONFIG_INVALID` con línea y columna en `details` |
| TC-M03-011 | AC-1.8 | P | C | `--board`, `TRELLO_BOARD` y `[board].default` presentes; luego sin flag; luego solo default | Se elige en ese orden |
| TC-M03-012 | AC-1.8 | N | C | Ninguna de las tres fuentes | `BOARD_NOT_CONFIGURED` |
| TC-M03-013 | AC-1.8 | B | C | `default = ""` | `BOARD_NOT_CONFIGURED` |
| TC-M03-014 | AC-1.10 | P | C | Escribir `default` en un archivo con comentarios y otras secciones | Solo cambia la línea de `[board]`; resto idéntico byte a byte; `config.toml.bak` con el contenido previo |
| TC-M03-015 | AC-1.10, A-22 | B | C | Archivo sin sección `[board]` | Se añade al final; resto intacto |
| TC-M03-016 | AC-1.10 | B | C | Archivo con finales de línea CRLF | Se conservan CRLF |
| TC-M03-017 | AC-1.10 | P | C | Eliminar el tablero por defecto | Desaparece `default` de `[board]`; `.bak` creado |
| TC-M03-018 | AC-1.11 | P | C | `logging.level`, `http.timeout_seconds`, `http.base_url`, `cache.*_ttl_seconds` definidos | Se exponen a logging, cliente HTTP y caché con esos valores |
| TC-M03-019 | AC-1.11, A-16 | N | C | `cache.enabled = false` y otra clave desconocida en `config.toml` | Se ignoran con un aviso por clave en `meta.warnings`; el caché sigue activo (solo `--no-cache` lo desactiva) |
| TC-M03-020 | AC-1.12 | P | C | Archivo con solo `[auth]` | Válido; el resto toma los valores de la plantilla de §10.5 |
| TC-M03-021 | AC-1.12 | B | C | Archivo con `[http]` parcial (solo `timeout_seconds`) | `base_url` toma el valor por defecto |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1-F3: `infra/settings.py` (carga, precedencia, validación, escritor de `[board]`), 21 TCs automatizados y verificados uno a uno, pruebas unitarias |
| 2026-11-09 | F4: ruff check, ruff format --check, mypy, pytest (110 pruebas, cobertura 98,92 %) y trazabilidad en verde; en `settings.py` 98 % |
| 2026-11-09 | F5 pendiente: solo en local, PR y CI a la espera de confirmación del usuario |
| 2026-11-09 | F5: PR #4 fusionado en `develop` con merge commit, CI en verde en Windows, Linux y macOS con Python 3.12, 3.13 y 3.14. Estado: `done`. |
