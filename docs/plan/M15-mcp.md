---
id: M15
title: Servidor MCP por stdio (experimental)
status: pending
wave: 7
depends_on: [M08, M10, M11, M12]
branch: feature/m15-mcp
tags: [plan, milestone, trelloctl]
---

# M15 — Servidor MCP por stdio (experimental)

**Estado:** ⚪ Pendiente · **Ola:** 7 · **Depende de:** M08, M10, M11, M12 · **Rama:** `feature/m15-mcp`

## Objetivo

Implementar `mcp serve` como adaptador delgado sobre el registro: herramientas generadas, envelope compacto como texto, `yes: true`, stdout limpio, lectura de configuración al arrancar y TTL evaluado por llamada.

## Lectura obligatoria

PRD RF‑25, AC‑3.3, AC‑22.8, §10.5 (servidor MCP), §10.10 completo, §11 (riesgo del SDK), aclaración A‑1 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `mcp_server.py` con el SDK oficial `mcp` importado de forma perezosa; rango de versión `mcp>=X,<X+1` fijado en el extra de `pyproject.toml` con la versión estable vigente al ejecutar el milestone.
- Herramientas `snake_case` generadas del registro con sus esquemas, parámetro opcional `board`, exclusiones de AC‑25.1.
- `mcp serve` sin el extra: `USAGE` con el comando de instalación (A‑1).
- Documentación como experimental en `--help`, README y skill.

## Fuera de alcance

Transportes distintos de stdio (PRD §9).

## Archivos

- **Crea:** `src/trelloctl/mcp_server.py`, `tests/unit/test_mcp_server*`, `tests/acceptance/test_m15_*`, cliente MCP de pruebas por stdio en `tests/fakes/mcp_client.py`.
- **Modifica:** `pyproject.toml` (rango del extra), `uv.lock`, `registry.py` (entrada `mcp serve`), `README.md` (sección MCP), `SKILL.md` (sección MCP si falta algo).
- **No toca:** `doctor.py` (M14, misma ola), `app/`, `infra/`, `domain/`.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados                                               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | ⚪      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC               | Tipo | Nivel | Escenario                                                                                     | Resultado esperado                                                                                   |
| ---------- | ---------------- | ---- | ----- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| TC-M15-001 | AC-25.1          | P    | M     | `initialize` y `tools/list`                                                                   | Una herramienta `snake_case` por operación de RF‑8 a RF‑21 y RF‑28 más `cache_reload` y `cache_status`, cada una con `board` opcional |
| TC-M15-002 | AC-25.1          | N    | M     | Buscar `config_*`, `doctor`, `version`, `skills_*`, `cache_clear`, `card_attachments_clear`   | No están en `tools/list`; `tools/call` sobre ellas devuelve error de protocolo                       |
| TC-M15-003 | AC-25.1          | P    | C     | Esquema de cada herramienta frente a la operación del registro                                | Mismos parámetros, tipos y obligatoriedad que en la CLI                                              |
| TC-M15-004 | AC-25.1          | P    | M     | `card_list` con `board` por nombre                                                            | Opera sobre ese tablero                                                                              |
| TC-M15-005 | AC-25.2, AC-3.3  | P    | M     | `tools/call card_get` con éxito                                                               | Envelope compacto como contenido de texto; `isError: false`                                          |
| TC-M15-006 | AC-25.2          | N    | M     | `tools/call card_get` sobre una tarjeta inexistente                                           | `isError: true` con el envelope de `CARD_NOT_FOUND`                                                  |
| TC-M15-007 | AC-25.2, AC-16.4 | N    | M     | `board_label_remove` sin `yes`; luego con `yes: true`                                         | `CONFIRMATION_REQUIRED` sin escritura; luego borrada                                                 |
| TC-M15-008 | AC-25.3          | P    | M     | Sesión completa con `--log-level debug -v` y una llamada que falla                            | Toda línea de stdout es un mensaje JSON‑RPC válido; los logs van a stderr y al archivo               |
| TC-M15-009 | AC-25.5          | P    | M     | Dos `card_list` separados por el vencimiento del TTL (TTL de 1 s en la configuración)         | La segunda recarga                                                                                   |
| TC-M15-010 | AC-25.5          | P    | M     | Cambiar `config.toml` con el servidor vivo; reiniciar                                         | Sin efecto hasta reiniciar; efectivo tras el reinicio                                                |
| TC-M15-011 | AC-25.5          | P    | M     | Dos servidores simultáneos escribiendo                                                        | Comparten `cache.db` y log sin errores; el log distingue los PID                                     |
| TC-M15-012 | AC-22.8          | P    | M     | `mcp serve --no-cache` con varias lecturas                                                    | `cache.db` no se crea en toda la sesión                                                              |
| TC-M15-013 | AC-25.4          | N    | S     | `mcp serve` con el paquete `mcp` no importable (A‑1)                                          | `USAGE`, salida 2, mensaje en inglés con el comando de instalación del extra                         |
| TC-M15-014 | AC-25.4          | P    | S     | Importar `trelloctl.cli` y ejecutar `card list`                                               | `mcp` no aparece entre los módulos cargados                                                          |
| TC-M15-015 | AC-25.4          | P    | C     | `--help` general, README y `SKILL.md`                                                         | Los tres describen MCP como experimental y el extra `trelloctl[mcp]`                                 |
| TC-M15-016 | AC-25.1          | B    | M     | Cierre de stdin por el harness                                                                | El proceso termina con salida 0 sin escribir en stdout                                               |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
