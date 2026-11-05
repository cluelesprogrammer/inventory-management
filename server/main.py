import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

# Orders file that restock orders are persisted to (tests can monkeypatch this)
ORDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'orders.json')
_orders_lock = threading.Lock()

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None
    # Only set on restock orders created from the Restocking tab
    order_type: Optional[str] = None
    lead_time_days: Optional[int] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

# ---------------------------------------------------------------------------
# Restocking
# ---------------------------------------------------------------------------

# Days from order to delivery, per product category. Unknown categories use the default.
LEAD_TIME_DAYS = {
    "Circuit Boards": 7,
    "Sensors": 10,
    "Controllers": 10,
    "Power Supplies": 12,
    "Actuators": 14,
}
DEFAULT_LEAD_TIME_DAYS = 14

class RestockLine(BaseModel):
    sku: str
    quantity: int

class CreateRestockOrderRequest(BaseModel):
    budget: float
    items: List[RestockLine]

def lead_time_for(category: str) -> int:
    return LEAD_TIME_DAYS.get(category, DEFAULT_LEAD_TIME_DAYS)

def restock_candidates() -> list:
    """Forecast SKUs that need restocking, largest shortfall first.

    shortfall = forecasted_demand - quantity_on_hand. SKUs with no inventory record
    or no shortfall are excluded. Unit cost comes from inventory (the forecast has none).
    """
    inventory_by_sku = {item["sku"]: item for item in inventory_items}
    candidates = []
    for forecast in demand_forecasts:
        item = inventory_by_sku.get(forecast["item_sku"])
        if item is None:
            continue
        shortfall = forecast["forecasted_demand"] - item["quantity_on_hand"]
        if shortfall <= 0:
            continue
        candidates.append({
            "sku": item["sku"],
            "name": item["name"],
            "quantity": shortfall,
            "unit_cost": item["unit_cost"],
            "line_total": round(shortfall * item["unit_cost"], 2),
            "category": item["category"],
            "warehouse": item["warehouse"],
        })
    candidates.sort(key=lambda c: c["quantity"], reverse=True)
    return candidates

@app.get("/api/restocking/recommendations")
def get_restocking_recommendations(budget: float = Query(0, ge=0)):
    """Greedy fill: walk candidates largest-shortfall first and take each one that still fits
    the remaining budget. Items that don't fit are skipped, so a cheaper later item can still be added."""
    remaining = budget
    selected = []
    for candidate in restock_candidates():
        if candidate["line_total"] <= remaining:
            selected.append(candidate)
            remaining = round(remaining - candidate["line_total"], 2)
    return {
        "budget": budget,
        "total_cost": round(budget - remaining, 2),
        "remaining": remaining,
        "items": selected,
    }

def _save_orders_file(all_orders: list) -> None:
    """Write orders atomically: temp file in the same directory, then os.replace."""
    directory = os.path.dirname(ORDERS_FILE)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(all_orders, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, ORDERS_FILE)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

@app.post("/api/restocking/orders", response_model=Order, status_code=201)
def place_restocking_order(request: CreateRestockOrderRequest):
    """Create a restock order. Prices and lead times are recomputed here; the client's
    numbers are not trusted. The order is appended to orders and written to orders.json."""
    if not request.items:
        raise HTTPException(status_code=400, detail="Order has no items")

    inventory_by_sku = {item["sku"]: item for item in inventory_items}
    seen = set()
    lines = []
    total = 0.0
    lead_days = 0
    for line in request.items:
        if line.quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Quantity for {line.sku} must be positive")
        if line.sku in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate SKU {line.sku}")
        seen.add(line.sku)
        item = inventory_by_sku.get(line.sku)
        if item is None:
            raise HTTPException(status_code=400, detail=f"Unknown SKU {line.sku}")
        line_total = round(line.quantity * item["unit_cost"], 2)
        total += line_total
        # The order arrives when its slowest category arrives
        lead_days = max(lead_days, lead_time_for(item["category"]))
        lines.append({
            "sku": item["sku"],
            "name": item["name"],
            "quantity": line.quantity,
            "unit_price": item["unit_cost"],
        })

    total = round(total, 2)
    if total > request.budget:
        raise HTTPException(
            status_code=400,
            detail=f"Order total {total} exceeds budget {request.budget}",
        )

    with _orders_lock:
        now = datetime.now().replace(microsecond=0)
        year = now.year
        restock_count = sum(1 for o in orders if o.get("order_type") == "restock")
        next_id = max((int(o["id"]) for o in orders), default=0) + 1
        categories = {item["category"] for item in (inventory_by_sku[l["sku"]] for l in lines)}
        warehouses = {inventory_by_sku[l["sku"]]["warehouse"] for l in lines}
        order = {
            "id": str(next_id),
            "order_number": f"RST-{year}-{restock_count + 1:04d}",
            "customer": "Internal Restock",
            "items": lines,
            "status": "Submitted",
            "order_date": now.isoformat(),
            "expected_delivery": (now + timedelta(days=lead_days)).isoformat(),
            "total_value": total,
            "warehouse": warehouses.pop() if len(warehouses) == 1 else "Multiple",
            "category": categories.pop() if len(categories) == 1 else "Multiple",
            "order_type": "restock",
            "lead_time_days": lead_days,
        }
        # Write the file first, then update memory, so a failed write leaves no phantom order
        _save_orders_file(orders + [order])
        orders.append(order)
    return order

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
