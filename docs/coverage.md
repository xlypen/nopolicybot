# Test Coverage

## CI Gate

- **Threshold:** `--cov-fail-under=46` (см. `.github/workflows/ci.yml`)
- **Artifact:** `coverage.xml` сохраняется как GitHub Actions artifact
- **Цель:** повысить до 75% по мере исправления тестов и добавления новых

## Текущее состояние

- Coverage gate активен в CI
- При снижении покрытия ниже порога pipeline падает
- Рекомендуется запускать `pytest --cov --cov-report=term` локально перед push
