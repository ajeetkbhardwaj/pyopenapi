# FastAPI example

A small FastAPI app with routers and Pydantic models used to validate `pyopenapi` static analysis and agent/spec generation.

## Endpoints
- `GET /health`
- `GET /products`
- `POST /products`
- `GET /products/{product_id}`
- `PATCH /products/{product_id}`
- `DELETE /products/{product_id}`
- `GET /customers/{customer_id}`
- `GET /customers/{customer_id}/orders`
- `POST /orders`
- `GET /orders/{order_id}`

## Generate OpenAPI

```bash
# from pyopenapi/
python -m pyopenapi analyze example/fastapiapp
python -m pyopenapi generate example/fastapiapp --engine static --format json --output example/fastapiapp/output
# or with configured LLM provider
python -m pyopenapi generate example/fastapiapp --engine agent --format json --output example/fastapiapp/output-agent
python -m pyopenapi dashboard example/fastapiapp/output-agent
```
