---
id: M01
title: Fundamentos — rutas, errores, salida y logs
status: pending
wave: 1
depends_on: [M00]
branch: feature/m01-fundamentos
tags: [plan, milestone, trelloctl]
---

# M01 — Fundamentos: rutas, errores, salida y logs

**Estado:** ⚪ Pendiente · **Ola:** 1 · **Depende de:** M00 · **Rama:** `feature/m01-fundamentos`

## Objetivo

Construir las piezas transversales que usa todo lo demás: directorio de trabajo, catálogo de errores con sus códigos de salida, envelope JSON, serialización de fechas, logging rotativo con enmascarado y reconfiguración de consola.

## Lectura obligatoria

PRD RF-2, RF-3, RF-24, RF-27, RNF-4, RNF-5, RNF-7, §10.5 (envelope y catálogo), §10.6 (directorio de trabajo), §10.8.

## Alcance

- `infra/paths.py`: resolución de HOME (la lectura de `TRELLOCTL_HOME` la consume M03), creación de subdirectorios faltantes, comprobación de escritura, borrado restringido a archivos propios.
- `domain/errors.py`: excepción tipada por código del catálogo y tabla código → clase → código de salida.
- `output.py`: envelope de éxito y de error, `meta` común (`schema_version`, `command`, `board_id`, `elapsed_ms`), indentado o compacto, UTF-8 sin escapar, salto de línea final, fechas ISO-8601 UTC, reconfiguración de stdout y stderr a UTF-8.
- `infra/logging.py`: archivo rotativo, línea por invocación, nivel, duplicado a stderr, filtro que enmascara key y token, tolerancia a fallos de rotación.

## Fuera de alcance

Lectura de configuración (M03), argparse y códigos de salida en el proceso (M06).

## Archivos

- **Crea:** `src/trelloctl/infra/paths.py`, `src/trelloctl/domain/errors.py`, `src/trelloctl/output.py`, `src/trelloctl/infra/logging.py`, `tests/unit/infra/`, `tests/unit/domain/test_errors*`, `tests/unit/test_output*`, `tests/acceptance/test_m01_*`.
- **No toca:** `domain/models.py`, `domain/mover.py` (M02).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados                                               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | ⚪      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC                | Tipo | Nivel | Escenario                                                                                   | Resultado esperado                                                                        |
| ---------- | ----------------- | ---- | ----- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| TC-M01-001 | AC-2.2 | P | C | HOME existente sin `logs/` ni `attachments/` | Se crean los dos subdirectorios; los archivos existentes quedan intactos |
| TC-M01-002 | AC-2.2 | N | C | Pedir el borrado de una ruta fuera de `cache.db` y `attachments/` (absoluta ajena o con `..`) | Se rechaza sin borrar nada |
| TC-M01-003 | AC-2.3 | N | C | HOME apunta a una ruta ocupada por un archivo | `HOME_NOT_WRITABLE`, clase configuración (salida 3) |
| TC-M01-004 | AC-2.3 | N | C | HOME existe pero la prueba de escritura falla (simulada) | `HOME_NOT_WRITABLE` |
| TC-M01-005 | AC-3.3 | P | C | Envelope de éxito con datos que contienen `ñ`, `á` y caracteres CJK | Indentado a dos espacios, caracteres sin escapar, UTF-8 y salto de línea final |
| TC-M01-006 | AC-3.3 | P | C | El mismo envelope en modo compacto | Una sola línea más el salto final; re-parseable e igual al indentado |
| TC-M01-007 | AC-3.3 | B | C | Datos con caracteres de control y emoji | JSON válido y re-parseable |
| TC-M01-008 | AC-3.4, RNF-4 | P | C | Envelope de error para un código del catálogo | `ok: false`, `error.code`, `error.message`, `error.details`, `meta.schema_version = "1"` |
| TC-M01-009 | RNF-5 | P | C | Recorrer el catálogo de §10.5 | Cada código tiene exactamente una clase y el código de salida de la tabla (0 a 9) |
| TC-M01-010 | RNF-5 | N | C | Excepción no tipada | Se traduce a `INTERNAL`, salida 1 |
| TC-M01-011 | RNF-7 | P | C | Serializar fechas de Trello (`…Z`) y `datetime` con zona | ISO-8601 en UTC con sufijo `Z` |
| TC-M01-012 | RNF-7 | B | C | Fecha con desplazamiento `+05:30` | Convertida a UTC |
| TC-M01-013 | AC-24.1 | P | C | Registrar el cierre de una invocación | Una línea con timestamp ISO-8601, nivel, PID, comando, duración y resultado |
| TC-M01-014 | AC-24.2 | P | C | Escribir hasta superar `max_bytes` con `backup_count = 2` | Rota y conserva exactamente 2 copias |
| TC-M01-015 | AC-24.2 | B | C | La rotación falla porque el archivo está bloqueado (simulado) | Se registra un aviso y la escritura continúa sin excepción |
| TC-M01-016 | AC-24.3, AC-23.5 | P | C | Mensajes y argumentos de log con la key y el token | Ambos aparecen enmascarados en archivo y stderr |
| TC-M01-017 | AC-3.2 | P | C | Configurar el logging con y sin modo verbose | Nunca hay manejador sobre stdout; verbose añade stderr |
| TC-M01-018 | AC-27.1 | P | C | Arrancar con stdout y stderr en `cp1252` (simulado) | Quedan reconfigurados a UTF-8 |
| TC-M01-019 | AC-27.2 | P | C | Análisis estático de `src/` | Ninguna llamada a `subprocess`, `os.system`, `os.popen` ni `shell=True` |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
