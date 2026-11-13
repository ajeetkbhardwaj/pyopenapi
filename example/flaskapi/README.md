# Flask API example

A small Flask + Blueprint example used to validate `pyopenapi` static analysis and agent/spec generation.

## Endpoints
- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/<item_id>`
- `PUT /api/items/<item_id>`
- `DELETE /api/items/<item_id>`
- `GET /api/users/<user_id>`
- `GET /api/users/<user_id>/orders`

## Generate OpenAPI

```bash
# from pyopenapi/
python -m pyopenapi analyze example/flaskapi
python -m pyopenapi generate example/flaskapi --engine static --format json --output example/flaskapi/output
# or with configured LLM provider
python -m pyopenapi generate example/flaskapi --engine agent --format json --output example/flaskapi/output-agent
python -m pyopenapi dashboard example/flaskapi/output-agent
```
