# Generated OpenAPI contract

Generate the backend schema after installing dependencies:

```bash
cd apps/backend
python -c 'import json; from app.main import app; print(json.dumps(app.openapi()))' \
  > ../../packages/contracts/openapi/openapi.json
```

The JSON artifact is intentionally generated rather than hand-maintained.
