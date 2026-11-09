# trelloctl

CLI en Python para operar tableros de Trello desde un harness de agentes. Cada comando escribe en stdout un único objeto JSON estructurado (el envelope) y termina con un código de salida que clasifica el resultado. Incluye un servidor MCP opcional por stdio.

- Solo biblioteca estándar en tiempo de ejecución (Python 3.12 o superior). La única dependencia opcional es el SDK oficial de MCP, detrás del extra `trelloctl[mcp]`.
- Plataformas: Windows y macOS (Linux funciona y se ejecuta en CI, sin soporte formal).
- Las credenciales salen solo de `config.toml` o de variables de entorno, nunca de opciones de la línea de comandos, y no aparecen sin enmascarar en la salida ni en los logs.

## Instalación

Con `pipx`:

```bash
pipx install trelloctl
```

Con `uv`:

```bash
uv tool install trelloctl
```

Desde un clon del repositorio:

```bash
uv tool install .
```

Comprobación:

```bash
trelloctl --version
```

## Configuración mínima

La configuración vive en `~/.trelloctl/config.toml` (se puede mover con la variable `TRELLOCTL_HOME`). Para crear el archivo con la plantilla comentada:

```bash
trelloctl config init
```

Solo la sección `[auth]` es obligatoria; cualquier otra clave ausente toma su valor por defecto. Un archivo mínimo válido:

```toml
[auth]
api_key = "<api key>"
token = "<token>"
```

`trelloctl config show` muestra la configuración efectiva con las credenciales enmascaradas y `trelloctl config validate` la comprueba. `trelloctl doctor` diagnostica el entorno completo (Python, configuración, credenciales, DNS, TLS, token, tablero, caché, MCP, skill e Inbox) y `trelloctl doctor --api` contrasta además los endpoints usados con la especificación OpenAPI de Trello.

## Credenciales

1. Crea la API key en https://trello.com/power-ups/admin (Power-Up, Trello Auth, «Generate a new API Key»).
2. Genera el token abriendo `https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&key=<api_key>` y autorizando la aplicación.
3. Guarda ambos valores en la sección `[auth]` de `config.toml`, o expórtalos como variables de entorno:

```text
TRELLO_API_KEY=<api key>
TRELLO_TOKEN=<token>
```

Las variables de entorno prevalecen sobre el archivo. No existe ninguna opción de línea de comandos para pasar credenciales.

## Selección de tablero

Cada comando trabaja sobre un único tablero. Se elige, por orden de precedencia:

1. `--board <id|shortLink|url|nombre>`
2. la variable de entorno `TRELLO_BOARD`
3. `[board].default` de `config.toml`

Para guardar el tablero por defecto:

```bash
trelloctl config board set 5f1b2c3d4e5f6a7b8c9d0e1f
```

```bash
trelloctl config board show
```

Por defecto un nombre solo coincide con tableros activos; `--include-archived-boards` incluye los archivados. Si varios tableros (o listas, tarjetas, etiquetas...) coinciden con un nombre, el comando falla con un error `AMBIGUOUS_*` que trae `error.details.candidates` y no ejecuta nada: repite el comando con el id elegido.

## Ejemplos

Cada ejemplo se ejecuta tal cual contra un Trello simulado como parte de las pruebas. Los comandos suponen un tablero por defecto con las listas `To Do`, `Doing` y `Done`.

Leer el tablero:

```bash
trelloctl board show
```

```bash
trelloctl board lists
```

```bash
trelloctl board labels
```

Crear una tarjeta con etiquetas:

```bash
trelloctl card create --list "To Do" --name "Nueva tarea" --labels bug,urgent
```

Buscar tarjetas (el comodín `*` y la búsqueda en nombre, descripción y comentarios):

```bash
trelloctl card search "core-78*" --in name,desc,comments
```

Listar con filtros y recortar la salida:

```bash
trelloctl card list --list "To Do" --label bug --limit 10 --fields id,name
```

Mover una tarjeta. Con `--left N` o `--right N` se mueve a la lista situada N posiciones a la izquierda o a la derecha; conviene ensayar antes con `--dry-run`:

```bash
trelloctl card move "Review PR 42" --right 1 --dry-run
```

```bash
trelloctl card move "Review PR 42" --right 1
```

Comentar, marcar un ítem de checklist y consultar la tarjeta completa:

```bash
trelloctl card comment add "Review PR 42" --text "Listo para revisar, @ana"
```

```bash
trelloctl card checklist item check "Review PR 42" QA "e2e tests"
```

```bash
trelloctl card get "Review PR 42" --full
```

Archivar en lugar de borrar (reversible):

```bash
trelloctl card archive "CORE-78 fix login"
```

Inbox personal:

```bash
trelloctl inbox add --name "Review November invoice" --due 2026-11-15
```

