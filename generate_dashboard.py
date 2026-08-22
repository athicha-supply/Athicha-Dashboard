"""
generate_dashboard.py
======================
All-in-one script: connect to SQL Server -> pull data from the 10 business
views -> aggregate into dashboard-ready summaries -> write a self-contained
index.html (with the data baked in) ready to publish via GitHub Pages.

Run this on a machine INSIDE your office network (same network as the
SQL Server). It does NOT need internet access to run -- only to `git push`
afterwards if you automate publishing.

Requirements:
    pip install pyodbc pandas
    (Also requires "ODBC Driver 17/18 for SQL Server" installed.)

Setup (environment variables, recommended over hardcoding):
    set SQLSERVER_HOST=myserver.database.windows.net
    set SQLSERVER_DB=mydatabase
    set SQLSERVER_USER=myuser
    set SQLSERVER_PASSWORD=mypassword
    python generate_dashboard.py

Output:
    ./index.html            <- the dashboard, ready for GitHub Pages
    ./dashboard_data.json   <- the raw aggregated data (for debugging/history)

Schedule this script (Windows Task Scheduler / cron) to run nightly, then
have it `git add`, `git commit`, `git push` the repo folder so GitHub Pages
picks up the fresh index.html automatically. See the README section at the
bottom of this file for exact commands.
"""

import os
import sys
import json
import base64
import decimal
import datetime

try:
    import pyodbc
    import pandas as pd
except ImportError:
    print("Missing dependency. Run: pip install pyodbc pandas")
    sys.exit(1)

# ---------------------------------------------------------------------------
# CONFIG -- edit directly, or leave as-is to pull from environment variables
# ---------------------------------------------------------------------------
CONFIG = {
    "host": os.environ.get("SQLSERVER_HOST", "server"),
    "port": os.environ.get("SQLSERVER_PORT", "1433"),
    "database": os.environ.get("SQLSERVER_DB", "db"),
    "user": os.environ.get("SQLSERVER_USER", "user"),
    "password": os.environ.get("SQLSERVER_PASSWORD", "password"),
    "use_windows_auth": False,
    "driver": "{ODBC Driver 17 for SQL Server}",
}

OUTPUT_HTML = "index.html"
OUTPUT_JSON = "dashboard_data.json"

VIEWS = [
    "Customer", "Sales", "SalesDetails", "SalesPur",
    "Purchase", "PurchaseDetails", "Payment", "Expense",
    "StockMaster", "StockValue",
]


def build_connection_string(cfg):
    if cfg["use_windows_auth"]:
        auth_part = "Trusted_Connection=yes;"
    else:
        auth_part = "UID=" + cfg["user"] + ";PWD=" + cfg["password"] + ";"
    return (
        "DRIVER=" + cfg["driver"] + ";"
        "SERVER=" + cfg["host"] + "," + str(cfg["port"]) + ";"
        "DATABASE=" + cfg["database"] + ";"
        + auth_part +
        "Encrypt=yes;TrustServerCertificate=yes;"
    )


def json_safe(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def fetch_view(cursor, view_name):
    cursor.execute(f"SELECT * FROM dbo.[{view_name}]")
    cols = [d[0] for d in cursor.description]
    rows = []
    for row in cursor.fetchall():
        rows.append({cols[i]: json_safe(row[i]) for i in range(len(cols))})
    return pd.DataFrame(rows)


def cs(x):
    """Clean a string: strip non-breaking spaces / whitespace."""
    if isinstance(x, str):
        return x.replace("\xa0", " ").strip()
    return x


def main():
    conn_str = build_connection_string(CONFIG)
    print("Connecting to " + CONFIG["host"] + "/" + CONFIG["database"] + " ...")
    try:
        conn = pyodbc.connect(conn_str, timeout=20)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)
    cursor = conn.cursor()

    print("Extracting views...")
    dfs = {}
    for v in VIEWS:
        dfs[v] = fetch_view(cursor, v)
        print(f"  {v:<20} {len(dfs[v])} rows")
    cursor.close()
    conn.close()

    sales = dfs["Sales"]
    purchase = dfs["Purchase"]
    expense = dfs["Expense"]
    payment = dfs["Payment"]
    salesdet = dfs["SalesDetails"]
    stockval = dfs["StockValue"]
    customer = dfs["Customer"]

    for df in [sales, purchase, payment]:
        df["YearMonth"] = df["YearMonth"].astype(str)

    expense["group_clean"] = expense["group"].apply(cs)
    expense["accnam_clean"] = expense["accnam"].apply(cs)
    expense["YearMonth"] = expense["Years"].astype(str) + expense["MonthNo"].astype(str).str.zfill(2)

    # True operating expense = ledger group 5 (expense accounts), excluding
    # "\u0e0b\u0e37\u0e49\u0e2d" (COGS -- already counted in the Purchase view)
    opex = expense[(expense["group_clean"] == "5") & (expense["accnam_clean"] != "\u0e0b\u0e37\u0e49\u0e2d")]

    salesdet["YearMonth"] = salesdet["Years"].astype(str) + salesdet["Months"].astype(str).str.zfill(2)
    salesdet["stkdes_clean"] = salesdet["stkdes"].apply(cs)

    customer["cuscod_clean"] = customer["cuscod"].apply(cs)
    customer["cusnam_clean"] = customer["cusnam"].apply(cs)
    cust_map = dict(zip(customer["cuscod_clean"], customer["cusnam_clean"]))
    sales["cuscod_clean"] = sales["cuscod"].apply(cs)
    sales["cusnam_lookup"] = sales["cuscod_clean"].map(cust_map).fillna(sales["cuscod_clean"])

    def monthly_agg(df, valcol, label):
        g = df.groupby("YearMonth")[valcol].agg(["sum", "count"]).reset_index()
        g.columns = ["YearMonth", f"{label}_amount", f"{label}_count"]
        return g

    sales_m = monthly_agg(sales, "netval", "sales")
    purchase_m = monthly_agg(purchase, "netval", "purchase")
    opex_m = monthly_agg(opex, "amount", "expense")
    payment_m = monthly_agg(payment, "netamt", "payment")

    monthly = sales_m.merge(purchase_m, on="YearMonth", how="outer") \
        .merge(opex_m, on="YearMonth", how="outer") \
        .merge(payment_m, on="YearMonth", how="outer")
    monthly = monthly.fillna(0)
    monthly = monthly[monthly["YearMonth"].str.match(r"^\d{6}$", na=False)]
    monthly["Year"] = monthly["YearMonth"].str[:4]
    monthly["MonthNum"] = monthly["YearMonth"].str[4:6].astype(int)
    monthly["gross_profit"] = monthly["sales_amount"] - monthly["purchase_amount"]
    monthly["net_profit"] = monthly["gross_profit"] - monthly["expense_amount"]
    monthly = monthly.sort_values("YearMonth").reset_index(drop=True)

    yearly = monthly.groupby("Year")[[
        "sales_amount", "purchase_amount", "expense_amount", "payment_amount",
        "sales_count", "purchase_count", "expense_count", "payment_count",
        "gross_profit", "net_profit"
    ]].sum().reset_index().sort_values("Year")

    cust_sales = sales.groupby("cusnam_lookup")["netval"].agg(["sum", "count"]).reset_index()
    cust_sales.columns = ["customer", "total_sales", "order_count"]
    top_customers = cust_sales.sort_values("total_sales", ascending=False).head(15).round(2).to_dict(orient="records")

    prod_sales = salesdet.groupby("stkdes_clean").agg(
        total_amount=("amount", "sum"), total_qty=("Qty", "sum"), order_count=("amount", "count")
    ).reset_index()
    prod_sales.columns = ["product", "total_amount", "total_qty", "order_count"]
    top_products = prod_sales.sort_values("total_amount", ascending=False).head(15).round(2).to_dict(orient="records")

    exp_breakdown = opex.groupby("accnam_clean")["amount"].agg(["sum", "count"]).reset_index()
    exp_breakdown.columns = ["account", "total_amount", "count"]
    expense_breakdown = exp_breakdown.sort_values("total_amount", ascending=False).head(12).round(2).to_dict(orient="records")

    stockval["stkdes_clean"] = stockval["stkdes"].apply(cs)
    stock_top = stockval[["stkdes_clean", "\u0e22\u0e2d\u0e14\u0e04\u0e07\u0e40\u0e2b\u0e25\u0e37\u0e2d", "\u0e21\u0e39\u0e25\u0e04\u0e48\u0e32\u0e04\u0e07\u0e40\u0e2b\u0e25\u0e37\u0e2d"]].copy()
    stock_top.columns = ["product", "qty_remaining", "value_remaining"]
    top_stock_value = stock_top.sort_values("value_remaining", ascending=False).head(15).round(2).to_dict(orient="records")
    total_stock_value = float(stockval["\u0e21\u0e39\u0e25\u0e04\u0e48\u0e32\u0e04\u0e07\u0e40\u0e2b\u0e25\u0e37\u0e2d"].sum())
    negative_stock_count = int((stockval["\u0e22\u0e2d\u0e14\u0e04\u0e07\u0e40\u0e2b\u0e25\u0e37\u0e2d"] < 0).sum())

    total_sales = float(sales["netval"].sum())
    total_purchase = float(purchase["netval"].sum())
    total_opex = float(opex["amount"].sum())
    gross_profit = total_sales - total_purchase
    net_profit = gross_profit - total_opex

    kpi = {
        "total_sales": round(total_sales, 2),
        "total_purchase": round(total_purchase, 2),
        "total_opex": round(total_opex, 2),
        "gross_profit": round(gross_profit, 2),
        "net_profit": round(net_profit, 2),
        "total_payment_received": round(float(payment["netamt"].sum()), 2),
        "customer_count": int(customer.shape[0]),
        "active_customer_count": int((customer["Status"].apply(cs) == "A").sum()),
        "total_stock_value": round(total_stock_value, 2),
        "total_stock_qty": round(float(stockval["\u0e22\u0e2d\u0e14\u0e04\u0e07\u0e40\u0e2b\u0e25\u0e37\u0e2d"].sum()), 2),
        "negative_stock_count": negative_stock_count,
        "date_range": [str(sales["docdat"].min()), str(sales["docdat"].max())],
        "sales_doc_count": int(sales.shape[0]),
        "purchase_doc_count": int(purchase.shape[0]),
        "generated_at": datetime.datetime.now().isoformat(timespec="minutes"),
    }

    dashboard_data = {
        "kpi": kpi,
        "monthly": monthly.round(2).to_dict(orient="records"),
        "yearly": yearly.round(2).to_dict(orient="records"),
        "top_customers": top_customers,
        "top_products": top_products,
        "expense_breakdown": expense_breakdown,
        "top_stock_value": top_stock_value,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dashboard_data, f, ensure_ascii=False)
    print(f"Wrote {OUTPUT_JSON}")

    template_before = base64.b64decode(TEMPLATE_BEFORE_B64).decode("utf-8")
    template_after = base64.b64decode(TEMPLATE_AFTER_B64).decode("utf-8")
    html = template_before + json.dumps(dashboard_data, ensure_ascii=False) + template_after

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_HTML}")
    print("\nDone. Commit and push this folder to publish via GitHub Pages.")


