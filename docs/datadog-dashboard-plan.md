# Datadog Dashboard Plan

## Prerequisites

- Dogcatcher running in AWS (ECS Fargate)
- Datadog Agent sidecar in the ECS task definition
- DogStatsD metrics emitting from `agent/performance.py`

## Metrics to Display

| Metric | Type | Tags |
|---|---|---|
| `dogcatcher.logs.processed` | Counter | `team`, `service`, `env` |
| `dogcatcher.tickets.created` | Counter | `team`, `service`, `env` |
| `dogcatcher.duplicates.detected` | Counter | `team`, `service` |
| `dogcatcher.run.duration` | Gauge | `team`, `env` |
| `dogcatcher.errors` | Counter | `team`, `service`, `error_type` |

## Dashboard Widgets

1. **Timeseries** - Tickets created over time
2. **Top List** - Services with most errors
3. **Query Value** - Total duplicates prevented
4. **Heatmap** - Error distribution by hour
5. **Group** - Metrics broken down by team

## Steps

1. Verify DogStatsD metrics are emitting correctly from AWS
2. Create dashboard in Datadog (Dashboards > New Dashboard)
3. Add widgets using the metrics above
4. Share dashboard with the team
