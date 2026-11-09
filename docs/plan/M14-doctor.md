---
id: M14
title: Diagnóstico doctor y doctor --api
status: in_review
wave: 7
depends_on: [M08, M10, M11, M12, M13]
branch: feature/m14-doctor
tags: [plan, milestone, trelloctl]
---

# M14 — Diagnóstico `doctor` y `doctor --api`

**Estado:** 🔵 En revisión · **Ola:** 7 · **Depende de:** M08, M10, M11, M12, M13 · **Rama:** `feature/m14-doctor`

## Objetivo

Implementar `doctor`, `doctor --json` y `doctor --api` con todas las comprobaciones de AC-5.2 en orden, sin dejar rastro. Va en la ola 7 porque las lecturas de humo y el contraste del inventario necesitan las rutas de lectura de todos los grupos en el Trello falso.

## Lectura obligatoria

PRD RF-5, AC-28.5, RNF-8, §10.8 (TLS en macOS), §11 (cambios del API), aclaraciones A-2, A-12, A-19 y A-22 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- Comprobaciones en el orden de AC-5.2 con `OK`, `WARN`, `FAIL`, explicación y acción sugerida.
- Salida de texto y `--json` (A-2); salida 0 sin `FAIL`, 1 con alguno.
- `--api`: descarga de la especificación OpenAPI, contraste con el inventario único de endpoints, métodos y parámetros que usa la herramienta, que vive en `doctor.py`, `WARN` por obsoletos, `FAIL` por ausentes, `WARN` si no hay especificación, lecturas de humo contra el tablero por defecto.
- Si `base_url` no es HTTPS, la comprobación TLS es `WARN` (A-19). El mensaje de fallo TLS incluye siempre la sugerencia de certificados para macOS, sin ramificar por plataforma.
- La URL de la especificación es un valor del módulo `doctor` que los TCs en proceso sustituyen por la del Trello falso, sin variables de entorno nuevas. DNS y TLS se comprueban contra el host de `base_url` (A-19) y sus fallos se simulan.

## Fuera de alcance

Cualquier corrección automática del entorno.

## Archivos

- **Crea:** `src/trelloctl/doctor.py`, `tests/fixtures/openapi/` (especificación reducida de fixture), `tests/fakes/trello/openapi.py`, `tests/unit/test_doctor*`, `tests/acceptance/test_m14_*`.
- **Modifica:** `registry.py` (entrada `doctor`, con la marca de salida de texto de M06).
- **No toca:** `cli.py`, `mcp_server.py` (M15, misma ola), `app/commands/*`, `infra/`, `domain/`.

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢      |
| F2   | Pruebas unitarias                                               | 🟢      |
| F3   | TCs automatizados                                               | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪ (local only: PR and CI pending user confirmation) |

## Casos de prueba automatizados

