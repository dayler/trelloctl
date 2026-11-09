---
id: M06
title: Registro, CLI, ayuda, versión y comandos config
status: pending
wave: 3
depends_on: [M01, M03, M04]
branch: feature/m06-cli
tags: [plan, milestone, trelloctl]
---

# M06 — Registro, CLI, ayuda, versión y comandos `config`

**Estado:** ⚪ Pendiente · **Ola:** 3 · **Depende de:** M01, M03, M04 · **Rama:** `feature/m06-cli`

## Objetivo

Construir el registro único de operaciones y el adaptador CLI que lo convierte en argparse, con opciones globales, ayuda en todos los niveles, errores de uso como JSON, códigos de salida y log por invocación. Implementar `version` y los comandos `config` que no necesitan resolver tableros por nombre.

## Lectura obligatoria

PRD RF‑1 (AC‑1.2, 1.3, 1.6, 1.10), RF‑3, RF‑4, RF‑6, RF‑24, RNF‑3, RNF‑10, §10.5 completo, §10.10 (registro único), aclaraciones A‑4, A‑6 y A‑7 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `registry.py`: especificación de operación (nombre, grupo, parámetros con tipo, descripción y ejemplo, handler, si exige `--yes`, si es lectura cacheada, si queda fuera de MCP) y un bloque delimitado por grupo para que los milestones siguientes añadan entradas sin conflictos de merge.
- `cli.py`: árbol de subparsers desde el registro, opciones globales (`--board`, `--compact`, `--no-cache`, `--log-level`, `-v`), `--help`/`-h` en todos los niveles, invocación sin argumentos, errores de argparse como envelope `USAGE`, traducción de `error.code` a código de salida, excepciones no controladas a `INTERNAL`, línea de log por invocación.
- Texto de la ayuda general de §10.5, en inglés.
- Comandos: `version`, `config init [--force]`, `config show`, `config validate`, `config board show`, `config board set <id|shortLink>`, `config board unset`. `config board set` por nombre o URL lo completa M07.
- Orden de validación previo a llamar al API (A‑7) en un único punto reutilizable.

## Fuera de alcance

Resolución de tableros por nombre (M07), comandos de los demás grupos.

## Archivos

