---
title: trelloctl — Estrategia de pruebas
version: 1.0.0
date: 2026-11-09
tags: [plan, pruebas, trelloctl]
---

# trelloctl — Estrategia de pruebas

Reglas comunes a todos los milestones del [[00-plan-maestro]]. Ninguna prueba automatizada habla con Trello real: el plan gratuito y sus límites de tasa lo impiden, y una suite dependiente de la red sería inestable. Lo que solo puede comprobarse contra Trello está en [[tc-manuales-trello]].

## Objetivos

- Un TC automatizado como mínimo por cada AC y RNF del PRD, más los casos negativos de cada fallo previsto y los casos de borde relevantes. Total planificado: 423 TCs (246 positivos, 128 negativos, 49 de borde).
- Cobertura mínima del 90 % de líneas y ramas, medida en cada sistema operativo y versión de Python de la matriz.
- Suite determinista, sin red externa, ejecutable en Windows, Linux y macOS.

## Niveles

| Nivel                  | Qué prueba                                                                 | Cómo                                                                                         | Dónde                     |
| ---------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------- |
| UT (prueba unitaria)   | Una función o clase aislada                                                | Dependencias sustituidas por dobles; el cliente HTTP con el stub de transporte                | `tests/unit/`             |
| TC nivel C (componente) | Un componente frente a un AC, sin pasar por el CLI                        | Componente real contra el Trello falso o el almacén real en un HOME temporal                  | `tests/acceptance/`       |
| TC nivel E (extremo a extremo en proceso) | Un comando completo                                   | Punto de entrada del CLI invocado en el proceso de pytest con argv, stdin, stdout y stderr capturados, contra el Trello falso | `tests/acceptance/` |
| TC nivel S (subproceso) | Lo que solo se observa en un proceso real: stdout puro, códigos de salida, arranque, empaquetado | `python -m trelloctl` en un subproceso con el HOME y el Trello falso de la prueba | `tests/acceptance/`       |
| TC nivel M (MCP)       | El servidor MCP                                                            | `trelloctl mcp serve` en un subproceso, con un cliente MCP de pruebas por stdio              | `tests/acceptance/`       |

Los UT cubren la lógica interna y buscan la cobertura; los TCs verifican los criterios de aceptación y son los que se trazan. Los niveles S y M no suman cobertura en el proceso de pytest, por lo que la cobertura se alcanza con UT y TCs de niveles C y E.

## Dobles de Trello

### Stub de transporte (UT)

Sustituye la capa de transporte del cliente HTTP (M04). Devuelve respuestas programadas (estado, cabeceras, cuerpo) o lanza errores de red, DNS, TLS o tiempo agotado. Permite probar la traducción de errores y el enmascarado sin sockets.

### Trello falso (TCs)

Servidor HTTP de la biblioteca estándar que escucha en `127.0.0.1` en un puerto efímero por prueba. La configuración de la prueba apunta `http.base_url` a él, como permite AC‑23.6.

| Capacidad                    | Detalle                                                                                                                                  | Milestone       |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------- |
| Núcleo                       | Enrutador por método y ruta, estado en memoria (tableros, listas, etiquetas, miembros, tarjetas, checklists, adjuntos, acciones), datos semilla por prueba | M00 |
| Autenticación                | Key y token por query; 401 `invalid key` / `invalid token`; descargas solo con cabecera OAuth                                            | M00, M04        |
| Registro de peticiones       | Método, ruta, query, cabeceras y cuerpo en orden, para asertar número y forma de las llamadas                                           | M00             |
| Inyección de fallos          | 429 con cada cuerpo de límite y sus cabeceras `x-rate-limit-*`, 4xx, 5xx, 401, retardo para provocar `TIMEOUT`, cuerpo no JSON; puerto cerrado para `NETWORK_ERROR` | M00 |
| Lecturas de tablero y tarjetas | `members/me`, `members/me/boards`, `boards/{id}` con anidados, `cards/open|closed|all` con `before`, `limit` y `modifiedSince`       | M04, M07        |
| Escrituras                   | Listas, etiquetas, tarjetas (incluida la copia con `keepFromSource`), comentarios con autoría por token, `idLabels`, `idMembers`, checklists e ítems | M08 a M10 |
| Búsqueda y actividad         | `GET /search` con `partial` y `comment:`; `boards/{id}/actions` con filtros y paginación                                                | M08, M09        |
| Adjuntos                     | Listado, alta multipart o enlace, descarga autenticada                                                                                   | M04, M11        |
| Inbox                        | `members/me?fields=inbox`, tablero de tipo `inbox`, traslado con `idBoard`                                                               | M12             |
| Especificación OpenAPI       | Especificación reducida de fixture, con variantes sin endpoint y con parámetro obsoleto                                                  | M14             |

El comportamiento del Trello falso se basa en la documentación pública del API y en las respuestas documentadas en el PRD. Cuando un TC manual contra Trello real revele una diferencia, se corrige primero el Trello falso y después el código.

## Aislamiento

