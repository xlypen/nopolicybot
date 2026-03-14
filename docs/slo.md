# Service Level Objectives (SLO)

Этот документ фиксирует официальные пороги качества сервиса для `nopolicybot`.
Значения используются как блокирующие CI/CD-gate перед production promotion.

## Официальные пороги

| Метрика | Порог | Область применения | Источник |
|---|---:|---|---|
| p95 latency | <= 800 ms | FastAPI v2 main surfaces | `/api/v2/metrics` |
| 5xx rate (rolling) | <= 1% | FastAPI v2 main surfaces | `/api/v2/metrics` |
| rate-limit hit ratio | <= 5% | FastAPI v2 main surfaces | `429 / requests_total` |
| alert volume | TBD (N/hour) | FastAPI v2 + security alerts | устанавливается после staging soak |

## Машиночитаемые пороги

```yaml
p95_latency_ms_max: 800
error_5xx_rate_max: 0.01
rate_limit_hit_ratio_max: 0.05
alert_volume_per_hour_max: null
```

## Примечания по gate

- `alert_volume_per_hour_max` остается `null` до завершения 24-48h soak на staging.
- SLO gate для production проверяет удаленные метрики перед деплоем.
- Release-readiness gate дополнительно проверяет соответствие текущих метрик порогам из этого файла.