- **Crea:** `src/trelloctl/registry.py`, `src/trelloctl/cli.py`, `src/trelloctl/app/commands/config.py`, `src/trelloctl/app/commands/version.py`, `tests/unit/test_cli*`, `tests/unit/test_registry*`, `tests/acceptance/test_m06_*`.
- **No toca:** `infra/cache.py` (M05, misma ola).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados                                               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | ⚪      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC              | Tipo | Nivel | Escenario                                                                                        | Resultado esperado                                                                                      |
| ---------- | --------------- | ---- | ----- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| TC-M06-001 | AC-3.1          | P    | S     | `--help`, `-h`, `--version` y sin argumentos                                                     | stdout es texto, no JSON                                                                                |
| TC-M06-002 | AC-4.1          | P    | E     | `--help` y `-h` en nivel general, en cada grupo y en cada comando del registro                   | Salida 0; cada comando describe argumentos, opciones y un ejemplo                                       |
| TC-M06-003 | AC-4.2          | P    | E     | Invocación sin argumentos                                                                        | Ayuda general y salida 2                                                                                |
| TC-M06-004 | AC-4.3          | P    | E     | Ayuda general                                                                                    | Menciona TTL de 30 min y 5 min, la regla de selección de tablero y los 4 comandos que exigen `--yes`   |
| TC-M06-005 | AC-6.1          | P    | E     | `--version` y `version`                                                                          | Una línea con la versión; JSON con versión, versión del intérprete y plataforma                         |
| TC-M06-006 | AC-3.1          | P    | E     | Cada comando del registro con argumentos válidos o con un error esperable                        | stdout contiene exactamente un objeto JSON                                                              |
| TC-M06-007 | AC-3.4          | N    | E     | Opción desconocida, argumento obligatorio ausente, valor de tipo incorrecto                      | Envelope `ok: false`, `USAGE`, salida 2; nada de texto de argparse en stdout                            |
| TC-M06-008 | AC-3.4          | N    | E     | Excepción no controlada dentro de un handler                                                     | `INTERNAL`, salida 1; la traza va al log, no a stdout                                                   |
| TC-M06-009 | AC-3.3          | P    | E     | `version --compact`                                                                              | Una sola línea                                                                                          |
| TC-M06-010 | AC-1.3          | P    | E     | `config init` sin HOME previo                                                                    | Crea el directorio y `config.toml` con todos los campos y comentarios de la plantilla y las dos URLs de credenciales |
| TC-M06-011 | AC-1.3          | N    | E     | `config init` con `config.toml` existente (A‑6)                                                  | `USAGE`, salida 2; el archivo no cambia                                                                 |
| TC-M06-012 | AC-1.3          | P    | E     | `config init --force` con `config.toml` existente                                                | Se reescribe con la plantilla                                                                           |
| TC-M06-013 | AC-2.4          | P    | E     | `config init` en POSIX (se omite en Windows)                                                     | `config.toml` con permisos `0600`                                                                       |
| TC-M06-014 | AC-1.2          | P    | E     | Sin `config.toml`: `--help`, `--version`, `version`, `config init`, `config show`                | Ninguno devuelve `CONFIG_NOT_FOUND` (M07, M13 y M14 amplían la lista con sus comandos)                  |
| TC-M06-015 | AC-1.2          | N    | E     | Sin `config.toml`: `config validate`, `config board set`, `config board unset`                   | `CONFIG_NOT_FOUND`, salida 3                                                                            |
| TC-M06-016 | AC-1.6          | P    | E     | `config show` con archivo y con variables de entorno                                             | Configuración efectiva en JSON con el token enmascarado                                                 |
| TC-M06-017 | AC-1.6          | P    | E     | `config validate` con archivo correcto y tablero por defecto accesible                           | `ok: true`                                                                                              |
| TC-M06-018 | AC-1.6          | N    | E     | `config validate` con tres problemas de tipo                                                     | `CONFIG_INVALID` con los tres problemas, cada uno con su clave TOML                                     |
| TC-M06-019 | AC-1.6          | B    | E     | `config validate` sin tablero por defecto                                                        | `ok: true` sin llamar al API de tableros                                                                |
| TC-M06-020 | AC-1.6          | N    | E     | `config validate` con tablero por defecto que responde 404                                       | Problema reportado en `board.default`                                                                   |
| TC-M06-021 | AC-1.10         | P    | E     | `config board show` con y sin tablero por defecto                                                | id, nombre y url; o indicación de que no hay                                                            |
| TC-M06-022 | AC-1.10         | P    | E     | `config board set <id>` y luego `config board unset`                                             | Guarda el id; `.bak` creado; resto intacto; `unset` lo elimina                                          |
| TC-M06-023 | AC-24.1         | P    | E     | Una invocación con éxito y otra con error                                                        | Cada una añade su línea al log con todos los campos                                                     |
| TC-M06-024 | AC-24.3         | P    | E     | `-v` y `--log-level debug`                                                                       | El log se duplica a stderr; el nivel cambia; stdout sigue siendo solo JSON                              |
| TC-M06-025 | AC-3.2          | P    | S     | Proceso real con `--log-level debug -v`                                                          | stdout es un único objeto JSON parseable; los logs solo en stderr                                       |
| TC-M06-026 | RNF-3           | N    | E     | Intentar `--token`, `--api-key` o equivalentes                                                   | No existen: `USAGE`                                                                                     |
| TC-M06-027 | AC-2.3          | N    | E     | `TRELLOCTL_HOME` apunta a un archivo                                                             | `HOME_NOT_WRITABLE`, salida 3                                                                           |
| TC-M06-028 | AC-1.4          | P    | E     | `TRELLO_API_KEY` y `TRELLO_TOKEN` distintos de los del archivo                                   | El Trello falso recibe las del entorno                                                                  |
| TC-M06-029 | AC-1.2          | N    | E     | Validación previa con config, credenciales y tablero ausentes a la vez (A‑7)                     | Se informa primero `CONFIG_NOT_FOUND`; con config, `AUTH_MISSING`; con credenciales, `BOARD_NOT_CONFIGURED` |
| TC-M06-030 | AC-14.4         | N    | E     | Opciones declaradas excluyentes en el registro usadas a la vez                                   | `USAGE` sin llamar al API                                                                               |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
