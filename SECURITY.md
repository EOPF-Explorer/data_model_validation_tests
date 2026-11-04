# Security Policy

## Supported Versions

Only the `main` branch is supported.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report vulnerabilities via [GitHub private security advisories](https://github.com/EOPF-Explorer/data_model_validation_tests/security/advisories/new).

We aim to acknowledge reports within **5 business days** and provide a resolution timeline within **15 business days**, depending on severity and complexity.

## Scope

In scope:
- The acceptance suite in `acceptance/`
- The GDAL test suite (`gdal/`, `helpers.py`, `conftest.py`) and the image built from `docker/Dockerfile.gdal`
- GitHub Actions workflows in `.github/workflows/`

Out of scope:
- The services and data stores the checks run against: report those to their own repositories

## Preferred Languages

English.
