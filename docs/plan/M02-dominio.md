---
id: M02
title: Dominio — modelos, mover y clasificación de identificadores
status: done
wave: 2
depends_on: [M01]
branch: feature/m02-dominio
tags: [plan, milestone, trelloctl]
---

# M02 — Dominio: modelos, mover y clasificación de identificadores

**Estado:** 🟢 Completado · **Ola:** 2 · **Depende de:** M01 · **Rama:** `feature/m02-dominio`

## Objetivo

Implementar la capa de dominio pura: modelos de Trello, cálculo de destino por desplazamiento, conversión de `--pos` a la posición de Trello y clasificación de identificadores. Sin caché, red ni configuración.

## Lectura obligatoria

PRD RF-7, AC-1.9, AC-9.1, AC-9.4, AC-12.2, RF-14, §10.3 (reglas de dependencia), §10.6 (modelo), §12 decisiones 8 y 9, aclaraciones A-3, A-14, A-21 y A-22 del [[00-plan-maestro#Aclaraciones de comportamiento]].

## Alcance

- `domain/models.py`: Board (con `kind`), List, Card, Label, Member, Comment, Checklist, CheckItem, Attachment, con construcción desde el JSON de Trello y tolerancia a campos ausentes.
- `domain/mover.py`: índice destino para `--left N`, `--right N` y `--to-index N` sobre listas abiertas, `--clamp`, `MOVE_OUT_OF_RANGE` con detalles; conversión de `--pos top|bottom|<índice>` a la posición de Trello con la regla A-3.
- Clasificador de identificadores de tarjeta (AC-7.3) y de tablero (AC-1.9), puro, sin resolver contra nada, en `domain/models.py`. Para un valor de 8 alfanuméricos devuelve las dos interpretaciones, nombre y shortLink, en ese orden (A-14).

## Fuera de alcance

Resolución contra caché o Trello (M07).

## Archivos

- **Crea:** `src/trelloctl/domain/models.py`, `src/trelloctl/domain/mover.py`, `tests/unit/domain/test_models*`, `tests/unit/domain/test_mover*`, `tests/acceptance/test_m02_*`.
- **No toca:** `domain/errors.py` (M01; lo usa para `USAGE` y `MOVE_OUT_OF_RANGE`), ni `infra/` (M03, M04 en la misma ola).

## Fases

| Fase | Descripción                                                     | Estado |
| ---- | --------------------------------------------------------------- | ------ |
| F1   | Implementación                                                  | 🟢     |
| F2   | Pruebas unitarias                                               | 🟢     |
| F3   | TCs automatizados                                               | 🟢     |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad | 🟢     |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`           | 🟢 |

## Casos de prueba automatizados

| TC         | AC             | Tipo | Nivel | Escenario                                                                     | Resultado esperado                                                                       |
| ---------- | -------------- | ---- | ----- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| TC-M02-001 | AC-14.2 | P | C | `right 1` desde el índice 1 de 5 listas abiertas | Destino índice 2 |
| TC-M02-002 | AC-14.2 | P | C | `left 2` desde el índice 3 | Destino índice 1 |
| TC-M02-003 | AC-14.2 | P | C | Lista archivada entre la actual y la siguiente | La archivada no cuenta: destino la siguiente abierta |
| TC-M02-004 | AC-14.2 | B | C | Desplazamiento sin N | N = 1 |
| TC-M02-005 | AC-14.2 | N | C | N = 0 o negativo | `USAGE` |
| TC-M02-006 | AC-14.3 | N | C | `right 3` desde el índice 3 de 5 | `MOVE_OUT_OF_RANGE` con `from_index`, `to_index`, `list_count` |
| TC-M02-007 | AC-14.3 | B | C | El mismo caso con `clamp`; y `left 9` con `clamp` | Última lista; primera lista |
| TC-M02-008 | AC-14.3, A-22 | B | C | `right 1` con `clamp` desde la última lista | Destino igual al origen; el resultado lo indica |
| TC-M02-009 | AC-9.4 | P | C | `to-index 0` y `to-index 2` para una lista | Posición entre las vecinas abiertas correspondientes |
| TC-M02-010 | AC-9.4 | N | C | `to-index` mayor o igual al número de listas abiertas | `MOVE_OUT_OF_RANGE` |
| TC-M02-011 | AC-9.1, AC-12.2 | P | C | `--pos top`, `bottom` e índice intermedio | `top`, `bottom` y un valor entre las posiciones de los vecinos |
| TC-M02-012 | AC-12.2, A-3 | B | C | Índice igual al número de elementos | Equivale a `bottom` |
| TC-M02-013 | AC-12.2, A-3 | N | C | Índice mayor que el número de elementos, negativo o no numérico | `USAGE` |
| TC-M02-014 | AC-12.2 | B | C | Contenedor vacío: índice 0 e índice 1 | 0 válido (`bottom`); 1 es `USAGE` |
| TC-M02-015 | AC-7.3, A-14 | P | C | Clasificar 24 hexadecimales, 8 alfanuméricos, URL `/c/…`, solo dígitos, texto | id; candidatos nombre y shortLink; shortLink extraído; idShort; nombre |
| TC-M02-016 | AC-7.3 | B | C | `12345678` (8 dígitos) y 24 caracteres no hexadecimales | idShort; nombre |
| TC-M02-017 | AC-1.9, A-14 | P | C | Clasificar tablero: id, shortLink, URL `/b/<shortLink>/slug?x=1`, nombre | id y URL se reconocen; un valor de 8 alfanuméricos es candidato a nombre y luego a shortLink; de la URL se extrae el shortLink |
| TC-M02-018 | AC-13.1 | P | C | Construir una tarjeta desde JSON completo de Trello | Lista, etiquetas, miembros, due, `dueComplete`, url, `dateLastActivity`, `badges` |
| TC-M02-019 | AC-13.1 | B | C | JSON de tarjeta sin `due`, `desc`, ni `badges` | Modelo válido con valores nulos o cero |
| TC-M02-020 | §10.3 | P | C | Análisis estático de `domain/` | No importa `infra`, `app`, `sqlite3`, `urllib`, `socket` ni `tomllib` (§10.3) |
| TC-M02-021 | AC-7.3, A-14 | B | C | Clasificar `Refactor` (8 alfanuméricos) | Candidato a nombre y, después, a shortLink |
| TC-M02-022 | AC-9.4, AC-14.2, A-21 | B | C | `to-index -1`; mover a la posición 0 dentro de la misma lista; desplazamiento desde una lista archivada | `USAGE`; posición final 0; `MOVE_OUT_OF_RANGE` con `from_index` nulo |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]].

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | Implementación de `domain/models.py` y `domain/mover.py`; 22/22 TCs automatizados y verificados uno a uno (nivel C). Compuertas locales: ruff check, ruff format --check, mypy, pytest (107 pruebas, cobertura 99,82 %) y trazabilidad, todas en verde. |
| 2026-11-09 | F5: PR #3 fusionado en `develop` con merge commit, CI en verde en Windows, Linux y macOS con Python 3.12, 3.13 y 3.14. Estado: `done`. |
