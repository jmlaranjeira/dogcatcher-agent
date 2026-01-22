# Commands Cheat Sheet

Copy/paste-friendly commands for the two agents: Watcher (Datadog → LLM → Jira) and Patchy (🩹🤖).

Tip: Use python-dotenv to load .env automatically when running locally.

## Watcher (Datadog → LLM → Jira)

### Local (dry-run)
```bash
python main.py --dry-run --env dev --service myservice --hours 24 --limit 50
```

### Local (real, con límite por ejecución)
```bash
python main.py --real --env prod --service myservice --hours 24 --limit 5 --max-tickets 3
```

### Local (parámetros típicos)
```bash
python main.py --dry-run --env dev --service myservice --hours 48 --limit 100
python main.py --real    --env prod --service myservice --hours 48 --limit 100 --max-tickets 5
```

### Reporte de auditoría
```bash
python tools/report.py --since-hours 48
```

### Docker compose (Watcher)
```bash
docker compose up --build
```

## Patchy (🩹🤖)

Requiere `GITHUB_TOKEN` y `patchy/repos.json` configurado.
Para cargar .env automáticamente:
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49 --draft true
```

### Casos rápidos (local)

- Draft PR con referencia Jira:
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph \
  --service myservice \
  --error-type npe \
  --jira DPRO-2518 \
  --loghash 09e1ef6cd94b \
  --draft true
```

- PR real (no draft), con localización y fix v1 (Java):
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph \
  --service myservice \
  --error-type "price missing" \
  --hint priceMissing \
  --stacktrace "src/main/java/com/acme/Foo.java:123" \
  --jira DPRO-2491 \
  --loghash 4c452e2d1c49 \
  --mode fix \
  --draft false
```

- PR mínima sin Jira:
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49 --draft true
```

### Modos de edición (`--mode`)
- `touch`: crea/sobrescribe un archivo de metadatos.
- `note` (default): añade nota en el archivo objetivo; si no existe, crea metadatos.
- `fix`: intenta un fix mínimo (v1: guardia NPE en Java; comentarios guía en Python/TS/JS).

### Docker compose (Patchy)
```bash
docker compose run --rm -e GITHUB_TOKEN=$GITHUB_TOKEN patchy \
  python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49 --draft true
```

## Tips
- Actualiza `patchy/repos.json` con `owner`, `name`, `default_branch` y opcionalmente `allowed_paths`, `lint_cmd`, `test_cmd`.
- Para entornos locales, asegúrate de que `.env` no contiene secretos que no quieras exportar fuera del proceso (usa python-dotenv como arriba).
