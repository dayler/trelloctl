---
title: trelloctl — TCs manuales contra Trello
version: 1.0.0
date: 2026-11-09
tags: [pruebas, manual, trelloctl]
---

# trelloctl — TCs manuales contra Trello

Diez casos que se ejecutan a mano contra Trello real, fuera de CI, porque el plan gratuito y sus límites de tasa impiden automatizarlos. Se eligieron donde el Trello falso de la suite automatizada podría diferir del servicio real: contratos que el PRD da por observados, cabeceras de autenticación, búsqueda, recarga incremental, Inbox (no documentado) y la integración con un harness real. Todo lo demás está cubierto por los 423 TCs automatizados del [[00-plan-maestro]].

Se ejecutan en M16 (y de nuevo antes de cada release). Una diferencia entre Trello y el Trello falso se corrige primero en el Trello falso y después en el código.

## Preparación

- **Cuenta y tablero.** Un workspace del plan gratuito con un tablero dedicado `trelloctl-sandbox` (nunca un tablero de trabajo) con las listas `To Do`, `Doing`, `Review` y `Done`, las etiquetas `bug` (rojo) y `urgent` (naranja), y tres tarjetas en `To Do`. Un segundo usuario invitado al tablero para TC‑MAN‑06.
- **Credenciales.** API key y token propios obtenidos con las URLs de AC‑1.3, en `config.toml` o en `TRELLO_API_KEY` / `TRELLO_TOKEN`. Nunca se pegan en tickets, PRs ni capturas.
- **Instalación.** El wheel de la release candidata instalado con `uv tool install` o `pipx`, con el extra `mcp` para TC‑MAN‑10.
- **HOME limpio.** `TRELLOCTL_HOME` apuntando a un directorio vacío, para empezar sin caché.
- **Presupuesto.** Cada TC indica las llamadas aproximadas al API; la ejecución completa queda por debajo de 150 llamadas, lejos del límite de 100 cada 10 s por token si se ejecutan con pausas normales.
- **Limpieza.** Al terminar, archivar las tarjetas y listas creadas; el tablero sandbox se reutiliza.

## Resumen

| TC         | Qué valida                                           | AC principales                       | Llamadas | Estado | Fecha | Versión |
| ---------- | ---------------------------------------------------- | ------------------------------------ | -------- | ------ | ----- | ------- |
| TC‑MAN‑01  | Credenciales, `doctor` y contrato OpenAPI real       | AC‑1.3, AC‑5.2, AC‑5.5, AC‑28.5      | ~20      | ⚪      |       |         |
| TC‑MAN‑02  | Credenciales revocadas                               | AC‑1.5, AC‑23.5                      | ~3       | ⚪      |       |         |
| TC‑MAN‑03  | Crear tarjeta completa y verla en la web             | AC‑12.1 a AC‑12.3, AC‑7.2            | ~6       | ⚪      |       |         |
| TC‑MAN‑04  | Mover N columnas tras reordenar listas en la web     | AC‑14.2, AC‑22.11, AC‑9.4            | ~6       | ⚪      |       |         |
| TC‑MAN‑05  | Recarga incremental con cambios hechos en la web     | AC‑22.5, AC‑22.6, AC‑22.9           | ~10      | ⚪      |       |         |
| TC‑MAN‑06  | Comentarios: mención, edición propia y ajena, búsqueda | AC‑17.2, AC‑17.3, AC‑13.3          | ~10      | ⚪      |       |         |
| TC‑MAN‑07  | Checklists y `badges` calculados por Trello          | AC‑20.2, AC‑20.5, AC‑20.7            | ~10      | ⚪      |       |         |
| TC‑MAN‑08  | Adjuntos: subida, descarga OAuth y sincronización    | AC‑21.2, AC‑21.3, AC‑21.5            | ~12      | ⚪      |       |         |
| TC‑MAN‑09  | Inbox personal (comportamiento no documentado)       | AC‑28.1 a AC‑28.4, AC‑28.7           | ~10      | ⚪      |       |         |
| TC‑MAN‑10  | Servidor MCP con Claude Code                         | AC‑25.1 a AC‑25.3, AC‑25.5           | ~10      | ⚪      |       |         |

Semáforos: ⚪ sin ejecutar · 🟢 pasa · 🔴 falla (abrir incidencia y enlazarla en la fila).

## TC‑MAN‑01 — Credenciales, `doctor` y contrato OpenAPI real

**Por qué es manual.** Solo el servicio real confirma que las URLs de credenciales siguen vigentes, que la especificación publicada contiene los endpoints que usa la herramienta y que el Inbox sigue expuesto.

