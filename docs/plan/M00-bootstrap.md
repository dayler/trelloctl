---
id: M00
title: Bootstrap y arnés de pruebas
status: in_review
wave: 0
depends_on: []
branch: feature/m00-bootstrap
tags: [plan, milestone, trelloctl]
---

# M00 — Bootstrap y arnés de pruebas

**Estado:** 🔵 En revisión · **Ola:** 0 · **Depende de:** — · **Rama:** `feature/m00-bootstrap`

## Objetivo

Dejar el repositorio listo para que cualquier milestone posterior se ejecute en un subagente aislado: empaquetado, herramientas de calidad, CI en verde y el arnés de pruebas común (Trello falso, aislamiento, guardia de red y comprobador de trazabilidad).

## Lectura obligatoria

- `CLAUDE.md` del repositorio.
- PRD §10.1 (decisiones de tecnología), §10.4 (estructura del paquete), RNF-1, RNF-8, RNF-9.
- [[01-estrategia-de-pruebas]] completo.

## Alcance

- `pyproject.toml` PEP 621 con backend `hatchling`: metadatos, licencia MIT, `requires-python >= 3.12`, sin dependencias de runtime, extra `mcp` (el rango de versión lo fija M15), grupo de desarrollo (`pytest`, `pytest-cov`, `ruff`, `mypy` y `hatchling`, para construir el wheel en las pruebas sin aislamiento y sin red), punto de entrada `trelloctl`, inclusión de `skills/` como datos del paquete.
- `uv.lock` versionado.
- Esqueleto mínimo del paquete según PRD §10.4: `__init__.py` con la versión, `__main__.py`, un `cli.py` mínimo que solo responde a `--version` y devuelve salida 2 ante cualquier otro argumento (lo sustituye M06), y los paquetes `app/`, `domain/` e `infra/` vacíos. Cada módulo lo crea el milestone que lo implementa.
- Configuración de `ruff` (lint y formato), `mypy` en modo estricto sobre `src/` y `tools/`, `pytest` (marcador `tc` registrado, rutas de pruebas) y `coverage` (rama activada, umbral 90 %).
- Estructura de `tests/`: `unit/`, `acceptance/`, `fakes/`, `fixtures/`, `conftest.py` raíz.
- Fixtures comunes: HOME temporal vía `TRELLOCTL_HOME`, entorno sin variables `TRELLO_*` reales, directorio de trabajo temporal, reloj inyectable.
- Guardia de red automática: cualquier conexión a un host distinto de loopback hace fallar la prueba.
- Núcleo del Trello falso (`tests/fakes/trello/`): servidor HTTP de la biblioteca estándar en `127.0.0.1` con puerto efímero, enrutador por método y ruta, almacén de estado en memoria, validación de key y token, registro de peticiones e inyección de fallos. Las rutas de cada recurso las añade el milestone que las necesita, cada uno en su propio módulo; el núcleo descubre esos módulos automáticamente, de modo que añadir uno no edita ningún archivo común.
- `tools/check_traceability.py` (herramienta de desarrollo, solo biblioteca estándar) con las reglas de [[01-estrategia-de-pruebas#Trazabilidad]].
- El workflow `.github/workflows/ci.yml` ya existe en el repositorio y, sin `pyproject.toml`, solo ejecuta el job `detect`. Al añadir el paquete se activan `checks`, `tests` y `package`; este milestone los deja en verde. La comprobación de A-1 del job `package` pasa de forma trivial hasta M15.

## Fuera de alcance

Cualquier lógica de producto. Las rutas concretas de Trello (las añade cada milestone).

## Archivos

- **Crea:** `pyproject.toml`, `uv.lock`, `src/trelloctl/**` (esqueleto), `tests/conftest.py`, `tests/fakes/trello/` (núcleo), `tools/check_traceability.py`, `tests/unit/tools/`, `tests/acceptance/test_m00_harness.py`.
- **No toca:** `docs/PRD.md`.

## Fases

| Fase | Descripción                                                          | Estado |
| ---- | -------------------------------------------------------------------- | ------ |
| F1   | Implementación (empaquetado, esqueleto, arnés, comprobador)          | 🟢      |
| F2   | Pruebas unitarias del arnés, del comprobador y del esqueleto (`cli.main()` y `__main__` en proceso, este con `runpy`) para alcanzar el 90 % sobre `trelloctl` | 🟢 |
| F3   | TCs automatizados de este milestone                                  | 🟢      |
| F4   | Verificación local: lint, tipos, cobertura ≥ 90 %, trazabilidad      | 🟢      |
| F5   | PR a `develop`, CI verde en los 3 SO, merge `--no-ff`                | ⚪ (local only: PR and CI pending user confirmation)      |

## Casos de prueba automatizados

Nivel: C componente · E CLI en proceso · S CLI en subproceso · M MCP por stdio. Tipo: P positivo · N negativo · B borde.

| TC         | AC    | Tipo | Nivel | Escenario                                                                                  | Resultado esperado                                                                 |
| ---------- | ----- | ---- | ----- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| TC-M00-001 | infra | P | C | Arrancar el Trello falso y pedir una ruta registrada | Responde en `127.0.0.1` con puerto efímero; se detiene sin dejar hilos vivos |
| TC-M00-002 | infra | N | C | Pedir una ruta no registrada | 404 con cuerpo de texto, como Trello |
| TC-M00-003 | infra | N | C | Petición sin key o con token distinto del configurado | 401 con cuerpo `invalid key` o `invalid token` |
| TC-M00-004 | infra | P | C | Programar un fallo (429 con cabeceras, 5xx, 401, retardo) para una ruta | Se aplica a la siguiente petición coincidente y solo a esa |
| TC-M00-005 | infra | P | C | Consultar el registro de peticiones tras varias llamadas | Expone método, ruta, query, cabeceras y cuerpo en orden |
| TC-M00-006 | infra | N | C | Una prueba intenta conectar con un host externo | La guardia de red hace fallar la prueba con un mensaje que nombra el host |
| TC-M00-007 | infra | P | C | Dos pruebas consecutivas leen `TRELLOCTL_HOME` y `TRELLO_*` | Cada una ve un HOME temporal distinto y ninguna variable real del entorno |
| TC-M00-008 | infra | P | C | Comprobador sobre documentos de ejemplo con un AC sin TC | Falla nombrando el AC |
| TC-M00-009 | infra | N | C | Comprobador con un milestone `done` cuyo TC no tiene prueba marcada | Falla nombrando el TC |
| TC-M00-010 | infra | N | C | Comprobador con una prueba marcada con un TC inexistente | Falla por TC huérfano |
| TC-M00-011 | infra | B | C | PRD con el guion no separable (U+2011) en los ids de AC | Se normaliza y los ids casan con los de los milestones |
| TC-M00-012 | infra | B | C | Estado de semáforo distinto entre plan maestro y milestone | Aviso, no fallo; fallo solo si un milestone `done` tiene dependencias no `done` |
| TC-M00-013 | infra | P | S | `python -m trelloctl` con el esqueleto instalado | El módulo se importa y delega en `cli` sin error de importación |

## Definición de terminado

La común del [[00-plan-maestro#Definición de terminado común]], más: CI en verde en los 9 jobs de test y en `checks` y `package`.

## Bitácora

| Fecha | Evento |
| ----- | ------ |
| 2026-11-09 | F1-F4 completadas en local: empaquetado, esqueleto, Trello falso (núcleo), guardia de red, aislamiento, reloj, comprobador de trazabilidad; 13/13 TCs automatizados y verificados uno a uno; cobertura 100 % sobre `trelloctl` |
| 2026-11-09 | Las seis comprobaciones locales en verde (sync, ruff check, ruff format, mypy, pytest con cobertura, trazabilidad). F5 pendiente: solo local, PR y CI a la espera de confirmación del usuario |