TEMPLATE_BEFORE_B64 = "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9InRoIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCwgaW5pdGlhbC1zY2FsZT0xLjAiPgo8dGl0bGU+RGFzaGJvYXJkIOC4oOC4suC4nuC4o+C4p+C4oeC4mOC4uOC4o+C4geC4tOC4iDwvdGl0bGU+CjxzY3JpcHQgc3JjPSJodHRwczovL2NkbmpzLmNsb3VkZmxhcmUuY29tL2FqYXgvbGlicy9DaGFydC5qcy80LjQuMC9jaGFydC51bWQubWluLmpzIj48L3NjcmlwdD4KPHN0eWxlPgpAaW1wb3J0IHVybCgnaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1Pc3dhbGQ6d2dodEA0MDA7NTAwOzYwMDs3MDAmZmFtaWx5PUlCTStQbGV4K1NhbnMrVGhhaTp3Z2h0QDQwMDs1MDA7NjAwOzcwMCZmYW1pbHk9SUJNK1BsZXgrTW9ubzp3Z2h0QDUwMDs2MDA7NzAwJmRpc3BsYXk9c3dhcCcpOwoKOnJvb3R7CiAgLS1uYXZ5OiAjMTYyMTJFOwogIC0tbmF2eS0yOiAjMUYyRTNFOwogIC0tcGFwZXI6ICNFRUYxRjM7CiAgLS1jYXJkOiAjRkZGRkZGOwogIC0taW5rOiAjMUMyMzJCOwogIC0tbXV0ZWQ6ICM2NDc0OEE7CiAgLS1vcmFuZ2U6ICNGRjZBMTM7CiAgLS1vcmFuZ2UtZGltOiAjRkZFMUNDOwogIC0tc3RlZWw6ICMzRTY2ODA7CiAgLS1ncmVlbjogIzJFOEI1NzsKICAtLXJlZDogI0MxNDQzRDsKICAtLWxpbmU6ICNEQ0UxRTY7Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7fQpib2R5ewogIG1hcmdpbjowOwogIGJhY2tncm91bmQ6dmFyKC0tcGFwZXIpOwogIGNvbG9yOnZhcigtLWluayk7CiAgZm9udC1mYW1pbHk6J0lCTSBQbGV4IFNhbnMgVGhhaScsJ0lCTSBQbGV4IFNhbnMnLHNhbnMtc2VyaWY7CiAgLXdlYmtpdC1mb250LXNtb290aGluZzphbnRpYWxpYXNlZDsKfQoubW9ub3tmb250LWZhbWlseTonSUJNIFBsZXggTW9ubycsbW9ub3NwYWNlO30KLmRpc3BsYXl7Zm9udC1mYW1pbHk6J09zd2FsZCcsc2Fucy1zZXJpZjt9CgovKiAtLS0tLS0tLS0tIEhlYWRlciAvIGR1dHkgYm9hcmQgLS0tLS0tLS0tLSAqLwouaGVhZGVyewogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDE4MGRlZyx2YXIoLS1uYXZ5KSAwJSx2YXIoLS1uYXZ5LTIpIDEwMCUpOwogIGNvbG9yOiNmZmY7CiAgcGFkZGluZzoyOHB4IDMycHggMjJweDsKICBwb3NpdGlvbjpyZWxhdGl2ZTsKICBvdmVyZmxvdzpoaWRkZW47Cn0KLmhlYWRlcjo6YWZ0ZXJ7CiAgY29udGVudDoiIjsKICBwb3NpdGlvbjphYnNvbHV0ZTsgcmlnaHQ6LTQwcHg7IHRvcDotNjBweDsKICB3aWR0aDoyMjBweDsgaGVpZ2h0OjIyMHB4OyBib3JkZXItcmFkaXVzOjUwJTsKICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudChjaXJjbGUsIHJnYmEoMjU1LDEwNiwxOSwwLjE4KSAwJSwgcmdiYSgyNTUsMTA2LDE5LDApIDcwJSk7Cn0KLmhlYWRlci10b3B7CiAgZGlzcGxheTpmbGV4OyBqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjsgYWxpZ24taXRlbXM6ZmxleC1zdGFydDsgZ2FwOjE2cHg7IGZsZXgtd3JhcDp3cmFwOwp9Ci5leWVicm93ewogIGZvbnQtZmFtaWx5OidJQk0gUGxleCBNb25vJyxtb25vc3BhY2U7CiAgZm9udC1zaXplOjExcHg7IGxldHRlci1zcGFjaW5nOi4xNmVtOyB0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7CiAgY29sb3I6dmFyKC0tb3JhbmdlKTsgbWFyZ2luOjAgMCA2cHg7Cn0KLnRpdGxlewogIGZvbnQtZmFtaWx5OidPc3dhbGQnLHNhbnMtc2VyaWY7IGZvbnQtd2VpZ2h0OjYwMDsKICBmb250LXNpemU6Y2xhbXAoMjJweCwzLjJ2dywzMnB4KTsgbWFyZ2luOjA7IGxldHRlci1zcGFjaW5nOi4wMWVtOwp9Ci5zdWJ0aXRsZXtjb2xvcjojQUVCOUM0OyBmb250LXNpemU6MTMuNXB4OyBtYXJnaW46NnB4IDAgMDsgbWF4LXdpZHRoOjUyMHB4OyBsaW5lLWhlaWdodDoxLjU7fQouYmFkZ2UtcmFuZ2V7CiAgZm9udC1mYW1pbHk6J0lCTSBQbGV4IE1vbm8nLG1vbm9zcGFjZTsgZm9udC1zaXplOjExLjVweDsKICBiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4wNik7IGJvcmRlcjoxcHggc29saWQgcmdiYSgyNTUsMjU1LDI1NSwwLjE0KTsKICBwYWRkaW5nOjhweCAxNHB4OyBib3JkZXItcmFkaXVzOjZweDsgY29sb3I6I0Q3REVFNTsgd2hpdGUtc3BhY2U6bm93cmFwOwp9Ci5iYWRnZS1yYW5nZSBie2NvbG9yOiNmZmY7fQoKLyogS1BJIHN0cmlwICovCi5rcGktc3RyaXB7CiAgZGlzcGxheTpncmlkOyBncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KGF1dG8tZml0LG1pbm1heCgxNTBweCwxZnIpKTsKICBnYXA6MXB4OyBiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4wOCk7CiAgbWFyZ2luLXRvcDoyMnB4OyBib3JkZXItcmFkaXVzOjEwcHg7IG92ZXJmbG93OmhpZGRlbjsKICBib3JkZXI6MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4xKTsKfQoua3BpLWNlbGx7CiAgYmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LDAuMDMpOyBwYWRkaW5nOjE0cHggMTZweDsgbWluLXdpZHRoOjA7Cn0KLmtwaS1sYWJlbHsKICBmb250LXNpemU6MTAuNXB4OyB0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7IGxldHRlci1zcGFjaW5nOi4wOWVtOwogIGNvbG9yOiM4RkEwQUY7IG1hcmdpbjowIDAgNnB4OyB3aGl0ZS1zcGFjZTpub3dyYXA7IG92ZXJmbG93OmhpZGRlbjsgdGV4dC1vdmVyZmxvdzplbGxpcHNpczsKfQoua3BpLXZhbHVlewogIGZvbnQtZmFtaWx5OidJQk0gUGxleCBNb25vJyxtb25vc3BhY2U7IGZvbnQtd2VpZ2h0OjYwMDsKICBmb250LXNpemU6Y2xhbXAoMTVweCwxLjl2dywyMHB4KTsgY29sb3I6I2ZmZjsgbGV0dGVyLXNwYWNpbmc6LTAuMDFlbTsKfQoua3BpLXZhbHVlLm9yYW5nZXtjb2xvcjp2YXIoLS1vcmFuZ2UpO30KLmtwaS12YWx1ZS5ncmVlbntjb2xvcjojNkZDRjk3O30KLmtwaS12YWx1ZS5yZWR7Y29sb3I6I0YxODY3RTt9Ci5rcGktc3Vie2ZvbnQtc2l6ZToxMC41cHg7IGNvbG9yOiM3QzhDOUM7IG1hcmdpbi10b3A6M3B4O30KCi8qIC0tLS0tLS0tLS0gQ29udHJvbHMgLS0tLS0tLS0tLSAqLwouY29udHJvbHN7CiAgbWF4LXdpZHRoOjEyODBweDsgbWFyZ2luOjAgYXV0bzsgcGFkZGluZzoyMHB4IDMycHggMDsKICBkaXNwbGF5OmZsZXg7IGdhcDoxMnB4OyBmbGV4LXdyYXA6d3JhcDsgYWxpZ24taXRlbXM6Y2VudGVyOwp9Ci5zZWd7CiAgZGlzcGxheTpmbGV4OyBiYWNrZ3JvdW5kOiNmZmY7IGJvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7IGJvcmRlci1yYWRpdXM6OHB4OyBvdmVyZmxvdzpoaWRkZW47Cn0KLnNlZyBidXR0b257CiAgYm9yZGVyOm5vbmU7IGJhY2tncm91bmQ6dHJhbnNwYXJlbnQ7IHBhZGRpbmc6OXB4IDE2cHg7IGZvbnQtc2l6ZToxM3B4OyBmb250LXdlaWdodDo1MDA7CiAgZm9udC1mYW1pbHk6J0lCTSBQbGV4IFNhbnMgVGhhaScsc2Fucy1zZXJpZjsgY29sb3I6dmFyKC0tbXV0ZWQpOyBjdXJzb3I6cG9pbnRlcjsgdHJhbnNpdGlvbjouMTVzOwogIGJvcmRlci1yaWdodDoxcHggc29saWQgdmFyKC0tbGluZSk7Cn0KLnNlZyBidXR0b246bGFzdC1jaGlsZHtib3JkZXItcmlnaHQ6bm9uZTt9Ci5zZWcgYnV0dG9uLmFjdGl2ZXtiYWNrZ3JvdW5kOnZhcigtLW5hdnkpOyBjb2xvcjojZmZmO30KLnNlZyBidXR0b246aG92ZXI6bm90KC5hY3RpdmUpe2JhY2tncm91bmQ6I0YzRjVGNzt9CgpzZWxlY3R7CiAgZm9udC1mYW1pbHk6J0lCTSBQbGV4IFNhbnMgVGhhaScsc2Fucy1zZXJpZjsgZm9udC1zaXplOjEzcHg7IHBhZGRpbmc6OXB4IDEycHg7CiAgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTsgYm9yZGVyLXJhZGl1czo4cHg7IGJhY2tncm91bmQ6I2ZmZjsgY29sb3I6dmFyKC0taW5rKTsgY3Vyc29yOnBvaW50ZXI7Cn0KLmNvbnRyb2wtbGFiZWx7Zm9udC1zaXplOjExcHg7IGNvbG9yOnZhcigtLW11dGVkKTsgdGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlOyBsZXR0ZXItc3BhY2luZzouMDhlbTsgbWFyZ2luLXJpZ2h0OjJweDt9CgovKiAtLS0tLS0tLS0tIExheW91dCAtLS0tLS0tLS0tICovCi53cmFwe21heC13aWR0aDoxMjgwcHg7IG1hcmdpbjowIGF1dG87IHBhZGRpbmc6MjBweCAzMnB4IDYwcHg7fQouZ3JpZC0ye2Rpc3BsYXk6Z3JpZDsgZ3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7IGdhcDoxOHB4O30KLmdyaWQtM3tkaXNwbGF5OmdyaWQ7IGdyaWQtdGVtcGxhdGUtY29sdW1uczoyZnIgMWZyOyBnYXA6MThweDt9CkBtZWRpYSAobWF4LXdpZHRoOjg2MHB4KXsgLmdyaWQtMiwgLmdyaWQtM3tncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyO30gfQoKLmNhcmR7CiAgYmFja2dyb3VuZDp2YXIoLS1jYXJkKTsgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTsgYm9yZGVyLXJhZGl1czoxMnB4OwogIHBhZGRpbmc6MjBweDsgbWFyZ2luLWJvdHRvbToxOHB4Owp9Ci5jYXJkLWhlYWR7ZGlzcGxheTpmbGV4OyBqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjsgYWxpZ24taXRlbXM6YmFzZWxpbmU7IG1hcmdpbi1ib3R0b206MTRweDsgZmxleC13cmFwOndyYXA7IGdhcDo4cHg7fQouY2FyZC10aXRsZXtmb250LWZhbWlseTonT3N3YWxkJyxzYW5zLXNlcmlmOyBmb250LXNpemU6MTZweDsgZm9udC13ZWlnaHQ6NTAwOyBtYXJnaW46MDsgbGV0dGVyLXNwYWNpbmc6LjAxZW07fQouY2FyZC1ub3Rle2ZvbnQtc2l6ZToxMS41cHg7IGNvbG9yOnZhcigtLW11dGVkKTt9Ci5jaGFydC1ib3h7cG9zaXRpb246cmVsYXRpdmU7IGhlaWdodDoyODBweDt9Ci5jaGFydC1ib3gudGFsbHtoZWlnaHQ6MzQwcHg7fQoKdGFibGV7d2lkdGg6MTAwJTsgYm9yZGVyLWNvbGxhcHNlOmNvbGxhcHNlOyBmb250LXNpemU6MTNweDt9CnRoewogIHRleHQtYWxpZ246bGVmdDsgZm9udC1zaXplOjEwLjVweDsgdGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlOyBsZXR0ZXItc3BhY2luZzouMDZlbTsKICBjb2xvcjp2YXIoLS1tdXRlZCk7IHBhZGRpbmc6OHB4IDZweDsgYm9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGluZSk7IGZvbnQtd2VpZ2h0OjYwMDsKfQp0ZHtwYWRkaW5nOjlweCA2cHg7IGJvcmRlci1ib3R0b206MXB4IHNvbGlkICNGMEYyRjQ7fQp0cjpsYXN0LWNoaWxkIHRke2JvcmRlci1ib3R0b206bm9uZTt9Ci5udW17Zm9udC1mYW1pbHk6J0lCTSBQbGV4IE1vbm8nLG1vbm9zcGFjZTsgdGV4dC1hbGlnbjpyaWdodDsgd2hpdGUtc3BhY2U6bm93cmFwO30KLnJhbmt7CiAgZGlzcGxheTppbmxpbmUtZmxleDsgYWxpZ24taXRlbXM6Y2VudGVyOyBqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyOwogIHdpZHRoOjIwcHg7IGhlaWdodDoyMHB4OyBib3JkZXItcmFkaXVzOjVweDsgYmFja2dyb3VuZDp2YXIoLS1vcmFuZ2UtZGltKTsgY29sb3I6dmFyKC0tb3JhbmdlKTsKICBmb250LWZhbWlseTonSUJNIFBsZXggTW9ubycsbW9ub3NwYWNlOyBmb250LXNpemU6MTAuNXB4OyBmb250LXdlaWdodDo3MDA7IG1hcmdpbi1yaWdodDo4cHg7Cn0KLmZsYWctbmVne2NvbG9yOnZhcigtLXJlZCk7IGZvbnQtc2l6ZToxMXB4OyBmb250LWZhbWlseTonSUJNIFBsZXggTW9ubycsbW9ub3NwYWNlO30KCi5mb290ZXJ7CiAgbWF4LXdpZHRoOjEyODBweDsgbWFyZ2luOjAgYXV0bzsgcGFkZGluZzowIDMycHggNDBweDsgY29sb3I6dmFyKC0tbXV0ZWQpOyBmb250LXNpemU6MTEuNXB4OyBsaW5lLWhlaWdodDoxLjY7Cn0KLmZvb3RlciBie2NvbG9yOnZhcigtLWluayk7fQoKOjotd2Via2l0LXNjcm9sbGJhcntoZWlnaHQ6NnB4O3dpZHRoOjZweDt9Cjo6LXdlYmtpdC1zY3JvbGxiYXItdGh1bWJ7YmFja2dyb3VuZDojQzdDRkQ2O2JvcmRlci1yYWRpdXM6M3B4O30KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KCjxkaXYgY2xhc3M9ImhlYWRlciI+CiAgPGRpdiBjbGFzcz0iaGVhZGVyLXRvcCI+CiAgICA8ZGl2PgogICAgICA8cCBjbGFzcz0iZXllYnJvdyI+QnVzaW5lc3MgT3ZlcnZpZXcgwrcgTGl2ZSBEYXRhIEV4cG9ydDwvcD4KICAgICAgPGgxIGNsYXNzPSJ0aXRsZSI+4LmB4LiU4LiK4Lia4Lit4Lij4LmM4LiU4Lig4Liy4Lie4Lij4Lin4Lih4LiY4Li44Lij4LiB4Li04LiIPC9oMT4KICAgICAgPHAgY2xhc3M9InN1YnRpdGxlIj7guKrguKPguLjguJvguKLguK3guJTguILguLLguKIg4Lii4Lit4LiU4LiL4Li34LmJ4LitIOC4hOC5iOC4suC5g+C4iuC5ieC4iOC5iOC4suC4oiDguYHguKXguLDguKrguJXguYfguK3guIHguKrguLTguJnguITguYnguLIg4LiI4Liy4LiB4LiQ4Liy4LiZ4LiC4LmJ4Lit4Lih4Li54LilIFNRTCBTZXJ2ZXIgKDEwIHZpZXdzKSDigJQg4LiI4Lix4LiU4Lir4Lih4Lin4LiU4Lir4Lih4Li54LmI4LmB4Lil4Liw4LiE4Liz4LiZ4Lin4LiT4LiI4Liy4LiB4Lij4Liy4Lii4LiB4Liy4Lij4LmA4Lit4LiB4Liq4Liy4Lij4Lij4Liw4LiU4Lix4LiaIHRyYW5zYWN0aW9uPC9wPgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJiYWRnZS1yYW5nZSI+4LiC4LmJ4Lit4Lih4Li54LilIDxiPjIwMTgtMDEtMDM8L2I+IOKAlCA8Yj4yMDI2LTA4LTIyPC9iPjwvZGl2PgogIDwvZGl2PgoKICA8ZGl2IGNsYXNzPSJrcGktc3RyaXAiIGlkPSJrcGlTdHJpcCI+PC9kaXY+CjwvZGl2PgoKPGRpdiBjbGFzcz0iY29udHJvbHMiPgogIDxzcGFuIGNsYXNzPSJjb250cm9sLWxhYmVsIj7guKHguLjguKHguKHguK3guIc8L3NwYW4+CiAgPGRpdiBjbGFzcz0ic2VnIiBpZD0ibW9kZVNlZyI+CiAgICA8YnV0dG9uIGRhdGEtbW9kZT0ibW9udGhseSIgY2xhc3M9ImFjdGl2ZSI+4Lij4Liy4Lii4LmA4LiU4Li34Lit4LiZPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtbW9kZT0ieWVhcmx5Ij7guKPguLLguKLguJvguLU8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1tb2RlPSJ5b3kiPuC5gOC4l+C4teC4ouC4muC4m+C4teC4leC5iOC4reC4m+C4tSAoWW9ZKTwvYnV0dG9uPgogIDwvZGl2PgoKICA8c3BhbiBjbGFzcz0iY29udHJvbC1sYWJlbCIgaWQ9InllYXJMYWJlbCI+4Lib4Li1PC9zcGFuPgogIDxzZWxlY3QgaWQ9InllYXJTZWxlY3QiPjwvc2VsZWN0PgoKICA8c3BhbiBjbGFzcz0iY29udHJvbC1sYWJlbCI+4LiV4Lix4Lin4LiK4Li14LmJ4Lin4Lix4LiUPC9zcGFuPgogIDxzZWxlY3QgaWQ9Im1ldHJpY1NlbGVjdCI+CiAgICA8b3B0aW9uIHZhbHVlPSJzYWxlc19hbW91bnQiPuC4ouC4reC4lOC4guC4suC4ojwvb3B0aW9uPgogICAgPG9wdGlvbiB2YWx1ZT0icHVyY2hhc2VfYW1vdW50Ij7guKLguK3guJTguIvguLfguYnguK08L29wdGlvbj4KICAgIDxvcHRpb24gdmFsdWU9Imdyb3NzX3Byb2ZpdCI+4LiB4Liz4LmE4Lij4LiC4Lix4LmJ4LiZ4LiV4LmJ4LiZPC9vcHRpb24+CiAgICA8b3B0aW9uIHZhbHVlPSJuZXRfcHJvZml0Ij7guIHguLPguYTguKPguKrguLjguJfguJjguLQgKOC4q+C4peC4seC4h+C4q+C4seC4geC4hOC5iOC4suC5g+C4iuC5ieC4iOC5iOC4suC4oik8L29wdGlvbj4KICAgIDxvcHRpb24gdmFsdWU9ImV4cGVuc2VfYW1vdW50Ij7guITguYjguLLguYPguIrguYnguIjguYjguLLguKLguJTguLPguYDguJnguLTguJnguIfguLLguJk8L29wdGlvbj4KICAgIDxvcHRpb24gdmFsdWU9InBheW1lbnRfYW1vdW50Ij7guYDguIfguLTguJnguKPguLHguJrguIrguLPguKPguLA8L29wdGlvbj4KICA8L3NlbGVjdD4KPC9kaXY+Cgo8ZGl2IGNsYXNzPSJ3cmFwIj4KCiAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICA8ZGl2IGNsYXNzPSJjYXJkLWhlYWQiPgogICAgICA8aDMgY2xhc3M9ImNhcmQtdGl0bGUiIGlkPSJtYWluQ2hhcnRUaXRsZSI+4LmB4LiZ4Lin4LmC4LiZ4LmJ4Lih4Lij4Liy4Lii4LmA4LiU4Li34Lit4LiZPC9oMz4KICAgICAgPHNwYW4gY2xhc3M9ImNhcmQtbm90ZSIgaWQ9Im1haW5DaGFydE5vdGUiPjwvc3Bhbj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2hhcnQtYm94IHRhbGwiPjxjYW52YXMgaWQ9Im1haW5DaGFydCI+PC9jYW52YXM+PC9kaXY+CiAgPC9kaXY+CgogIDxkaXYgY2xhc3M9ImdyaWQtMiI+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgPGRpdiBjbGFzcz0iY2FyZC1oZWFkIj4KICAgICAgICA8aDMgY2xhc3M9ImNhcmQtdGl0bGUiPuC4peC4ueC4geC4hOC5ieC4suC4ouC4reC4lOC4guC4suC4ouC4quC4ueC4h+C4quC4uOC4lCAxNSDguK3guLHguJnguJTguLHguJo8L2gzPgogICAgICAgIDxzcGFuIGNsYXNzPSJjYXJkLW5vdGUiPuC4leC4peC4reC4lOC4iuC5iOC4p+C4h+C4guC5ieC4reC4oeC4ueC4peC4l+C4seC5ieC4h+C4q+C4oeC4lDwvc3Bhbj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImNoYXJ0LWJveCB0YWxsIj48Y2FudmFzIGlkPSJjdXN0Q2hhcnQiPjwvY2FudmFzPjwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgPGRpdiBjbGFzcz0iY2FyZC1oZWFkIj4KICAgICAgICA8aDMgY2xhc3M9ImNhcmQtdGl0bGUiPuC4quC4tOC4meC4hOC5ieC4suC4guC4suC4ouC4lOC4teC4quC4ueC4h+C4quC4uOC4lCAxNSDguK3guLHguJnguJTguLHguJo8L2gzPgogICAgICAgIDxzcGFuIGNsYXNzPSJjYXJkLW5vdGUiPuC4leC4suC4oeC4oeC4ueC4peC4hOC5iOC4suC4guC4suC4ojwvc3Bhbj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImNoYXJ0LWJveCB0YWxsIj48Y2FudmFzIGlkPSJwcm9kQ2hhcnQiPjwvY2FudmFzPjwvZGl2PgogICAgPC9kaXY+CiAgPC9kaXY+CgogIDxkaXYgY2xhc3M9ImdyaWQtMyI+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgPGRpdiBjbGFzcz0iY2FyZC1oZWFkIj4KICAgICAgICA8aDMgY2xhc3M9ImNhcmQtdGl0bGUiPuC4hOC5iOC4suC5g+C4iuC5ieC4iOC5iOC4suC4ouC4lOC4s+C5gOC4meC4tOC4meC4h+C4suC4mSDguYHguKLguIHguJXguLLguKHguJrguLHguI3guIrguLU8L2gzPgogICAgICAgIDxzcGFuIGNsYXNzPSJjYXJkLW5vdGUiPuC5hOC4oeC5iOC4o+C4p+C4oeC4leC5ieC4meC4l+C4uOC4meC4i+C4t+C5ieC4reC4quC4tOC4meC4hOC5ieC4siAoQ09HUyk8L3NwYW4+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJjaGFydC1ib3giPjxjYW52YXMgaWQ9ImV4cENoYXJ0Ij48L2NhbnZhcz48L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgIDxkaXYgY2xhc3M9ImNhcmQtaGVhZCI+CiAgICAgICAgPGgzIGNsYXNzPSJjYXJkLXRpdGxlIj7guKHguLnguKXguITguYjguLLguKrguJXguYfguK3guIHguITguIfguYDguKvguKXguLfguK08L2gzPgogICAgICAgIDxzcGFuIGNsYXNzPSJjYXJkLW5vdGUiPlRvcCAxNTwvc3Bhbj4KICAgICAgPC9kaXY+CiAgICAgIDx0YWJsZT4KICAgICAgICA8dGhlYWQ+PHRyPjx0aD7guKrguLTguJnguITguYnguLI8L3RoPjx0aCBzdHlsZT0idGV4dC1hbGlnbjpyaWdodCI+4LiE4LiH4LmA4Lir4Lil4Li34LitPC90aD48dGggc3R5bGU9InRleHQtYWxpZ246cmlnaHQiPuC4oeC4ueC4peC4hOC5iOC4sjwvdGg+PC90cj48L3RoZWFkPgogICAgICAgIDx0Ym9keSBpZD0ic3RvY2tUYWJsZSI+PC90Ym9keT4KICAgICAgPC90YWJsZT4KICAgIDwvZGl2PgogIDwvZGl2PgoKPC9kaXY+Cgo8ZGl2IGNsYXNzPSJmb290ZXIiPgogIDxwPjxiPuC4q+C4oeC4suC4ouC5gOC4q+C4leC4uOC4guC5ieC4reC4oeC4ueC4pTo8L2I+IOC4ouC4reC4lOC4guC4suC4oi/guIvguLfguYnguK0g4LiE4Liz4LiZ4Lin4LiT4LiI4Liy4LiB4Lih4Li54Lil4LiE4LmI4Liy4LiB4LmI4Lit4LiZIFZBVCAobmV0dmFsKSDCtyDguITguYjguLLguYPguIrguYnguIjguYjguLLguKLguJTguLPguYDguJnguLTguJnguIfguLLguJnguIHguKPguK3guIfguYDguInguJ7guLLguLDguJrguLHguI3guIrguLXguKvguKHguKfguJTguITguYjguLLguYPguIrguYnguIjguYjguLLguKIgKOC4geC4peC4uOC5iOC4oSA1KSDguYHguKXguLDguJXguLHguJTguKPguLLguKLguIHguLLguKMgIuC4i+C4t+C5ieC4rSIg4Lit4Lit4LiB4LmA4Lie4Li34LmI4Lit4LmE4Lih4LmI4LmD4Lir4LmJ4LiZ4Lix4Lia4LiL4LmJ4Liz4LiB4Lix4Lia4Lii4Lit4LiU4LiL4Li34LmJ4LitIMK3IOC4guC5ieC4reC4oeC4ueC4peC4ouC5ieC4reC4meC4q+C4peC4seC4h+C4guC4reC4h+C4ouC4reC4lOC4i+C4t+C5ieC4rS/guITguYjguLLguYPguIrguYnguIjguYjguLLguKIv4LmA4LiH4Li04LiZ4Lij4Lix4Lia4LmA4Lij4Li04LmI4Lih4Lia4Lix4LiZ4LiX4Li24LiB4LiV4Lix4LmJ4LiH4LmB4LiV4LmI4Lib4Li1IDIwMjDigJMyMDIxIOC5gOC4m+C5h+C4meC4leC5ieC4meC5hOC4myDguJvguLXguIHguYjguK3guJnguKvguJnguYnguLLguIjguLbguIfguYHguKrguJTguIfguYDguInguJ7guLLguLDguKLguK3guJTguILguLLguKIgwrcg4LiC4LmJ4Lit4Lih4Li54Lil4LiK4Li44LiU4LiZ4Li14LmJ4LmA4Lib4LmH4LiZ4LiC4LmJ4Lit4Lih4Li54LilIGV4cG9ydCDguJMg4LmA4Lin4Lil4Liy4LiX4Li14LmI4LiU4Li24LiHIOC5hOC4oeC5iOC5g+C4iuC5iCByZWFsLXRpbWU8L3A+CjwvZGl2PgoKPHNjcmlwdD4KY29uc3QgUkFXID0g"
TEMPLATE_AFTER_B64 = "OwoKY29uc3QgZm10ID0gKG4sIG9wdHM9e30pID0+IG5ldyBJbnRsLk51bWJlckZvcm1hdCgndGgtVEgnLCB7bWF4aW11bUZyYWN0aW9uRGlnaXRzOjAsIC4uLm9wdHN9KS5mb3JtYXQobnx8MCk7CmNvbnN0IGZtdENvbXBhY3QgPSAobikgPT4gewogIGNvbnN0IHYgPSBufHwwOwogIGNvbnN0IHNpZ24gPSB2PDAgPyAnLScgOiAnJzsKICBjb25zdCBhYnMgPSBNYXRoLmFicyh2KTsKICBpZihhYnM+PTFlNikgcmV0dXJuIHNpZ24rKGFicy8xZTYpLnRvRml4ZWQoMikrJ00nOwogIGlmKGFicz49MWUzKSByZXR1cm4gc2lnbisoYWJzLzFlMykudG9GaXhlZCgxKSsnSyc7CiAgcmV0dXJuIHNpZ24rYWJzLnRvRml4ZWQoMCk7Cn07Cgpjb25zdCBtb250aE5hbWVzVGggPSBbJ+C4oS7guIQuJywn4LiBLuC4ni4nLCfguKHguLUu4LiELicsJ+C5gOC4oS7guKIuJywn4LieLuC4hC4nLCfguKHguLQu4LiiLicsJ+C4gS7guIQuJywn4LiqLuC4hC4nLCfguIEu4LiiLicsJ+C4lS7guIQuJywn4LieLuC4oi4nLCfguJgu4LiELiddOwpjb25zdCBtZXRyaWNMYWJlbHMgPSB7CiAgc2FsZXNfYW1vdW50OifguKLguK3guJTguILguLLguKInLCBwdXJjaGFzZV9hbW91bnQ6J+C4ouC4reC4lOC4i+C4t+C5ieC4rScsIGdyb3NzX3Byb2ZpdDon4LiB4Liz4LmE4Lij4LiC4Lix4LmJ4LiZ4LiV4LmJ4LiZJywKICBuZXRfcHJvZml0OifguIHguLPguYTguKPguKrguLjguJfguJjguLQnLCBleHBlbnNlX2Ftb3VudDon4LiE4LmI4Liy4LmD4LiK4LmJ4LiI4LmI4Liy4LiiJywgcGF5bWVudF9hbW91bnQ6J+C5gOC4h+C4tOC4meC4o+C4seC4muC4iuC4s+C4o+C4sCcKfTsKY29uc3QgbWV0cmljQ29sb3JzID0gewogIHNhbGVzX2Ftb3VudDonI0ZGNkExMycsIHB1cmNoYXNlX2Ftb3VudDonIzNFNjY4MCcsIGdyb3NzX3Byb2ZpdDonIzJFOEI1NycsCiAgbmV0X3Byb2ZpdDonIzE2MjEyRScsIGV4cGVuc2VfYW1vdW50OicjQzE0NDNEJywgcGF5bWVudF9hbW91bnQ6JyM4RTZGQ0YnCn07CgovLyAtLS0tLS0tLS0tLS0tLS0tIEhlYWRlciByYW5nZSArIEtQSSAtLS0tLS0tLS0tLS0tLS0tCmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJy5iYWRnZS1yYW5nZScpLmlubmVySFRNTCA9CiAgYOC4guC5ieC4reC4oeC4ueC4pSA8Yj4ke1JBVy5rcGkuZGF0ZV9yYW5nZVswXX08L2I+IOKAlCA8Yj4ke1JBVy5rcGkuZGF0ZV9yYW5nZVsxXX08L2I+YDsKCmNvbnN0IGtwaUl0ZW1zID0gWwogIHtsYWJlbDon4Lii4Lit4LiU4LiC4Liy4Lii4Lij4Lin4LihJywgdmFsdWU6J+C4vycrZm10Q29tcGFjdChSQVcua3BpLnRvdGFsX3NhbGVzKSwgY2xzOidvcmFuZ2UnfSwKICB7bGFiZWw6J+C4ouC4reC4lOC4i+C4t+C5ieC4reC4o+C4p+C4oScsIHZhbHVlOifguL8nK2ZtdENvbXBhY3QoUkFXLmtwaS50b3RhbF9wdXJjaGFzZSl9LAogIHtsYWJlbDon4LiB4Liz4LmE4Lij4LiC4Lix4LmJ4LiZ4LiV4LmJ4LiZJywgdmFsdWU6J+C4vycrZm10Q29tcGFjdChSQVcua3BpLmdyb3NzX3Byb2ZpdCksIGNsczonZ3JlZW4nfSwKICB7bGFiZWw6J+C4hOC5iOC4suC5g+C4iuC5ieC4iOC5iOC4suC4ouC4lOC4s+C5gOC4meC4tOC4meC4h+C4suC4mScsIHZhbHVlOifguL8nK2ZtdENvbXBhY3QoUkFXLmtwaS50b3RhbF9vcGV4KX0sCiAge2xhYmVsOifguIHguLPguYTguKPguKrguLjguJfguJjguLQnLCB2YWx1ZTon4Li/JytmbXRDb21wYWN0KFJBVy5rcGkubmV0X3Byb2ZpdCksIGNsczogUkFXLmtwaS5uZXRfcHJvZml0Pj0wPydncmVlbic6J3JlZCd9LAogIHtsYWJlbDon4LmA4LiH4Li04LiZ4Lij4Lix4Lia4LiK4Liz4Lij4Liw4Liq4Liw4Liq4LihJywgdmFsdWU6J+C4vycrZm10Q29tcGFjdChSQVcua3BpLnRvdGFsX3BheW1lbnRfcmVjZWl2ZWQpfSwKICB7bGFiZWw6J+C4oeC4ueC4peC4hOC5iOC4suC4quC4leC5h+C4reC4geC4hOC4h+C5gOC4q+C4peC4t+C4rScsIHZhbHVlOifguL8nK2ZtdENvbXBhY3QoUkFXLmtwaS50b3RhbF9zdG9ja192YWx1ZSl9LAogIHtsYWJlbDon4Lil4Li54LiB4LiE4LmJ4Liy4LiX4Lix4LmJ4LiH4Lir4Lih4LiUJywgdmFsdWU6Zm10KFJBVy5rcGkuY3VzdG9tZXJfY291bnQpLCBzdWI6IFJBVy5rcGkubmVnYXRpdmVfc3RvY2tfY291bnQrJyDguKPguLLguKLguIHguLLguKPguKrguJXguYfguK3guIHguJXguLTguJTguKXguJonfSwKXTsKZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2twaVN0cmlwJykuaW5uZXJIVE1MID0ga3BpSXRlbXMubWFwKGsgPT4gYAogIDxkaXYgY2xhc3M9ImtwaS1jZWxsIj4KICAgIDxwIGNsYXNzPSJrcGktbGFiZWwiPiR7ay5sYWJlbH08L3A+CiAgICA8ZGl2IGNsYXNzPSJrcGktdmFsdWUgJHtrLmNsc3x8Jyd9Ij4ke2sudmFsdWV9PC9kaXY+CiAgICAke2suc3ViP2A8ZGl2IGNsYXNzPSJrcGktc3ViIj4ke2suc3VifTwvZGl2PmA6Jyd9CiAgPC9kaXY+YCkuam9pbignJyk7CgovLyAtLS0tLS0tLS0tLS0tLS0tIENvbnRyb2xzIHN0YXRlIC0tLS0tLS0tLS0tLS0tLS0KbGV0IG1vZGUgPSAnbW9udGhseSc7CmxldCBtZXRyaWMgPSAnc2FsZXNfYW1vdW50JzsKY29uc3QgeWVhcnMgPSBbLi4ubmV3IFNldChSQVcubW9udGhseS5tYXAobT0+bS5ZZWFyKSldLnNvcnQoKTsKbGV0IHNlbGVjdGVkWWVhciA9IHllYXJzW3llYXJzLmxlbmd0aC0yXSB8fCB5ZWFyc1t5ZWFycy5sZW5ndGgtMV07IC8vIGRlZmF1bHQgdG8gYSBmdWxsZXIgeWVhciAobm90IGN1cnJlbnQgcGFydGlhbCkKCmNvbnN0IHllYXJTZWxlY3QgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgneWVhclNlbGVjdCcpOwp5ZWFyU2VsZWN0LmlubmVySFRNTCA9IHllYXJzLm1hcCh5PT5gPG9wdGlvbiB2YWx1ZT0iJHt5fSI+JHt5fTwvb3B0aW9uPmApLmpvaW4oJycpOwp5ZWFyU2VsZWN0LnZhbHVlID0gc2VsZWN0ZWRZZWFyOwoKZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21ldHJpY1NlbGVjdCcpLmFkZEV2ZW50TGlzdGVuZXIoJ2NoYW5nZScsIGU9PnsgbWV0cmljID0gZS50YXJnZXQudmFsdWU7IHJlbmRlck1haW4oKTsgfSk7CnllYXJTZWxlY3QuYWRkRXZlbnRMaXN0ZW5lcignY2hhbmdlJywgZT0+eyBzZWxlY3RlZFllYXIgPSBlLnRhcmdldC52YWx1ZTsgcmVuZGVyTWFpbigpOyB9KTsKCmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoJyNtb2RlU2VnIGJ1dHRvbicpLmZvckVhY2goYnRuPT57CiAgYnRuLmFkZEV2ZW50TGlzdGVuZXIoJ2NsaWNrJywgKCk9PnsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoJyNtb2RlU2VnIGJ1dHRvbicpLmZvckVhY2goYj0+Yi5jbGFzc0xpc3QucmVtb3ZlKCdhY3RpdmUnKSk7CiAgICBidG4uY2xhc3NMaXN0LmFkZCgnYWN0aXZlJyk7CiAgICBtb2RlID0gYnRuLmRhdGFzZXQubW9kZTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd5ZWFyTGFiZWwnKS5zdHlsZS5kaXNwbGF5ID0gbW9kZT09PSd5ZWFybHknID8gJ25vbmUnIDogJ2lubGluZSc7CiAgICB5ZWFyU2VsZWN0LnN0eWxlLmRpc3BsYXkgPSBtb2RlPT09J3llYXJseScgPyAnbm9uZScgOiAnaW5saW5lLWJsb2NrJzsKICAgIHJlbmRlck1haW4oKTsKICB9KTsKfSk7CgovLyAtLS0tLS0tLS0tLS0tLS0tIE1haW4gY2hhcnQgLS0tLS0tLS0tLS0tLS0tLQpsZXQgbWFpbkNoYXJ0OwpmdW5jdGlvbiByZW5kZXJNYWluKCl7CiAgY29uc3QgY3R4ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW5DaGFydCcpOwogIGNvbnN0IGNvbG9yID0gbWV0cmljQ29sb3JzW21ldHJpY107CiAgbGV0IGNmZzsKCiAgaWYobW9kZSA9PT0gJ21vbnRobHknKXsKICAgIGNvbnN0IHJvd3MgPSBSQVcubW9udGhseS5maWx0ZXIobT0+bS5ZZWFyPT09c2VsZWN0ZWRZZWFyKS5zb3J0KChhLGIpPT5hLk1vbnRoTnVtLWIuTW9udGhOdW0pOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW5DaGFydFRpdGxlJykudGV4dENvbnRlbnQgPSBg4LmB4LiZ4Lin4LmC4LiZ4LmJ4Lih4Lij4Liy4Lii4LmA4LiU4Li34Lit4LiZIOKAlCAke21ldHJpY0xhYmVsc1ttZXRyaWNdfSAo4Lib4Li1ICR7c2VsZWN0ZWRZZWFyfSlgOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW5DaGFydE5vdGUnKS50ZXh0Q29udGVudCA9IGAke3Jvd3MubGVuZ3RofSDguYDguJTguLfguK3guJlgOwogICAgY2ZnID0gewogICAgICB0eXBlOidiYXInLAogICAgICBkYXRhOnsgbGFiZWxzOiByb3dzLm1hcChyPT5tb250aE5hbWVzVGhbci5Nb250aE51bS0xXSksCiAgICAgICAgZGF0YXNldHM6W3tsYWJlbDptZXRyaWNMYWJlbHNbbWV0cmljXSwgZGF0YTogcm93cy5tYXAocj0+clttZXRyaWNdKSwgYmFja2dyb3VuZENvbG9yOiBjb2xvciwgYm9yZGVyUmFkaXVzOjQsIG1heEJhclRoaWNrbmVzczozNH1dfSwKICAgICAgb3B0aW9uczogYmFzZU9wdGlvbnMoZmFsc2UpCiAgICB9OwogIH0gZWxzZSBpZihtb2RlID09PSAneWVhcmx5Jyl7CiAgICBjb25zdCByb3dzID0gUkFXLnllYXJseS5zbGljZSgpLnNvcnQoKGEsYik9PmEuWWVhci5sb2NhbGVDb21wYXJlKGIuWWVhcikpOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW5DaGFydFRpdGxlJykudGV4dENvbnRlbnQgPSBg4LmA4Lib4Lij4Li14Lii4Lia4LmA4LiX4Li14Lii4Lia4Lij4Liy4Lii4Lib4Li1IOKAlCAke21ldHJpY0xhYmVsc1ttZXRyaWNdfWA7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbWFpbkNoYXJ0Tm90ZScpLnRleHRDb250ZW50ID0gYCR7cm93c1swXS5ZZWFyfSDigJMgJHtyb3dzW3Jvd3MubGVuZ3RoLTFdLlllYXJ9YDsKICAgIGNmZyA9IHsKICAgICAgdHlwZTonYmFyJywKICAgICAgZGF0YTp7IGxhYmVsczogcm93cy5tYXAocj0+ci5ZZWFyKSwKICAgICAgICBkYXRhc2V0czpbe2xhYmVsOm1ldHJpY0xhYmVsc1ttZXRyaWNdLCBkYXRhOiByb3dzLm1hcChyPT5yW21ldHJpY10pLCBiYWNrZ3JvdW5kQ29sb3I6IGNvbG9yLCBib3JkZXJSYWRpdXM6NCwgbWF4QmFyVGhpY2tuZXNzOjQ4fV19LAogICAgICBvcHRpb25zOiBiYXNlT3B0aW9ucyhmYWxzZSkKICAgIH07CiAgfSBlbHNlIHsgLy8geW95CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbWFpbkNoYXJ0VGl0bGUnKS50ZXh0Q29udGVudCA9IGDguYDguJfguLXguKLguJrguJvguLXguJXguYjguK3guJvguLUgKFlvWSkg4oCUICR7bWV0cmljTGFiZWxzW21ldHJpY119IOC4o+C4suC4ouC5gOC4lOC4t+C4reC4mWA7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbWFpbkNoYXJ0Tm90ZScpLnRleHRDb250ZW50ID0gYCR7eWVhcnMubGVuZ3RofSDguJvguLVgOwogICAgY29uc3QgcGFsZXR0ZSA9IFsnI0RDRTFFNicsJyNCOEMyQ0InLCcjOEZBMEFGJywnIzNFNjY4MCcsJyMxNjIxMkUnLCcjRkY2QTEzJywnI0MxNDQzRCcsJyMyRThCNTcnLCcjOEU2RkNGJ107CiAgICBjb25zdCBkYXRhc2V0cyA9IHllYXJzLm1hcCgoeSxpKT0+ewogICAgICBjb25zdCByb3dzID0gUkFXLm1vbnRobHkuZmlsdGVyKG09Pm0uWWVhcj09PXkpLnNvcnQoKGEsYik9PmEuTW9udGhOdW0tYi5Nb250aE51bSk7CiAgICAgIGNvbnN0IGFyciA9IG5ldyBBcnJheSgxMikuZmlsbChudWxsKTsKICAgICAgcm93cy5mb3JFYWNoKHI9PnsgYXJyW3IuTW9udGhOdW0tMV0gPSByW21ldHJpY107IH0pOwogICAgICBjb25zdCBpc0xhdGVzdCA9IGk9PT15ZWFycy5sZW5ndGgtMTsKICAgICAgcmV0dXJuIHsKICAgICAgICBsYWJlbDogeSwgZGF0YTogYXJyLAogICAgICAgIGJvcmRlckNvbG9yOiBpc0xhdGVzdCA/ICcjRkY2QTEzJyA6IHBhbGV0dGVbaSVwYWxldHRlLmxlbmd0aF0sCiAgICAgICAgYmFja2dyb3VuZENvbG9yOiAndHJhbnNwYXJlbnQnLAogICAgICAgIGJvcmRlcldpZHRoOiBpc0xhdGVzdCA/IDMgOiAxLjUsCiAgICAgICAgcG9pbnRSYWRpdXM6IDAsIHRlbnNpb246LjMsCiAgICAgICAgc3BhbkdhcHM6dHJ1ZSwKICAgICAgfTsKICAgIH0pOwogICAgY2ZnID0gewogICAgICB0eXBlOidsaW5lJywKICAgICAgZGF0YTp7IGxhYmVsczogbW9udGhOYW1lc1RoLCBkYXRhc2V0cyB9LAogICAgICBvcHRpb25zOiBiYXNlT3B0aW9ucyh0cnVlKQogICAgfTsKICB9CgogIGlmKG1haW5DaGFydCkgbWFpbkNoYXJ0LmRlc3Ryb3koKTsKICBtYWluQ2hhcnQgPSBuZXcgQ2hhcnQoY3R4LCBjZmcpOwp9CgpmdW5jdGlvbiBiYXNlT3B0aW9ucyhzaG93TGVnZW5kKXsKICByZXR1cm4gewogICAgcmVzcG9uc2l2ZTp0cnVlLCBtYWludGFpbkFzcGVjdFJhdGlvOmZhbHNlLAogICAgaW50ZXJhY3Rpb246e21vZGU6J2luZGV4JywgaW50ZXJzZWN0OmZhbHNlfSwKICAgIHBsdWdpbnM6ewogICAgICBsZWdlbmQ6eyBkaXNwbGF5OiBzaG93TGVnZW5kLCBwb3NpdGlvbjonYm90dG9tJywgbGFiZWxzOnsgYm94V2lkdGg6MTAsIGJveEhlaWdodDoxMCwgZm9udDp7ZmFtaWx5OiInSUJNIFBsZXggU2FucyBUaGFpJyIsIHNpemU6MTF9LCBjb2xvcjonIzY0NzQ4QScgfSB9LAogICAgICB0b29sdGlwOnsKICAgICAgICBiYWNrZ3JvdW5kQ29sb3I6JyMxNjIxMkUnLCB0aXRsZUZvbnQ6e2ZhbWlseToiJ0lCTSBQbGV4IE1vbm8nIn0sIGJvZHlGb250OntmYW1pbHk6IidJQk0gUGxleCBNb25vJyIsIHNpemU6MTJ9LAogICAgICAgIHBhZGRpbmc6MTAsIGNvcm5lclJhZGl1czo2LAogICAgICAgIGNhbGxiYWNrczp7IGxhYmVsOiAoYyk9PiBgICR7Yy5kYXRhc2V0LmxhYmVsfTog4Li/JHtmbXQoYy5wYXJzZWQueSl9YCB9CiAgICAgIH0KICAgIH0sCiAgICBzY2FsZXM6ewogICAgICB4OnsgZ3JpZDp7ZGlzcGxheTpmYWxzZX0sIHRpY2tzOntmb250OntmYW1pbHk6IidJQk0gUGxleCBTYW5zIFRoYWknIiwgc2l6ZToxMX0sIGNvbG9yOicjNjQ3NDhBJ30gfSwKICAgICAgeTp7IGdyaWQ6e2NvbG9yOicjRUVGMUYzJ30sIHRpY2tzOnsgZm9udDp7ZmFtaWx5OiInSUJNIFBsZXggTW9ubyciLCBzaXplOjEwLjV9LCBjb2xvcjonIzg1OTJBMCcsIGNhbGxiYWNrOih2KT0+Zm10Q29tcGFjdCh2KSB9IH0KICAgIH0KICB9Owp9CgovLyAtLS0tLS0tLS0tLS0tLS0tIEN1c3RvbWVyIGNoYXJ0IC0tLS0tLS0tLS0tLS0tLS0KbmV3IENoYXJ0KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdjdXN0Q2hhcnQnKSwgewogIHR5cGU6J2JhcicsCiAgZGF0YTp7CiAgICBsYWJlbHM6IFJBVy50b3BfY3VzdG9tZXJzLm1hcChjPT5jLmN1c3RvbWVyLmxlbmd0aD4yNiA/IGMuY3VzdG9tZXIuc2xpY2UoMCwyNikrJ+KApicgOiBjLmN1c3RvbWVyKSwKICAgIGRhdGFzZXRzOlt7IGRhdGE6IFJBVy50b3BfY3VzdG9tZXJzLm1hcChjPT5jLnRvdGFsX3NhbGVzKSwgYmFja2dyb3VuZENvbG9yOicjM0U2NjgwJywgYm9yZGVyUmFkaXVzOjQgfV0KICB9LAogIG9wdGlvbnM6ewogICAgaW5kZXhBeGlzOid5JywgcmVzcG9uc2l2ZTp0cnVlLCBtYWludGFpbkFzcGVjdFJhdGlvOmZhbHNlLAogICAgcGx1Z2luczp7IGxlZ2VuZDp7ZGlzcGxheTpmYWxzZX0sIHRvb2x0aXA6eyBjYWxsYmFja3M6e2xhYmVsOihjKT0+YCDguL8ke2ZtdChjLnBhcnNlZC54KX1gfSwgYmFja2dyb3VuZENvbG9yOicjMTYyMTJFJywgYm9keUZvbnQ6e2ZhbWlseToiJ0lCTSBQbGV4IE1vbm8nIn0gfSB9LAogICAgc2NhbGVzOnsKICAgICAgeDp7IGdyaWQ6e2NvbG9yOicjRUVGMUYzJ30sIHRpY2tzOntmb250OntmYW1pbHk6IidJQk0gUGxleCBNb25vJyIsIHNpemU6MTB9LCBjb2xvcjonIzg1OTJBMCcsIGNhbGxiYWNrOih2KT0+Zm10Q29tcGFjdCh2KX0gfSwKICAgICAgeTp7IGdyaWQ6e2Rpc3BsYXk6ZmFsc2V9LCB0aWNrczp7Zm9udDp7ZmFtaWx5OiInSUJNIFBsZXggU2FucyBUaGFpJyIsIHNpemU6MTAuNX0sIGNvbG9yOicjM0E0NTUyJ30gfQogICAgfQogIH0KfSk7CgovLyAtLS0tLS0tLS0tLS0tLS0tIFByb2R1Y3QgY2hhcnQgLS0tLS0tLS0tLS0tLS0tLQpuZXcgQ2hhcnQoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Byb2RDaGFydCcpLCB7CiAgdHlwZTonYmFyJywKICBkYXRhOnsKICAgIGxhYmVsczogUkFXLnRvcF9wcm9kdWN0cy5tYXAocD0+cC5wcm9kdWN0Lmxlbmd0aD4yNiA/IHAucHJvZHVjdC5zbGljZSgwLDI2KSsn4oCmJyA6IHAucHJvZHVjdCksCiAgICBkYXRhc2V0czpbeyBkYXRhOiBSQVcudG9wX3Byb2R1Y3RzLm1hcChwPT5wLnRvdGFsX2Ftb3VudCksIGJhY2tncm91bmRDb2xvcjonI0ZGNkExMycsIGJvcmRlclJhZGl1czo0IH1dCiAgfSwKICBvcHRpb25zOnsKICAgIGluZGV4QXhpczoneScsIHJlc3BvbnNpdmU6dHJ1ZSwgbWFpbnRhaW5Bc3BlY3RSYXRpbzpmYWxzZSwKICAgIHBsdWdpbnM6eyBsZWdlbmQ6e2Rpc3BsYXk6ZmFsc2V9LCB0b29sdGlwOnsgY2FsbGJhY2tzOntsYWJlbDooYyk9PmAg4Li/JHtmbXQoYy5wYXJzZWQueCl9YH0sIGJhY2tncm91bmRDb2xvcjonIzE2MjEyRScsIGJvZHlGb250OntmYW1pbHk6IidJQk0gUGxleCBNb25vJyJ9IH0gfSwKICAgIHNjYWxlczp7CiAgICAgIHg6eyBncmlkOntjb2xvcjonI0VFRjFGMyd9LCB0aWNrczp7Zm9udDp7ZmFtaWx5OiInSUJNIFBsZXggTW9ubyciLCBzaXplOjEwfSwgY29sb3I6JyM4NTkyQTAnLCBjYWxsYmFjazoodik9PmZtdENvbXBhY3Qodil9IH0sCiAgICAgIHk6eyBncmlkOntkaXNwbGF5OmZhbHNlfSwgdGlja3M6e2ZvbnQ6e2ZhbWlseToiJ0lCTSBQbGV4IFNhbnMgVGhhaSciLCBzaXplOjEwLjV9LCBjb2xvcjonIzNBNDU1Mid9IH0KICAgIH0KICB9Cn0pOwoKLy8gLS0tLS0tLS0tLS0tLS0tLSBFeHBlbnNlIGNoYXJ0IC0tLS0tLS0tLS0tLS0tLS0KbmV3IENoYXJ0KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleHBDaGFydCcpLCB7CiAgdHlwZTonYmFyJywKICBkYXRhOnsKICAgIGxhYmVsczogUkFXLmV4cGVuc2VfYnJlYWtkb3duLm1hcChlPT5lLmFjY291bnQubGVuZ3RoPjE4ID8gZS5hY2NvdW50LnNsaWNlKDAsMTgpKyfigKYnIDogZS5hY2NvdW50KSwKICAgIGRhdGFzZXRzOlt7IGRhdGE6IFJBVy5leHBlbnNlX2JyZWFrZG93bi5tYXAoZT0+ZS50b3RhbF9hbW91bnQpLCBiYWNrZ3JvdW5kQ29sb3I6JyNDMTQ0M0QnLCBib3JkZXJSYWRpdXM6NCB9XQogIH0sCiAgb3B0aW9uczp7CiAgICBpbmRleEF4aXM6J3knLCByZXNwb25zaXZlOnRydWUsIG1haW50YWluQXNwZWN0UmF0aW86ZmFsc2UsCiAgICBwbHVnaW5zOnsgbGVnZW5kOntkaXNwbGF5OmZhbHNlfSwgdG9vbHRpcDp7IGNhbGxiYWNrczp7bGFiZWw6KGMpPT5gIOC4vyR7Zm10KGMucGFyc2VkLngpfWB9LCBiYWNrZ3JvdW5kQ29sb3I6JyMxNjIxMkUnLCBib2R5Rm9udDp7ZmFtaWx5OiInSUJNIFBsZXggTW9ubycifSB9IH0sCiAgICBzY2FsZXM6ewogICAgICB4OnsgZ3JpZDp7Y29sb3I6JyNFRUYxRjMnfSwgdGlja3M6e2ZvbnQ6e2ZhbWlseToiJ0lCTSBQbGV4IE1vbm8nIiwgc2l6ZToxMH0sIGNvbG9yOicjODU5MkEwJywgY2FsbGJhY2s6KHYpPT5mbXRDb21wYWN0KHYpfSB9LAogICAgICB5OnsgZ3JpZDp7ZGlzcGxheTpmYWxzZX0sIHRpY2tzOntmb250OntmYW1pbHk6IidJQk0gUGxleCBTYW5zIFRoYWknIiwgc2l6ZToxMH0gfSB9CiAgICB9CiAgfQp9KTsKCi8vIC0tLS0tLS0tLS0tLS0tLS0gU3RvY2sgdGFibGUgLS0tLS0tLS0tLS0tLS0tLQpkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RvY2tUYWJsZScpLmlubmVySFRNTCA9IFJBVy50b3Bfc3RvY2tfdmFsdWUubWFwKChzLGkpPT5gCiAgPHRyPgogICAgPHRkPjxzcGFuIGNsYXNzPSJyYW5rIj4ke2krMX08L3NwYW4+JHtzLnByb2R1Y3R9PC90ZD4KICAgIDx0ZCBjbGFzcz0ibnVtIj4ke2ZtdChzLnF0eV9yZW1haW5pbmcpfSR7cy5xdHlfcmVtYWluaW5nPDA/JyA8c3BhbiBjbGFzcz0iZmxhZy1uZWciPuKXj+C4leC4tOC4lOC4peC4mjwvc3Bhbj4nOicnfTwvdGQ+CiAgICA8dGQgY2xhc3M9Im51bSI+4Li/JHtmbXQocy52YWx1ZV9yZW1haW5pbmcpfTwvdGQ+CiAgPC90cj5gKS5qb2luKCcnKTsKCnJlbmRlck1haW4oKTsKPC9zY3JpcHQ+CjwvYm9keT4KPC9odG1sPgo="