1. Ejecutar `trelloctl config init` en el HOME limpio y comprobar que las dos URLs que indica funcionan: la de la API key lleva a la administración de Power‑Ups y la del token muestra la pantalla de autorización con `read,write` y sin caducidad.
2. Rellenar las credenciales y fijar `trelloctl-sandbox` con `trelloctl config board set trelloctl-sandbox`.
3. Ejecutar `trelloctl doctor`.
4. Ejecutar `trelloctl doctor --api --json`.

**Resultado esperado.** `doctor` sin `FAIL` y salida 0; DNS, TLS, token, tablero y caché en `OK`; Inbox en `OK`. `doctor --api` descarga la especificación de `developer.atlassian.com`, no informa endpoints ausentes y ejecuta una lectura de humo por endpoint de lectura con éxito. Cualquier `WARN` por parámetro obsoleto se registra como hallazgo.

## TC‑MAN‑02 — Credenciales revocadas

**Por qué es manual.** El texto exacto del 401 de Trello (`invalid token`) solo se observa en el servicio real.

1. Revocar el token desde la configuración de la cuenta de Trello (o usar uno caducado de una sesión anterior).
2. Ejecutar `trelloctl board show --fresh`.
3. Ejecutar `trelloctl --log-level debug board show --fresh` y revisar el log.
4. Generar un token nuevo y restaurarlo.

**Resultado esperado.** `AUTH_INVALID`, salida 4, sin reintento (una sola petición en el log), cuerpo original en `error.details.upstream`. Ni la respuesta ni el log contienen la key ni el token en claro.

## TC‑MAN‑03 — Crear tarjeta completa y verla en la web

**Por qué es manual.** La interpretación de Markdown, la fecha límite en la zona del usuario y la posición solo se ven en la interfaz de Trello.

1. Ejecutar `trelloctl card create --list "To Do" --name "TC-MAN-03" --desc-file desc.md --due 2026-12-01T10:00:00Z --labels bug,urgent --members <username> --pos top`, con un `desc.md` que incluya encabezado, lista, enlace y una etiqueta HTML.
2. Abrir la tarjeta en la web.
3. Ejecutar `trelloctl card get "TC-MAN-03"`.

**Resultado esperado.** La tarjeta aparece la primera de `To Do`, con las dos etiquetas, el miembro y la fecha correcta en la hora local del navegador. La descripción se ve con formato Markdown y el HTML aparece literal. `card get` devuelve fechas en UTC y los mismos datos que la web.

## TC‑MAN‑04 — Mover N columnas tras reordenar listas en la web

**Por qué es manual.** Verifica contra el orden real de Trello que `--right` usa metadatos frescos aunque el caché diga otra cosa.

1. Ejecutar `trelloctl board lists` para cachear el orden.
2. En la web, arrastrar `Review` antes de `Doing`.
3. Ejecutar `trelloctl card move "TC-MAN-03" --right 2 --dry-run` y después sin `--dry-run`.
4. Ejecutar `trelloctl board list move Done --left 1 --dry-run`.

**Resultado esperado.** El `--dry-run` calcula el destino con el orden nuevo (`To Do` → `Doing`, porque ahora `Review` va antes); el movimiento real deja la tarjeta al final de esa lista en la web. El `--dry-run` de la lista indica origen y destino correctos sin cambiar nada.

## TC‑MAN‑05 — Recarga incremental con cambios hechos en la web

**Por qué es manual.** La semántica real de `cards/all?modifiedSince` (qué cambios devuelve y que no informa borrados) es la base del caché y solo puede confirmarse en Trello.

1. Fijar `cache.cards_ttl_seconds = 30` y ejecutar `trelloctl cache reload`.
2. En la web: renombrar una tarjeta, archivar otra, crear una nueva y borrar definitivamente una cuarta.
3. Esperar 30 s y ejecutar `trelloctl card list --include-archived`.
4. Ejecutar `trelloctl card get <idShort de la tarjeta nueva>`.
5. Ejecutar `trelloctl cache reload` y repetir el paso 3.

**Resultado esperado.** Tras el paso 3 aparecen el nombre nuevo, la tarjeta archivada marcada `archived` y la nueva; la borrada sigue presente (limitación documentada en AC‑22.5). El paso 4 resuelve por `idShort` sin error. Tras el paso 5 la borrada desaparece.

## TC‑MAN‑06 — Comentarios: mención, edición propia y ajena, búsqueda

**Por qué es manual.** La notificación de menciones, el rechazo de Trello al editar comentarios ajenos y el índice de búsqueda por palabra (`partial=true`) son comportamiento del servicio.

1. Ejecutar `trelloctl card comment add "TC-MAN-03" --text "Revisar despliegue con @<segundo usuario>"`.
2. Comprobar que el segundo usuario recibe la notificación.
3. Ejecutar `trelloctl card comment edit "TC-MAN-03" <id> --text "Revisar despliegue final"`.
4. Con el segundo usuario, añadir un comentario desde la web; intentar `trelloctl card comment remove "TC-MAN-03" <id ajeno> --yes`.
5. Tras unos minutos, ejecutar `trelloctl card search "despl*" --in comments`.