| TC         | AC               | Tipo | Nivel | Escenario                                                                              | Resultado esperado                                                                         |
| ---------- | ---------------- | ---- | ----- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| TC-M14-001 | AC-5.1, AC-3.1 | P | E | `doctor` con entorno sano | Informe de texto, no JSON, salida 0 |
| TC-M14-002 | AC-5.1 | P | E | `doctor --json` con entorno sano | Envelope `ok: true` con cada comprobación, su estado y explicación |
| TC-M14-003 | AC-5.2 | P | E | `doctor --json` | Comprobaciones exactamente en el orden de AC-5.2 |
| TC-M14-004 | AC-5.3 | P | E | Entorno con un `FAIL` | Cada comprobación con `OK`, `WARN` o `FAIL` y una línea; el `FAIL` con acción sugerida |
| TC-M14-005 | AC-5.4 | P | E | Solo `WARN` | Salida 0 |
| TC-M14-006 | AC-5.4, A-2 | N | E | Token inválido (401 en `members/me`) con y sin `--json` | Salida 1; en JSON, `ok: true` con el `FAIL` en los datos |
| TC-M14-007 | AC-5.2 | N | E | Intérprete por debajo de 3.12 (versión simulada) | `FAIL` con la acción sugerida |
| TC-M14-008 | AC-5.2, AC-1.2 | N | E | Sin `config.toml` | `doctor` se ejecuta; `FAIL` de configuración que sugiere `config init` |
| TC-M14-009 | AC-5.2 | N | E | `config.toml` malformado | `FAIL` con línea y columna |
| TC-M14-010 | AC-5.2 | N | E | Sin credenciales | `FAIL` de credenciales |
| TC-M14-011 | AC-5.2, A-19 | N | E | DNS fallido; TLS fallido por certificados (simulados), con `base_url` HTTPS | `FAIL` en cada caso; el de TLS sugiere instalar certificados en macOS |
| TC-M14-012 | AC-5.2 | N | E | HOME o `logs/` no escribibles | `FAIL` de directorio de trabajo |
| TC-M14-013 | AC-5.2, A-12 | B | E | Sin tablero por defecto | `WARN`, sin llamadas al tablero |
| TC-M14-014 | AC-5.2 | N | E | Tablero por defecto inaccesible | `FAIL` del acceso al tablero |
| TC-M14-015 | AC-5.2 | P | E | `cache.db` sano; luego corrupto | `OK` con versión de esquema; `FAIL` de integridad |
| TC-M14-016 | AC-5.2, A-12 | P | E | Extra `mcp` localizable y no localizable (simulado) | `OK` con la versión; `OK` con "not installed (optional)" |
| TC-M14-017 | AC-5.2, A-12 | P | E | Skill instalado en `user` y `project` con versión igual, distinta y ausente | `OK`; `WARN`; `WARN` |
| TC-M14-018 | AC-28.5 | P | E | `members/me?fields=inbox` sin `idBoard` ni `idList` | `WARN` del Inbox; el resto de comprobaciones se ejecutan y la salida no cambia por él |
| TC-M14-019 | AC-5.5 | P | E | `doctor --api` con especificación que contiene todo el inventario | `OK` del contrato y una lectura de humo por endpoint de lectura |
| TC-M14-020 | AC-5.5 | N | E | Especificación sin uno de los endpoints usados; con un parámetro obsoleto | `FAIL` nombrando el endpoint; `WARN` nombrando el parámetro |
| TC-M14-021 | AC-5.5 | N | E | Especificación no disponible (404 o red) | `WARN` y las lecturas de humo se ejecutan igual |
| TC-M14-022 | AC-5.5 | P | C | Inventario de `doctor.py` frente a las rutas registradas en los módulos del Trello falso | Coinciden: ninguna ruta del cliente falta en el inventario y ninguna entrada del inventario carece de ruta |
| TC-M14-023 | AC-5.6 | P | E | `doctor` sobre un HOME sin `logs/` ni `attachments/` | Los crea; tras terminar no queda ningún archivo temporal |
| TC-M14-024 | RNF-8 | P | E | `doctor --api` | Solo se contactan `base_url` y la URL de la especificación |
| TC-M14-025 | AC-5.2, A-19 | B | E | `doctor` con `base_url` `http://` (Trello falso) y el resto sano | Comprobación TLS en `WARN` «not applicable»; salida 0 |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1-F3: `doctor.py` (checks in AC-5.2 order, `--api` contract and smoke reads, single endpoint inventory), `doctor` block in `registry.py` (`text_output`, `mcp=False`), fake route `tests/fakes/trello/openapi.py` with fixture `tests/fixtures/openapi/swagger.v3.json`, 29 unit tests and 25 acceptance TCs (TC-M14-001 to 025), each verified individually |
| 2026-11-09 | Decisions: `--api` adds `api_spec`, `api_contract` (FAIL for missing endpoint or method), `api_parameters` (WARN for deprecated or undocumented parameters) and one `smoke GET <path>` check per read endpoint; checks that need the API are skipped as `WARN` when credentials or the token are invalid; the `/members/{id}` smoke read uses a member id taken from the default board; the file-download route is in the inventory with `spec=False`; TC-M14-012 and TC-M14-023 skip the CLI's own `ensure_home` (monkeypatch) because the CLI would otherwise fail before `doctor` runs |
| 2026-11-09 | F4 local gates: `uv sync --all-extras` ok; `ruff check` ok; `ruff format --check` ok; `mypy` ok; `pytest --cov` 923 passed, 1 skipped, coverage 99.04 % (doctor.py 99 %); `check_traceability.py` OK. F5 (PR and CI) pending user confirmation |
