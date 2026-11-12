---
title: trelloctl — Walkthrough de la implementación
status: done
version: 1.0.0
date: 2026-11-09
source: docs/plan/00-plan-maestro.md
tags: [plan, trelloctl, walkthrough]
---

# trelloctl — Walkthrough de la implementación

Resumen de cómo se implementó el [[00-plan-maestro|plan maestro]] sobre el [[PRD]] (v1.0.0, conciliado después en la v1.1.0): qué se construyó, cómo se verificó, qué se desvió del plan y qué queda pendiente. Es la salida del plan maestro. No sustituye a las bitácoras de cada milestone, que guardan el detalle fase por fase.

## 1. Resumen ejecutivo

- Se implementaron los 17 milestones (M00 a M16): una CLI en Python que opera tableros de Trello con una sola salida JSON por invocación, caché local en SQLite, resolución de nombres a ids, los comandos de tablero, tarjetas, checklists, adjuntos e Inbox, `doctor`, el skill para el harness y un servidor MCP opcional por stdio. En tiempo de ejecución solo usa la biblioteca estándar; el SDK de MCP es el único extra (`trelloctl[mcp]`).
- Los 451 TCs automatizados del plan tienen prueba marcada con `@pytest.mark.tc(...)`, y el comprobador de trazabilidad confirma que los 137 AC y los 10 RNF del PRD están cubiertos. Solo RNF-2 (arranque en frío) se mide a mano, por la aclaración A-18.
- Suite completa al cierre: 1124 pruebas pasan y 1 se omite en Windows (permisos POSIX), con una cobertura de líneas y ramas del 98,61 % (umbral: 90 %).
- Cada milestone entró en `develop` mediante un PR con merge commit, con CI en verde en Windows, Linux y macOS con Python 3.12, 3.13 y 3.14. Los PR son #1 a #17 y el de cierre (M16). El #10 es el refactor `touch_cards` de la caché, que no es un milestone.
- Ningún milestone quedó bloqueado. Hubo tres errores de CI reales y varios problemas de proceso; se resumen en la sección 6.

### Estado por milestone

| Milestone | Ola | TCs | PR | Pruebas en su rama | Cobertura | Estado |
| --------- | --- | --- | -- | ------------------ | --------- | ------ |
| M00 Bootstrap y arnés de pruebas | 0 | 13 | #1 | 28 | 100 % | 🟢 |
| M01 Fundamentos | 1 | 19 | #2 | 65 | 99,61 % | 🟢 |
| M02 Dominio | 2 | 22 | #3 | 107 | 99,82 % | 🟢 |
| M03 Configuración | 2 | 21 | #4 | 110 | 98,92 % | 🟢 |
| M04 Cliente HTTP | 2 | 21 | #5 | 102 | 99,36 % | 🟢 |
| M05 Caché SQLite | 3 | 17 | #6 | 217 | 98,76 % | 🟢 |
| M06 Registro, CLI y config | 3 | 33 | #7 | 262 | 99,26 % | 🟢 |
| M07 Sincronización y resolución | 4 | 51 | #8 | 412 | 98,63 % | 🟢 |
| M13 Skill | 4 | 15 | #9 | 309 | 98,85 % | 🟢 |
| M08 Tablero | 5 | 41 | #11 | 515 | 98,77 % | 🟢 |
| M11 Adjuntos | 5 | 21 | #12 | 562 | 98,80 % | 🟢 |
| M09 Tarjetas | 5 | 61 | #13 | 755 | 99,00 % | 🟢 |
| M12 Inbox | 6 | 19 | #14 | 806 | 98,97 % | 🟢 |
| M10 Detalle de tarjeta | 6 | 39 | #15 | 869 | 99,00 % | 🟢 |
| M15 Servidor MCP | 7 | 19 | #16 | 906 | 98,46 % | 🟢 |
| M14 Doctor | 7 | 25 | #17 | 960 | 98,55 % | 🟢 |
| M16 Cierre | 8 | 14 | este PR | 1124 | 98,61 % | 🟢 |