def run_git_command(args):
    import subprocess
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def auto_commit_and_push():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    git_dir = os.path.join(repo_root, ".git")
    if not os.path.isdir(git_dir):
        print("Git repository not initialized; skipping auto-commit and push.")
        return

    name = os.environ.get("GIT_USER_NAME", "Athicha Dashboard Bot")
    email = os.environ.get("GIT_USER_EMAIL", "athicha-dashboard@local")

    current_name = run_git_command(["config", "--get", "user.name"])[1]
    if not current_name:
        run_git_command(["config", "user.name", name])

    current_email = run_git_command(["config", "--get", "user.email"])[1]
    if not current_email:
        run_git_command(["config", "user.email", email])

    run_git_command(["add", "-A"])

    code, stdout, stderr = run_git_command(["diff", "--cached", "--quiet"])
    if code == 0:
        print("No dashboard changes to commit; skipping Git commit.")
        return

    code, stdout, stderr = run_git_command(["commit", "-m", "Nightly dashboard refresh"])
    if code != 0 and "nothing to commit" in (stderr + stdout).lower():
        print("Git reported no changes to commit; skipping push.")
        return
    if code != 0:
        print("Git commit failed:")
        print(stderr or stdout)
        return

    print("Pushing dashboard update to GitHub...")
    code, stdout, stderr = run_git_command(["push", "origin", "main"])
    if code != 0:
        print("Git push failed:")
        print(stderr or stdout)
    else:
        print("Dashboard pushed successfully.")