**Resultado esperado.** Mención notificada; edición propia reflejada en la web; borrado del comentario ajeno rechazado con `API_ERROR` y el cuerpo de Trello en `details.upstream`. La búsqueda encuentra la tarjeta con el id del comentario, avisa en `meta.warnings` de que se quitaron los comodines y la consulta registrada en el log lleva `partial=true`.

## TC‑MAN‑07 — Checklists y `badges` calculados por Trello

**Por qué es manual.** Los contadores `badges.checkItems` y `checkItemsChecked` los calcula Trello; el caché debe coincidir con ellos.

1. Ejecutar `trelloctl card checklist add "TC-MAN-03" --name QA --items "unit,e2e,docs"`.
2. Ejecutar `trelloctl card checklist item check "TC-MAN-03" QA unit,e2e`.
3. Ejecutar `trelloctl card get "TC-MAN-03"` y después `trelloctl card get "TC-MAN-03" --fresh`.
4. Ejecutar `trelloctl card checklist item remove "TC-MAN-03" QA docs --yes`.

**Resultado esperado.** La web muestra 2/3 tras el paso 2. Los `badges` de los dos `card get` coinciden entre sí y con la web. Tras el paso 4, 2/2 en la web y en `card checklist list`.

## TC‑MAN‑08 — Adjuntos: subida, descarga OAuth y sincronización

**Por qué es manual.** La descarga exige la cabecera `Authorization: OAuth …` (AC‑21.2) y el límite de 10 MB del plan gratuito; ambos solo se validan en el servicio real.

1. Ejecutar `trelloctl card attachments add "TC-MAN-03" --file informe.pdf` (unos cientos de KB) y `--url https://example.com --name Enlace`.
2. Intentar subir un archivo de 11 MB.
3. Ejecutar `trelloctl card attachments download "TC-MAN-03" --all`.
4. En la web, borrar el PDF y subir otro archivo; repetir el paso 3.

**Resultado esperado.** El paso 2 devuelve `ATTACHMENT_TOO_LARGE` sin llamar al API. El paso 3 descarga el PDF en `attachments/<board>/<card>/` con el mismo tamaño que en Trello y devuelve el enlace como `skipped`. El paso 4 borra el PDF local, descarga el nuevo y lo refleja en las categorías de la respuesta.

## TC‑MAN‑09 — Inbox personal

**Por qué es manual.** `members/me?fields=inbox` y el tablero de tipo `inbox` no están documentados (PRD §11); es el riesgo de contrato más alto.

1. Desde la app móvil o la web, añadir una tarjeta al Inbox personal.
2. Ejecutar `trelloctl inbox list` sin `--board` ni tablero por defecto (`config board unset` temporalmente).
3. Ejecutar `trelloctl inbox add --name "TC-MAN-09" --due 2026-12-05`.
4. Restaurar el tablero por defecto; ejecutar `trelloctl card get <id de TC-MAN-09>` sin `--board`.
5. Ejecutar `trelloctl inbox move "TC-MAN-09" --to "To Do" --pos top`.

**Resultado esperado.** El paso 2 lista la tarjeta del paso 1. La del paso 3 aparece en el Inbox de la web. El paso 4 la devuelve con el contexto del Inbox. Tras el paso 5 la tarjeta está la primera en `To Do` del sandbox, ya no está en el Inbox y `cache status` lo refleja. Si algún paso falla porque Trello cambió el contrato, se registra y se comprueba que `doctor` lo detecta (AC‑28.5).

## TC‑MAN‑10 — Servidor MCP con Claude Code

**Por qué es manual.** Solo un harness real valida la negociación con el SDK vigente y el uso de las herramientas por un agente.

1. Registrar el servidor en Claude Code con la configuración de §10.10 del PRD.
2. Abrir una sesión y pedir al agente que liste las listas del tablero, busque `TC-MAN` y mueva una tarjeta a `Done`.
3. Pedir al agente que borre la etiqueta `urgent` sin confirmar; después, confirmando.
4. Abrir una segunda sesión en paralelo y pedir una lectura.
5. Cerrar ambas sesiones.

**Resultado esperado.** El agente ve las herramientas `snake_case` y ninguna de las excluidas (`config_*`, `doctor`, `skills_*`, `cache_clear`). Las operaciones se reflejan en la web. Sin `yes: true` recibe `CONFIRMATION_REQUIRED`. Las dos sesiones conviven sin errores de caché y el log muestra dos PID. Al cerrar, no queda ningún proceso `trelloctl mcp serve` vivo.

## Registro de ejecuciones

| Fecha | Versión | Ejecutado por | Resultado | Incidencias |
| ----- | ------- | ------------- | --------- | ----------- |