- Cada prueba usa un `TRELLOCTL_HOME` temporal, un directorio de trabajo temporal y un HOME de usuario temporal (para `skills install`).
- Las variables `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_BOARD` y `TRELLOCTL_HOME` del entorno real se eliminan antes de cada prueba.
- Una guardia de red automática hace fallar cualquier prueba que intente conectar con un host distinto de loopback.
- Los TTL se prueban con un reloj inyectable, nunca con esperas reales, salvo en el nivel M, donde se configura un TTL de un segundo.
- Las pruebas específicas de una plataforma (permisos POSIX) se omiten de forma explícita en las demás.

## Trazabilidad

- Cada TC tiene un id único `TC-Mxx-nnn` en la tabla de su milestone, con los AC o RNF que cubre y, si su resultado esperado depende de una aclaración del plan maestro, su id (`A-n`). El comprobador acepta esas referencias y verifica que la aclaración existe.
- Cada prueba que automatiza un TC lleva el marcador `tc` con ese id; una prueba puede cubrir varios TCs y un TC puede repartirse en varias pruebas parametrizadas.
- `tools/check_traceability.py` (M00) comprueba en CI:
  1. Todo AC y RNF del PRD aparece en al menos un TC de algún milestone (normalizando el guion no separable U+2011 que usa el PRD).
  2. Todo TC de un milestone con `status: done` tiene al menos una prueba marcada.
  3. Ningún marcador `tc` apunta a un TC inexistente.
  4. Ningún milestone `done` tiene dependencias que no estén `done`.
  5. Las discrepancias de semáforo entre el plan maestro y los milestones se notifican como aviso, no como fallo, porque el orquestador actualiza el plan maestro después de cada merge.
- En M16 el comprobador se ejecuta en modo estricto: todos los TCs de todos los milestones con prueba.

## Cobertura

- Medida con `pytest-cov`, de líneas y de ramas, sobre el paquete `trelloctl`.
- Umbral del 90 % aplicado a la suite completa en cada job de la matriz; el job falla por debajo.
- Exclusiones admitidas: bloques solo para el verificador de tipos y la guarda de ejecución como script. No se usan exclusiones para esquivar el umbral.
- El código específico de una plataforma es mínimo por diseño (AC‑27.1), por lo que el umbral se cumple en los tres sistemas operativos sin combinar informes.

## Comprobaciones de idioma (RNF‑10)

Una heurística común, usada en M13 y M16, rechaza en los textos emitidos o instalados los caracteres propios del español (á, é, í, ó, ú, ñ, ¿, ¡) y una lista corta de palabras funcionales españolas. Los datos de prueba con caracteres no ASCII se excluyen porque son contenido del usuario, no texto de la herramienta.

## Datos de prueba

- Datos semilla sintéticos por prueba, construidos con funciones de fábrica; ningún dato real de tableros ni credenciales.
- Respuestas JSON de referencia en `tests/fixtures/`, con la forma documentada por Trello.
- Credenciales de prueba fijas y evidentemente falsas, definidas en los fixtures.

## Integración continua

Workflow `.github/workflows/ci.yml`, ajustado al plan gratuito de GitHub Actions.

| Job                | Cuándo                                 | Dónde                               | Qué hace                                                                                 |
| ------------------ | -------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------- |
| `detect`           | Siempre                                | Ubuntu                              | Detecta si existe `pyproject.toml`; sin él, los demás jobs se omiten (estado previo a M00) |
| `checks`           | Si existe el paquete                   | Ubuntu, Python 3.12                 | Lint, formato, tipado estricto y comprobador de trazabilidad                             |
| `tests`            | Tras `checks`                          | PR y `main`: 3 SO × Python 3.12, 3.13, 3.14 (9 jobs). Push a `develop`: Ubuntu, Python 3.12 | UT y TCs automatizados contra el Trello falso, con el umbral de cobertura |
| `package`          | Tras `checks`                          | Ubuntu, Python 3.12                 | Construye el wheel, lo instala sin extras en un entorno limpio y verifica RNF‑1 y A‑1    |

Medidas de ahorro de minutos:

- `tests` y `package` dependen de `checks`: si el lint falla no se gastan los 9 jobs.
- `concurrency` cancela la ejecución anterior del mismo PR o rama al llegar un push nuevo.
- La matriz completa solo corre en los PR (donde decide el merge) y en `main`; el push a `develop` tras un merge, cuyo contenido ya pasó la matriz en el PR, corre un único job de humo.
- `fail-fast` cancela el resto de la matriz al primer fallo.
- Caché de `uv` activada y `timeout-minutes` en cada job.
- No hay disparadores por push a ramas `feature/*`: el PR ya las cubre.

El repositorio es público y en ese caso los minutos de los runners estándar no se descuentan. Si pasara a privado, los 2000 minutos mensuales del plan gratuito se consumen con multiplicador 2 en Windows y 10 en macOS; la matriz completa de un PR rondaría entonces los 100 a 150 minutos facturables, y convendría reducir macOS a una sola versión de Python.