Las pruebas y la cobertura son las medidas en la rama de cada milestone antes de abrir su PR, cada una con todo lo fusionado hasta entonces.

## 2. Matriz de criterios de aceptación

Cada fila lista el milestone y los números de los TCs (`TC-Mxx-nnn`) que verifican el criterio. Todos los TCs automatizados están en verde en CI. TC-MAN-11 (RNF-2) y los otros diez casos de [[tc-manuales-trello]] se ejecutan contra Trello real y no están ejecutados todavía.

| Criterio | Milestone y TCs (números de `TC-Mxx-nnn`) | Resultado |
| -------- | ------------------------------------------ | --------- |
| AC-1.1 | M03: 001, 002 | 🟢 |
| AC-1.2 | M03: 003; M06: 014, 015, 029; M07: 023, 040; M11: 018; M13: 015; M14: 008; M15: 018; M16: 014 | 🟢 |
| AC-1.3 | M06: 010, 011, 012 | 🟢 |
| AC-1.4 | M03: 004; M06: 028 | 🟢 |
| AC-1.5 | M03: 005, 006, 007; M04: 011, 012; M07: 024 | 🟢 |
| AC-1.6 | M03: 008, 009; M06: 016, 017, 018, 019, 020 | 🟢 |
| AC-1.7 | M03: 010 | 🟢 |
| AC-1.8 | M03: 011, 012, 013; M07: 021 | 🟢 |
| AC-1.9 | M02: 017; M07: 017, 018, 019, 043, 044, 045, 046, 047, 049 | 🟢 |
| AC-1.10 | M03: 014, 015, 016, 017; M06: 021, 022, 032; M07: 022, 048 | 🟢 |
| AC-1.11 | M03: 018, 019 | 🟢 |
| AC-1.12 | M03: 020, 021; M06: 010 | 🟢 |
| AC-2.1 | M08: 009 | 🟢 |
| AC-2.2 | M01: 001, 002; M11: 020 | 🟢 |
| AC-2.3 | M01: 003, 004; M06: 027 | 🟢 |
| AC-2.4 | M06: 013 | 🟢 |
| AC-3.1 | M06: 001, 006; M14: 001 | 🟢 |
| AC-3.2 | M01: 017; M06: 025 | 🟢 |
| AC-3.3 | M01: 005, 006, 007; M06: 009, 031; M15: 005 | 🟢 |
| AC-3.4 | M01: 008; M06: 007, 008 | 🟢 |
| AC-4.1 | M06: 002, 033 | 🟢 |
| AC-4.2 | M06: 003 | 🟢 |
| AC-4.3 | M06: 004 | 🟢 |
| AC-5.1 | M14: 001, 002 | 🟢 |
| AC-5.2 | M05: 016; M14: 003, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 025 | 🟢 |
| AC-5.3 | M14: 004 | 🟢 |
| AC-5.4 | M14: 005, 006 | 🟢 |
| AC-5.5 | M14: 019, 020, 021, 022 | 🟢 |
| AC-5.6 | M14: 023 | 🟢 |
| AC-6.1 | M06: 005 | 🟢 |
| AC-7.1 | M07: 017 | 🟢 |
| AC-7.2 | M07: 025, 026, 027; M10: 010, 038 | 🟢 |
| AC-7.3 | M02: 015, 016, 021; M09: 016, 017, 061; M12: 015 | 🟢 |
| AC-7.4 | M07: 019, 028, 029, 046; M09: 020; M10: 036 | 🟢 |
| AC-7.5 | M07: 020, 030; M08: 038; M10: 016, 020, 037 | 🟢 |
| AC-8.1 | M08: 001, 002 | 🟢 |
| AC-8.2 | M08: 003, 004 | 🟢 |
| AC-8.3 | M08: 005 | 🟢 |
| AC-8.4 | M08: 006, 007 | 🟢 |
| AC-9.1 | M02: 011; M08: 010, 011 | 🟢 |
| AC-9.2 | M08: 012 | 🟢 |
| AC-9.3 | M08: 013, 039 | 🟢 |
| AC-9.4 | M02: 009, 010, 022; M08: 015, 016, 017 | 🟢 |
| AC-9.5 | M08: 020, 021, 022; M09: 049 | 🟢 |
| AC-10.1 | M08: 023, 024 | 🟢 |
| AC-10.2 | M08: 025 | 🟢 |
| AC-10.3 | M08: 026 | 🟢 |
| AC-11.1 | M08: 028, 029, 030, 031 | 🟢 |
| AC-11.2 | M08: 032 | 🟢 |
| AC-11.3 | M08: 033, 034 | 🟢 |
| AC-11.4 | M08: 035 | 🟢 |
| AC-12.1 | M09: 001, 010 | 🟢 |
| AC-12.2 | M02: 011, 012, 013, 014; M09: 002, 003, 011 | 🟢 |
| AC-12.3 | M09: 004, 005, 056; M12: 010 | 🟢 |
| AC-12.4 | M09: 006, 007, 008, 056; M10: 004, 005, 025; M15: 019 | 🟢 |
| AC-12.5 | M09: 009 | 🟢 |
| AC-13.1 | M02: 018, 019; M09: 012, 013, 014, 015 | 🟢 |
| AC-13.2 | M09: 021, 022, 023 | 🟢 |
| AC-13.3 | M05: 017; M09: 024, 025, 026, 027, 028, 029, 030, 031, 057 | 🟢 |
| AC-13.4 | M08: 028, 029, 036, 037; M09: 032, 033; M10: 001; M12: 005 | 🟢 |
| AC-13.5 | M09: 034, 035, 058 | 🟢 |
| AC-13.6 | M08: 008; M09: 036 | 🟢 |
| AC-14.1 | M09: 037, 060 | 🟢 |
| AC-14.2 | M02: 001, 002, 003, 004, 005, 022; M09: 038, 039, 060 | 🟢 |
| AC-14.3 | M02: 006, 007, 008; M08: 017, 041; M09: 040 | 🟢 |
| AC-14.4 | M08: 018; M09: 041 | 🟢 |
| AC-14.5 | M08: 019; M09: 042 | 🟢 |
| AC-15.1 | M09: 043 | 🟢 |
| AC-15.2 | M09: 044, 045 | 🟢 |
| AC-15.3 | M09: 046 | 🟢 |
| AC-16.1 | M09: 047, 048, 050, 056 | 🟢 |
| AC-16.2 | M09: 051 | 🟢 |
| AC-16.3 | M09: 049 | 🟢 |
| AC-16.4 | M08: 027; M09: 053; M10: 009, 027, 034; M15: 007 | 🟢 |
| AC-17.1 | M10: 001, 002 | 🟢 |
| AC-17.2 | M10: 003, 004, 039 | 🟢 |
| AC-17.3 | M10: 006, 007, 008, 039 | 🟢 |
| AC-17.4 | M10: 011 | 🟢 |
| AC-18.1 | M10: 012, 013, 014, 016 | 🟢 |
| AC-18.2 | M10: 015 | 🟢 |
| AC-19.1 | M10: 017, 018, 020 | 🟢 |
| AC-19.2 | M10: 019 | 🟢 |
| AC-20.1 | M10: 021 | 🟢 |
| AC-20.2 | M10: 022, 023, 024 | 🟢 |
| AC-20.3 | M10: 026 | 🟢 |
| AC-20.4 | M10: 028, 029 | 🟢 |
| AC-20.5 | M10: 030, 031, 032 | 🟢 |
| AC-20.6 | M10: 033 | 🟢 |
| AC-20.7 | M10: 035 | 🟢 |
| AC-21.1 | M11: 001 | 🟢 |
| AC-21.2 | M04: 016, 017; M11: 002, 003, 009, 010, 021 | 🟢 |
| AC-21.3 | M11: 004, 005, 006 | 🟢 |
| AC-21.4 | M11: 007, 008 | 🟢 |
| AC-21.5 | M04: 019; M11: 011, 012, 013, 014, 015 | 🟢 |
| AC-21.6 | M11: 016, 017, 018 | 🟢 |
| AC-22.1 | M05: 001, 002, 010 | 🟢 |
| AC-22.2 | M05: 003, 004 | 🟢 |
| AC-22.3 | M05: 005, 006, 007 | 🟢 |
| AC-22.4 | M07: 004, 005, 010 | 🟢 |
| AC-22.5 | M07: 008, 009, 042 | 🟢 |
| AC-22.6 | M05: 009; M07: 001, 002, 003, 004, 005, 006, 007 | 🟢 |
| AC-22.7 | M05: 008; M07: 011; M08: 020 | 🟢 |
| AC-22.8 | M05: 011; M07: 016, 041; M15: 012 | 🟢 |
| AC-22.9 | M07: 032, 033; M09: 018, 019 | 🟢 |
| AC-22.10 | M07: 012, 013, 014, 015; M08: 040 | 🟢 |
| AC-22.11 | M07: 038; M08: 015; M09: 038, 055 | 🟢 |
| AC-22.12 | M07: 020, 030, 031 | 🟢 |
| AC-22.13 | M07: 034, 035, 036, 050; M09: 054 | 🟢 |
| AC-22.14 | M05: 012, 013; M07: 037 | 🟢 |
| AC-22.15 | M05: 014, 015; M07: 039, 040; M11: 017, 019; M12: 002 | 🟢 |
| AC-23.1 | M04: 002 | 🟢 |
| AC-23.2 | M04: 003, 004, 005, 006 | 🟢 |
| AC-23.3 | M04: 002, 007, 008, 010, 020 | 🟢 |
| AC-23.4 | M04: 009 | 🟢 |
| AC-23.5 | M01: 016; M04: 013, 014 | 🟢 |
| AC-23.6 | M04: 001 | 🟢 |
| AC-24.1 | M01: 013; M06: 023 | 🟢 |
| AC-24.2 | M01: 014, 015 | 🟢 |
| AC-24.3 | M01: 016; M04: 015; M06: 024 | 🟢 |
| AC-25.1 | M15: 001, 002, 003, 004, 017 | 🟢 |
| AC-25.2 | M15: 005, 006, 007, 018 | 🟢 |
| AC-25.3 | M15: 008 | 🟢 |
| AC-25.4 | M15: 013, 014, 015 | 🟢 |
| AC-25.5 | M15: 009, 010, 011 | 🟢 |
| AC-26.1 | M13: 001, 002, 003 | 🟢 |
| AC-26.2 | M13: 004, 005, 006, 007, 008, 009 | 🟢 |
| AC-26.3 | M13: 010 | 🟢 |
| AC-26.4 | M13: 011, 012; M16: 013 | 🟢 |
| AC-27.1 | M01: 018; M16: 001 | 🟢 |
| AC-27.2 | M01: 019; M16: 002 | 🟢 |
| AC-28.1 | M12: 001, 002, 003, 004 | 🟢 |
| AC-28.2 | M12: 005, 006, 007 | 🟢 |
| AC-28.3 | M12: 007, 008, 009, 010, 019 | 🟢 |
| AC-28.4 | M12: 011, 012, 013, 014, 015, 016, 017 | 🟢 |
| AC-28.5 | M14: 018 | 🟢 |
| AC-28.6 | M06: 010; M13: 013 | 🟢 |
| AC-28.7 | M07: 051; M09: 059; M12: 018 | 🟢 |
| RNF-1 | M16: 003 | 🟢 |
| RNF-2 | TC-MAN-11 (medición manual, aclaración A-18) | ⚪ pendiente del responsable |
| RNF-3 | M04: 021; M06: 026; M16: 011 | 🟢 |
| RNF-4 | M01: 008; M16: 004 | 🟢 |
| RNF-5 | M01: 009, 010; M16: 005 | 🟢 |
| RNF-6 | M08: 014; M09: 052; M10: 012; M16: 006 | 🟢 |
| RNF-7 | M01: 011, 012; M16: 007 | 🟢 |
| RNF-8 | M04: 018; M14: 024; M16: 008 | 🟢 |
| RNF-9 | M16: 009 | 🟢 |
| RNF-10 | M13: 014; M16: 010 | 🟢 |

