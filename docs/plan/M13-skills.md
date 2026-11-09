---
id: M13
title: Skill trelloctl — contenido, list, install y export
status: pending
wave: 4
depends_on: [M06]
branch: feature/m13-skills
tags: [plan, milestone, trelloctl]
---

# M13 — Skill `trelloctl`: contenido, `list`, `install` y `export`

**Estado:** ⚪ Pendiente · **Ola:** 4 · **Depende de:** M06 · **Rama:** `feature/m13-skills`

## Objetivo

Redactar el `SKILL.md` en inglés con el contenido de §10.9 y empaquetarlo; implementar `skills list|install|export`.

## Lectura obligatoria

PRD RF‑26, AC‑28.6, RNF‑10, §10.5 (CLI y envelope), §10.9, §10.10 (registro MCP), aclaración A‑6 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `skills/trelloctl/SKILL.md` con frontmatter `name`, `description`, `version` (la del paquete al generarse) y las secciones de §10.9, cada una con sus comandos y un ejemplo de salida. Los comandos de milestones aún no fusionados se documentan según §10.5; M16 verifica que todos los ejemplos son invocaciones válidas del CLI final.
- `skills list`, `skills install --harness claude [--scope user|project | --dir <ruta>] [--force]`, `skills export --dir <ruta>`.
- Inclusión del skill en el wheel.

## Fuera de alcance

Instalación directa para harness distintos de Claude Code (PRD §12, decisión 3).

## Archivos

- **Crea:** `src/trelloctl/skills/trelloctl/SKILL.md`, `src/trelloctl/app/commands/skills.py`, `tests/unit/app/commands/test_skills*`, `tests/acceptance/test_m13_*`.
- **Modifica:** `registry.py` (bloque `skills`).
- **No toca:** `app/resolver.py`, `app/commands/cache.py` (M07, misma ola).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación (incluida la redacción del skill)                | ⚪      |
| F2   | Pruebas unitarias                                               | ⚪      |
| F3   | TCs automatizados                                               | ⚪      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | ⚪      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | ⚪      |

## Casos de prueba automatizados

| TC         | AC               | Tipo | Nivel | Escenario                                                                                  | Resultado esperado                                                                                   |
| ---------- | ---------------- | ---- | ----- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| TC-M13-001 | AC-26.1          | P    | E     | `skills list`                                                                              | Muestra `trelloctl` con la versión del paquete                                                       |
| TC-M13-002 | AC-26.1          | P    | C     | Frontmatter de `SKILL.md`                                                                  | `name = trelloctl`, `description` no vacía, `version` igual a la del paquete                         |
| TC-M13-003 | AC-26.1          | P    | S     | Construir el wheel e inspeccionarlo                                                        | Incluye `skills/trelloctl/SKILL.md`                                                                  |
| TC-M13-004 | AC-26.2          | P    | E     | `skills install --harness claude` con HOME de usuario temporal                             | Copia en `~/.claude/skills/trelloctl/SKILL.md`                                                       |
| TC-M13-005 | AC-26.2          | P    | E     | `--scope project` con cwd temporal; `--dir <ruta>`                                         | Copia en `./.claude/skills/trelloctl/`; copia en la ruta indicada                                    |
| TC-M13-006 | AC-26.2          | N    | E     | Destino con skill existente sin `--force` (A‑6)                                            | `USAGE`; archivo intacto                                                                             |
| TC-M13-007 | AC-26.2          | P    | E     | Destino con skill existente y `--force`                                                    | Sobrescrito                                                                                          |
| TC-M13-008 | AC-26.2          | N    | E     | `--harness codex`                                                                          | `USAGE`                                                                                              |
| TC-M13-009 | AC-26.2          | N    | E     | `--scope user --dir x`                                                                     | `USAGE`                                                                                              |
| TC-M13-010 | AC-26.3          | P    | E     | `skills export --dir <ruta>`                                                               | `SKILL.md` en la ruta                                                                                |
| TC-M13-011 | AC-26.4          | P    | C     | Secciones del skill                                                                        | Las 10 secciones de §10.9 y cada comando listado en su fila                                         |
| TC-M13-012 | AC-26.4          | P    | C     | Reglas del skill                                                                           | Archivar en lugar de borrar, `--yes` solo con confirmación explícita, `--dry-run` antes de mover, no tocar `config.toml` |
| TC-M13-013 | AC-28.6          | P    | C     | Skill y plantilla de `config init`                                                         | Ambos recomiendan email‑to‑board y la app de Slack sobre una lista del tablero de trabajo            |
| TC-M13-014 | RNF-10           | P    | C     | Texto del skill                                                                            | En inglés: sin caracteres ni palabras del español según la heurística común                          |
| TC-M13-015 | AC-1.2           | P    | E     | `skills list`, `skills install` y `skills export` sin `config.toml`                        | Funcionan                                                                                            |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
