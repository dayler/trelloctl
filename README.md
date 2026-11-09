# trelloctl

Minimal Python client for the Trello REST API, scoped to a single board.

## Status

Early stage. Nothing to install or run yet.

## Servidor MCP (experimental)

`trelloctl mcp serve` inicia un servidor MCP por stdio que expone las mismas operaciones como herramientas en `snake_case` (`card_move`, `board_lists`, `cache_reload`...). Es experimental y necesita el extra `trelloctl[mcp]`:

```bash
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

## License

MIT. See [LICENSE](LICENSE).