## 3. Arquitectura tal como quedó

Las capas solo dependen hacia abajo (PRD §10.3):

| Capa | Módulos | Papel |
| ---- | ------- | ----- |
| Interfaces | `cli.py`, `mcp_server.py` | Traducen la entrada, llaman a `app/commands` y serializan con `output.py`; sin lógica de negocio |
| Aplicación | `app/commands/*` (board, lists, labels, activity, card, checklists, attachments, inbox, config, cache, skills, version, context), `app/resolver.py`, `app/search.py`, `doctor.py` | Un caso de uso por operación; devuelven el envelope como dato y nunca imprimen |
| Dominio | `domain/models.py`, `domain/mover.py`, `domain/errors.py` | Puro, sin E/S ni importaciones de `infra` o `app` |
| Infraestructura | `infra/settings.py`, `infra/trello_client.py`, `infra/cache.py`, `infra/logging.py`, `infra/paths.py` | Configuración, cliente HTTP, caché SQLite, logs y rutas |

Decisiones de diseño que conviene conocer:

- **`registry.py` es la fuente única de operaciones.** Cada operación se declara una vez y de ahí salen los analizadores de argparse de la CLI y la lista de herramientas MCP. El archivo se organiza en un bloque delimitado por grupo de comandos, de modo que los milestones paralelos no colisionaron al fusionar.
- **Resolución de nombres a ids en un solo lugar.** `app/resolver.py` reutiliza la caché, recarga cuando el dato venció o la referencia está rancia, y localiza tarjetas sin tablero seleccionado (aclaración A-15). Los comandos no repiten esa lógica.
- **Caché SQLite con TTL y escrituras transaccionales,** con un respaldo en memoria para las pruebas. Un dato vence cuando su edad es mayor o igual que su TTL (A-8). Los tests usan un reloj inyectable, nunca esperas reales.
- **Un único envelope JSON por invocación** y logs solo en stderr y en el archivo de log; en modo MCP, stdout lleva solo mensajes del protocolo. Las credenciales salen de `config.toml` o del entorno, nunca de opciones, y no aparecen sin enmascarar.
- **Ninguna llamada reintenta contra la API** (AC-23.1): una petición, un resultado.
- **Trello falso por recurso.** Las pruebas de aceptación usan un servidor HTTP de la biblioteca estándar en loopback, con un módulo por recurso en `tests/fakes/trello/`; una guardia de red hace fallar cualquier prueba que intente salir de loopback.

