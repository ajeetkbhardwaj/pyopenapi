from __future__ import annotations

from flask import Blueprint, Flask, jsonify, request

from schemas import Item, ItemCreate, Order, User

app = Flask(__name__)
api = Blueprint("api", __name__, url_prefix="/api")


@app.get("/health")
def health():
    """Service health check."""
    return jsonify({"status": "ok"})


@api.get("/items")
def list_items():
    """List items with optional filters."""
    status = request.args.get("status")
    limit = request.args.get("limit", type=int, default=20)
    return jsonify([])


@api.post("/items")
def create_item():
    """Create a new item."""
    payload = request.get_json(silent=True) or {}
    item = Item(id=1, owner_id=1, **ItemCreate(**payload).model_dump())
    return jsonify(item.model_dump()), 201


@api.get("/items/<int:item_id>")
def get_item(item_id: int):
    """Get an item by id."""
    return jsonify({"id": item_id, "name": "Sample", "price": 9.99, "status": "active", "tags": [], "owner_id": 1})


@api.put("/items/<int:item_id>")
def update_item(item_id: int):
    """Replace an item."""
    payload = request.get_json(silent=True) or {}
    return jsonify({"id": item_id, **payload})


@api.delete("/items/<int:item_id>")
def delete_item(item_id: int):
    """Delete an item."""
    return "", 204


@api.get("/users/<user_id>")
def get_user(user_id: str):
    """Get a user profile."""
    return jsonify(User(id=1, email="user@example.com", full_name="Example User").model_dump())


@api.get("/users/<int:user_id>/orders")
def list_user_orders(user_id: int):
    """List orders for a user."""
    include_cancelled = request.args.get("include_cancelled", default="false")
    return jsonify([Order(id=1, user_id=user_id, item_ids=[1], total=9.99).model_dump()])


app.register_blueprint(api)


if __name__ == "__main__":
    app.run(debug=True)