```bash
trelloctl inbox move "Review November invoice" --to "To Do" --pos 0
```

Las operaciones irreversibles exigen `--yes` y sin él fallan con `CONFIRMATION_REQUIRED`: `board label remove`, `card comment remove`, `card checklist remove` y `card checklist item remove`.

Ayuda de cualquier comando:

```bash
trelloctl card move --help
```

## Formato de salida y códigos de salida

Éxito: `{"ok": true, "data": ..., "meta": {...}}`. Error: `{"ok": false, "error": {"code", "message", "details"}, "meta": {...}}`. `meta.schema_version` vale `"1"` en todas las respuestas. La salida va indentada; `--compact` la escribe en una sola línea. Los logs van a stderr (con `-v`) y a `~/.trelloctl/logs/trelloctl.log`, nunca a stdout.

| Código | Clase | Códigos de error |
| ------ | ----- | ---------------- |
| 0 | éxito | |
| 1 | genérico | `INTERNAL` |
| 2 | uso | `USAGE` |
| 3 | configuración | `CONFIG_NOT_FOUND`, `CONFIG_INVALID`, `BOARD_NOT_CONFIGURED`, `HOME_NOT_WRITABLE` |
| 4 | autenticación | `AUTH_MISSING`, `AUTH_INVALID` |
| 5 | no encontrado o ambiguo | `*_NOT_FOUND`, `ATTACHMENT_GONE`, `AMBIGUOUS_*`, `INBOX_UNAVAILABLE` |
| 6 | regla de dominio | `MOVE_OUT_OF_RANGE`, `CONFIRMATION_REQUIRED`, `ATTACHMENT_TOO_LARGE` |
| 7 | API remoto | `API_ERROR`, `RATE_LIMITED` |
| 8 | red | `NETWORK_ERROR`, `TIMEOUT` |
| 9 | caché rancio | `CACHE_STALE` (con `ok: true`) |

No hay reintentos contra la API de Trello: una petición, un resultado. Ante `RATE_LIMITED` espera `error.details.retry_in_seconds`.

## Caché

Las lecturas se sirven de una caché local (`~/.trelloctl/cache.db`): metadatos (listas, etiquetas, miembros, tableros) 30 minutos y tarjetas 5 minutos. `--fresh` fuerza la lectura desde Trello y `--no-cache` ni lee ni escribe la caché. `meta.cache` aparece en toda lectura. Comandos: `trelloctl cache reload`, `trelloctl cache status` y `trelloctl cache clear`.

## Skill para el harness

El paquete incluye un `SKILL.md` (en inglés) que enseña al agente a usar la herramienta:

```bash
trelloctl skills install --harness claude --scope user
```

`--scope project` lo instala en el proyecto actual y `trelloctl skills export --dir <ruta>` lo copia a una carpeta.

## Servidor MCP (experimental)

`trelloctl mcp serve` inicia un servidor MCP por stdio que expone las mismas operaciones como herramientas en `snake_case` (`card_move`, `board_lists`, `cache_reload`...). Es experimental y necesita el extra `trelloctl[mcp]`:

```text
uv tool install 'trelloctl[mcp]'
```

Sin el extra, `mcp serve` termina con `USAGE` (salida 2) y el comando de instalación.

Registro en el harness (una sola vez):

```json
{
  "mcpServers": {
    "trelloctl": { "command": "trelloctl", "args": ["mcp", "serve"] }
  }
}
```

- Cada herramienta devuelve el envelope JSON compacto como contenido de texto; `isError` es `true` cuando `ok` es falso.
- Todas aceptan los parámetros opcionales `board` e `include_archived_boards`. Donde la CLI exige `--yes`, la herramienta exige `yes: true`.
- No se exponen `config *`, `doctor`, `version`, `skills *`, `cache clear` ni `card attachments clear`.
- El valor `-` (stdin) de `desc` y `text` devuelve `USAGE` («stdin not available in MCP»), porque stdin es el canal del protocolo.
- El servidor arranca aunque falte `config.toml` o las credenciales; cada llamada devuelve entonces `CONFIG_NOT_FOUND` o `AUTH_MISSING`.
- La configuración se lee una sola vez al arrancar: un cambio en `config.toml` exige reiniciar el servidor. Los TTL del caché se evalúan en cada llamada.
- stdout contiene solo mensajes del protocolo; los logs van a stderr (con `-v`) y a `~/.trelloctl/logs/trelloctl.log`.

## Desarrollo

```text
uv sync --all-extras
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=trelloctl --cov-branch --cov-report=term-missing --cov-fail-under=90
uv run python tools/check_traceability.py
```

Ninguna prueba automática habla con Trello real: usan un servidor Trello simulado en loopback. La documentación de producto y los planes están en `docs/`.

## Licencia

MIT. Consulta [LICENSE](LICENSE).