## 4. Aclaraciones del plan y conciliación con el PRD

Las aclaraciones A-1 a A-25 del plan maestro fijaron los comportamientos que el PRD no define. Esta tabla indica qué TCs verifican cada una; así quedaron resueltas en el código.

| Aclaración | Milestone y TCs que la verifican |
| ---------- | -------------------------------- |
| A-1 | M15: 013 |
| A-2 | M14: 006 |
| A-3 | M02: 012, 013; M08: 011; M09: 003; M10: 029; M12: 017 |
| A-4 | M07: 040; M11: 018 |
| A-5 | M08: 037; M09: 030, 031 |
| A-6 | M06: 011; M13: 006 |
| A-7 | M06: 029 |
| A-8 | M05: 007 |
| A-9 | M11: 010 |
| A-10 | M07: 014 |
| A-11 | M09: 008; M10: 005; M11: 015 |
| A-12 | M14: 013, 016, 017 |
| A-13 | M09: 056; M10: 039; M12: 019 |
| A-14 | M02: 015, 017, 021; M07: 044, 049; M09: 061 |
| A-15 | M07: 051; M09: 059 |
| A-16 | M03: 019; M06: 018, 020, 031 |
| A-17 | M05: 013; M07: 037 |
| A-19 | M14: 011, 025 |
| A-20 | M09: 057 |
| A-21 | M02: 022; M09: 060 |
| A-22 | M02: 008; M03: 006, 009, 015; M04: 006; M07: 050; M08: 041; M10: 016, 024 |
| A-23 | M06: 010, 012, 032 |
| A-24 | M06: 033; M07: 045, 046, 047, 048; M15: 017 |
| A-25 | M15: 018, 019 |

