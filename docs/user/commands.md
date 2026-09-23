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

- PR real (no draft), con localización del fallo y fix verificado por LLM:
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph \
  --service myservice \
  --error-type "price missing" \
  --hint priceMissing \
  --stacktrace "src/main/java/com/acme/Foo.java:123" \
  --jira DPRO-2491 \
  --loghash 4c452e2d1c49 \
  --draft false
```

- PR mínima sin Jira:
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49 --draft true
```

### Cómo decide Patchy
El LLM propone un cambio mínimo y un test nuevo que reproduce el error. Patchy solo abre la PR si el test falla con el código original, pasa con el fix aplicado y la suite completa (`test_cmd`) sigue en verde. Si no lo consigue, no abre ninguna PR y comenta el diagnóstico en el ticket de Jira. Ver `patchy/README.md`.

### Docker compose (Patchy)
```bash
docker compose run --rm -e GITHUB_TOKEN=$GITHUB_TOKEN patchy \
  python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49 --draft true
```

## Tips
- Actualiza `patchy/repos.json` con `owner`, `name`, `default_branch` y opcionalmente `allowed_paths`, `lint_cmd`, `test_cmd`, `test_single_cmd`.
- Para entornos locales, asegúrate de que `.env` no contiene secretos que no quieras exportar fuera del proceso (usa python-dotenv como arriba).