if __name__ == "__main__":
    main()
    auto_commit_and_push()

# ---------------------------------------------------------------------------
# README: automation & publishing
# ---------------------------------------------------------------------------
#
# 1) One-time setup
#    - Create a GitHub repo (e.g. "business-dashboard"), public.
#    - Clone it to a folder on the machine that has SQL Server access, e.g.
#      C:\dashboard-repo
#    - Copy this script into that folder.
#    - In GitHub: Settings -> Pages -> Source: "main" branch, folder "/ (root)".
#      GitHub gives you a URL like https://<username>.github.io/business-dashboard/
#
# 2) Nightly refresh (Windows Task Scheduler)
#    Create a scheduled task that runs a .bat file like this nightly:
#
#      cd /d C:\dashboard-repo
#      set SQLSERVER_HOST=your-server
#      set SQLSERVER_DB=your-db
#      set SQLSERVER_USER=your-user
#      set SQLSERVER_PASSWORD=your-password
#      python generate_dashboard.py
#      git add index.html dashboard_data.json
#      git commit -m "Nightly dashboard refresh"
#      git push
#
#    (First time, run `git config` for user.name/user.email, and set up a
#    GitHub Personal Access Token or SSH key so `git push` works without a
#    login prompt.)
#
# 3) Security notes
#    - Never commit the SQLSERVER_PASSWORD into the .bat file if the repo is
#      shared; keep the .bat file itself outside git (it's just a local
#      scheduled task, not something you push).
#    - A public GitHub Pages site has NO login -- anyone with the link can
#      see the numbers (revenue, top customers, etc). If that is not
#      acceptable, use a private repo + GitHub Pro/Team (Pages supports
#      private repos on paid plans), or host on Netlify/Cloudflare Pages
#      with password protection instead.