El PRD v1.1.0 incorpora al texto A-23 (`config init` escribe la configuración mínima), A-14 (un valor de 8 alfanuméricos se busca primero por nombre), A-15 (tarjetas por id o URL sin tablero seleccionado), A-17 (tarjetas de una lista desaparecida), A-19 (comprobaciones de red de `doctor` contra `http.base_url`), A-20 (clases `[...]` en la búsqueda local) y A-24 (`--include-archived-boards`); sección «Conciliación con el PRD» del plan maestro. Además decide no deprecar el shortLink y no purgar la caché de un tablero desaparecido (decisiones 15 y 16, §12 del PRD). Los identificadores de requisitos no cambian.

## 5. Desviaciones respecto del plan

1. **M00.** Se eliminó una prueba del `cli.py` mínimo en `tests/unit/test_skeleton.py`, obsoleta al sustituirlo M06.
2. **M06.** Se añadió `app/commands/context.py` (ayudante común de contexto), que no figura en la lista de archivos del milestone ni en PRD §10.4; está dentro de `app/commands/`.
3. **M07.** Cambió una aserción de `test_m06_cli.py`: `config board set not-an-id` pasa de `USAGE` a `BOARD_NOT_FOUND`, porque todo valor que no es un id se resuelve como nombre (AC-1.10). El resolver usaba `Cache._tx()` por no existir una operación pública; se corrigió con `Cache.touch_cards` en un PR previo y pequeño (#10), como indica el plan para `infra/`.
4. **M08 y M09.** Ampliaron `tests/fakes/trello/core.py` (M00): una ruta registrada por una prueba sustituye a la del módulo del recurso, para que las pruebas de M07 pudieran simular referencias rancias.
5. **M09.** `--left` y `--right` exigen el valor N en la CLI, porque el registro no admite opciones con valor opcional.
6. **M16.** Al verificar RNF-6, `card archive|done` y `board list archive` repetidos reenviaban el mismo PUT. Se corrigió en este PR (no se escribe si el estado ya coincide) y TC-M16-006 exige cero escrituras en todas las operaciones repetidas. Además, el mensaje `USAGE` de argparse repetía el valor tras `--token`; `cli.py` lo sustituye por `***` y TC-M16-011 comprueba que no aparece en stdout, stderr ni log. Ambas correcciones tocan `card.py`, `lists.py` y `cli.py`, fuera del alcance declarado de M16.
7. **Estado del plan.** Los cambios de estado de M00 a M15 (F5 y `done`) viajan en el PR de M14 y los de M14 y M16 en este PR, en lugar de commits directos a `develop`, a petición del responsable.
8. **README.** Está en español, única excepción fuera de `docs/`, y queda fuera de la comprobación de idioma de RNF-10 (decisión del responsable; `CLAUDE.md`, M15 y M16 reflejan el cambio).

## 6. Errores de CI y lecciones aprendidas

Los PR #1 a #9 fallaron en CI por tres errores de código que la verificación local en Windows con Python 3.14 no podía mostrar:

| Error | Dónde | Causa | Corrección |
| ----- | ----- | ----- | ---------- |
| El job «Base install» fallaba en todos los PR | M00, `ci.yml` | `uv pip freeze` lista una wheel local como `trelloctl @ file:///…`, no como `trelloctl==x.y.z`, y el `grep` lo tomaba por una dependencia extra | `grep -Ev '^trelloctl( @ \|==)'` |
| `RecursionError` en Windows con Python 3.12 | M01, `infra/logging.py` | Si fallaba la rotación, el aviso se emitía por el mismo handler, que volvía a intentar rotar, y así sin fin | Guarda contra la re-entrada en `SafeRotatingFileHandler` |
| Test del `SKILL.md` empaquetado falla en Windows | M13 | Git hace checkout con CRLF y el test comparaba texto con la wheel, que tiene LF | Comparar bytes |

Los problemas de proceso que los multiplicaron: el CI no corrió hasta abrir los PR juntos al final; las fusiones locales a `develop` hicieron que las ramas salieran de un `develop` sin validar; `fail-fast` cancela los otros ocho jobs de la matriz, así que un error parece nueve; `gh run rerun` reutiliza el merge ref antiguo; hubo dos orquestadores a la vez al principio, y una caída de GitHub Actions (18:12 UTC) produjo corridas fallidas sin jobs.

Desde entonces cada milestone pasa, antes de abrir su PR, por una lista de comprobaciones: sincronizar con `origin/develop` mediante merge, las seis comprobaciones, pytest en Python 3.12 y 3.14, la instalación base en un entorno limpio (con `mcp serve` saliendo con 2 sin el extra) y una revisión de riesgos de plataforma (re-entrada del logging, comparaciones de texto frente a bytes, `encoding`, `pathlib`, cierre de archivos y conexiones antes de borrarlos o rotarlos, reloj inyectable). Los PR se abren y se fusionan de uno en uno, en orden de ola, y la corrección va en la rama del milestone que introdujo el error. Con ese proceso los PR #10 a #17 pasaron el CI a la primera.

## 7. Cómo verificar en local

```bash
uv sync --all-extras
```

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

```bash
uv run mypy
```

```bash
uv run pytest --cov=trelloctl --cov-branch --cov-report=term-missing --cov-fail-under=90
```

```bash
uv run python tools/check_traceability.py
```

Para la versión mínima de Python: `uv run --python 3.12 pytest -q`. Para la instalación base: `uv build --wheel`, instalar la wheel en un entorno limpio y comprobar que `uv pip freeze` solo lista `trelloctl`.

## 8. Pendiente y riesgos

- **TCs manuales contra Trello real** (TC-MAN-01 a TC-MAN-11 de [[tc-manuales-trello]]): los ejecuta el responsable, y son condición para fusionar `develop` en `main`. Cuando uno discrepe del Trello falso, se corrige primero el falso y luego el código.
- **Fusión de `develop` en `main` y publicación** (por ejemplo en PyPI): decisión del responsable, fuera del alcance del plan.
- **PRD:** la v1.1.0 ya incorpora las aclaraciones de la sección 4. Queda pendiente, para una versión futura, reconsiderar la deprecación del shortLink y la purga de la caché de tableros desaparecidos.
- **Riesgo principal:** el Trello falso se basa en la documentación pública del API; los TCs manuales son la comprobación contra el comportamiento real. Los más sensibles son la recarga incremental, los adjuntos con OAuth, el Inbox personal y el servidor MCP con un cliente real.
- **Higiene del repositorio:** si existe un `develop` local con merges propios de la fase de integración, no debe publicarse: sus merge commits son distintos de los de `origin/develop`.
