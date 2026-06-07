## Summary

<!-- What does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Schema / migration change
- [ ] CI / tooling / dependency update
- [ ] Documentation

## Testing done

<!-- Describe what you tested and how. -->

## Checklist

- [ ] `just api-test` passes with ≥80% coverage
- [ ] `just api-lint` and `just api-typecheck` pass
- [ ] `just api-security` is clean (Bandit + Semgrep)
- [ ] No code signs, sends, or handles transaction private keys
- [ ] All monetary values use `NUMERIC` (never `float`)
- [ ] Any new migrations are reversible (`downgrade()` is correct)
- [ ] ADR or docs updated if an architectural decision changed
