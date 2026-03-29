from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
import pandas as pd
import os
import uvicorn
import socket
import shutil
import threading
import json
import sqlite3
import urllib.request
import urllib.parse

app = FastAPI()

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    env_path = os.getenv(filename)
    if env_path: return env_path
    return os.path.join(BASE_DIR, filename)

EXCEL_CAPITAL = get_path("사용자정의주문현황_수도권.xlsx")
EXCEL_PROVINCE = get_path("사용자정의주문현황_지방.xlsx")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(BASE_DIR, "picking_data.db")

# Supabase Configuration (PRIMARY storage)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bgegrgvhstglipgikmtl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_5VaQhNR9rcchFxU5CHtfxA_NwUaKA0S")


# ===== Supabase (Primary Storage) =====
def supabase_request(method, path, body=None, params=None):
    """Make a REST API request to Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

def supabase_count(region):
    """Get row count for a region from Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/picking_records?select=count&region=eq.{region}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "count=exact"
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        count_str = resp.headers.get("Content-Range", "").split("/")[-1]
        return int(count_str) if count_str and count_str != "*" else 0

def supabase_save_records(df, region, ddate):
    """Save records to Supabase (replaces old data for the region)."""
    records = []
    for _, row in df.iterrows():
        records.append({
            "region": region,
            "store": str(row['store']),
            "product": str(row['product']),
            "qty": float(row['qty']),
            "vehicle": str(row['vehicle']),
            "delivery_date": str(ddate)
        })
    
    # Delete old records for this region
    supabase_request("DELETE", "picking_records", params={"region": f"eq.{region}"})
    
    # Insert in chunks of 200
    for i in range(0, len(records), 200):
        chunk = records[i:i + 200]
        supabase_request("POST", "picking_records", body=chunk)
    
    # Update region_meta
    supabase_request("DELETE", "region_meta", params={"region": f"eq.{region}"})
    supabase_request("POST", "region_meta", body=[{
        "region": region,
        "delivery_date": str(ddate)
    }])
    
    print(f"[Supabase] Saved {len(records)} records for {region} (delivery_date: {ddate})")

def supabase_get_products(region, sub_region=""):
    """Get aggregated product list from Supabase."""
    params = {"region": f"eq.{region}", "select": "product,qty"}
    
    if sub_region and sub_region != "전체":
        params["vehicle"] = f"like.*{sub_region}*"
    
    # Fetch all records (paginated)
    all_records = []
    offset = 0
    limit = 1000
    while True:
        p = dict(params)
        p["limit"] = str(limit)
        p["offset"] = str(offset)
        url = f"{SUPABASE_URL}/rest/v1/picking_records"
        query_parts = []
        for k, v in p.items():
            query_parts.append(f"{k}={urllib.parse.quote(str(v), safe='.*')}")
        full_url = url + "?" + "&".join(query_parts)
        
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        req = urllib.request.Request(full_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            batch = json.loads(resp.read().decode("utf-8"))
            all_records.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
    
    # Aggregate by product
    product_totals = {}
    for r in all_records:
        name = r["product"]
        product_totals[name] = product_totals.get(name, 0) + r["qty"]
    
    return [{"product": k, "qty": v} for k, v in product_totals.items()]

def supabase_get_details(product_name, region, sub_region=""):
    """Get detail records for a specific product from Supabase."""
    params = {
        "region": f"eq.{region}",
        "product": f"eq.{product_name}",
        "select": "store,qty,vehicle,delivery_date",
        "order": "qty.asc,vehicle.asc"
    }
    
    if sub_region and sub_region != "전체":
        params["vehicle"] = f"like.*{sub_region}*"
    
    url = f"{SUPABASE_URL}/rest/v1/picking_records"
    query_parts = []
    for k, v in params.items():
        query_parts.append(f"{k}={urllib.parse.quote(str(v), safe='.*')}")
    full_url = url + "?" + "&".join(query_parts)
    
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    req = urllib.request.Request(full_url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

def supabase_get_region_info(region):
    """Get region metadata from Supabase."""
    # Get row count
    row_count = supabase_count(region)
    
    # Get delivery_date from region_meta
    url = f"{SUPABASE_URL}/rest/v1/region_meta?region=eq.{region}&select=delivery_date,updated_at"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        meta_list = json.loads(resp.read().decode("utf-8"))
    
    if meta_list:
        return {
            "rows": row_count,
            "delivery_date": meta_list[0].get("delivery_date", "N/A"),
            "updated_at": meta_list[0].get("updated_at", "N/A")
        }
    return {"rows": row_count, "delivery_date": "N/A", "updated_at": "N/A"}

def supabase_delete_records(region):
    """Delete all records for a region from Supabase."""
    supabase_request("DELETE", "picking_records", params={"region": f"eq.{region}"})
    supabase_request("DELETE", "region_meta", params={"region": f"eq.{region}"})
    print(f"[Supabase] Deleted all records for {region}")


# ===== SQLite Local Cache (Fallback) =====
def init_db():
    """Create the local SQLite database and tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS picking_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region TEXT NOT NULL,
            store TEXT NOT NULL,
            product TEXT NOT NULL,
            qty REAL NOT NULL,
            vehicle TEXT DEFAULT '',
            delivery_date TEXT DEFAULT 'N/A',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS region_meta (
            region TEXT PRIMARY KEY,
            delivery_date TEXT DEFAULT 'N/A',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_region ON picking_records(region)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_product ON picking_records(product)")
    conn.commit()
    conn.close()

def db_save_records(df, region, ddate):
    """Save records to the local SQLite cache."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM picking_records WHERE region = ?", (region,))
    records = []
    for _, row in df.iterrows():
        records.append((
            region, str(row['store']), str(row['product']),
            float(row['qty']), str(row['vehicle']), str(ddate)
        ))
    c.executemany(
        "INSERT INTO picking_records (region, store, product, qty, vehicle, delivery_date) VALUES (?, ?, ?, ?, ?, ?)",
        records
    )
    c.execute(
        "INSERT OR REPLACE INTO region_meta (region, delivery_date, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (region, str(ddate))
    )
    conn.commit()
    conn.close()
    print(f"[SQLite] Cached {len(records)} records for {region}")

def db_get_products(region, sub_region=""):
    """(Fallback) Get aggregated product list from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    if sub_region and sub_region != "전체":
        query = "SELECT product, SUM(qty) as qty FROM picking_records WHERE region = ? AND vehicle LIKE ? GROUP BY product"
        df = pd.read_sql_query(query, conn, params=(region, f"%{sub_region}%"))
    else:
        query = "SELECT product, SUM(qty) as qty FROM picking_records WHERE region = ? GROUP BY product"
        df = pd.read_sql_query(query, conn, params=(region,))
    conn.close()
    return df.to_dict(orient='records') if not df.empty else []

def db_get_details(product_name, region, sub_region=""):
    """(Fallback) Get detail records from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    if sub_region and sub_region != "전체":
        query = "SELECT store, qty, vehicle, delivery_date FROM picking_records WHERE region = ? AND product = ? AND vehicle LIKE ? ORDER BY qty ASC, vehicle ASC"
        df = pd.read_sql_query(query, conn, params=(region, product_name, f"%{sub_region}%"))
    else:
        query = "SELECT store, qty, vehicle, delivery_date FROM picking_records WHERE region = ? AND product = ? ORDER BY qty ASC, vehicle ASC"
        df = pd.read_sql_query(query, conn, params=(region, product_name))
    conn.close()
    return df.fillna("").to_dict(orient='records') if not df.empty else []

def db_get_region_info(region):
    """(Fallback) Get region metadata from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM picking_records WHERE region = ?", (region,))
    row_count = c.fetchone()[0]
    c.execute("SELECT delivery_date, updated_at FROM region_meta WHERE region = ?", (region,))
    meta = c.fetchone()
    conn.close()
    if meta:
        return {"rows": row_count, "delivery_date": meta[0], "updated_at": meta[1]}
    return {"rows": 0, "delivery_date": "N/A", "updated_at": "N/A"}


# ===== Smart Data Access (Supabase first, SQLite fallback) =====
def safe_get_products(region, sub_region=""):
    """Try Supabase first, fall back to SQLite on error."""
    try:
        return supabase_get_products(region, sub_region)
    except Exception as e:
        print(f"[Fallback] Supabase failed for products ({e}), using SQLite")
        return db_get_products(region, sub_region)

def safe_get_details(product_name, region, sub_region=""):
    """Try Supabase first, fall back to SQLite on error."""
    try:
        return supabase_get_details(product_name, region, sub_region)
    except Exception as e:
        print(f"[Fallback] Supabase failed for details ({e}), using SQLite")
        return db_get_details(product_name, region, sub_region)

def safe_get_region_info(region):
    """Try Supabase first, fall back to SQLite on error."""
    try:
        return supabase_get_region_info(region)
    except Exception as e:
        print(f"[Fallback] Supabase failed for info ({e}), using SQLite")
        return db_get_region_info(region)


# ===== Utility =====
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_file_mtime(file_path):
    if os.path.exists(file_path):
        mtime = os.path.getmtime(file_path)
        from datetime import datetime
        return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    return "N/A"


# ===== Data Loading =====
def load_excel_data(region="capital"):
    """Load data from Excel file and save to BOTH Supabase (primary) and SQLite (cache)."""
    file_path = EXCEL_CAPITAL if region == "capital" else EXCEL_PROVINCE
    
    if not os.path.exists(file_path):
        print(f"[Load] Excel file not found: {file_path}")
        return 0
    
    try:
        print(f"[Load] Reading Excel: {file_path}")
        df = pd.read_excel(file_path, header=1)
        
        # Extract delivery date from column index 25
        ddate = "N/A"
        if len(df) > 0:
            raw_date = str(df.iloc[0, 25])
            if "." in raw_date: raw_date = raw_date.split(".")[0]
            if len(raw_date) == 8:
                ddate = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            else:
                ddate = raw_date

        # Create a simplified dataframe
        processed_df = pd.DataFrame({
            'store': df.iloc[:, 1],
            'product': df.iloc[:, 12],
            'qty': df.iloc[:, 30],
            'vehicle': df.iloc[:, 44]
        })
        
        # Clean data
        processed_df = processed_df.dropna(subset=['product', 'store'])
        processed_df['vehicle'] = processed_df['vehicle'].fillna('')
        processed_df['qty'] = pd.to_numeric(processed_df['qty'], errors='coerce').fillna(0)
        
        # Convert quantity to Box (divide by 20) for specific meat products
        mask = processed_df['product'].str.contains('삼겹양지|목심|설도', na=False, regex=True)
        processed_df.loc[mask, 'qty'] = processed_df.loc[mask, 'qty'] / 20
        
        # Filter out 0 or negative quantities
        processed_df = processed_df[processed_df['qty'] > 0]

        # Aggregate by store and product (sum quantities for same store+product combinations)
        processed_df = processed_df.groupby(['store', 'product'], as_index=False).agg({
            'qty': 'sum',
            'vehicle': lambda x: ', '.join(x.dropna().astype(str).unique())  # Combine vehicle info if different
        })

        row_count = len(processed_df)
        print(f"[Load] Processed {row_count} rows for {region}")
        
        # Save to Supabase (PRIMARY - persistent!)
        try:
            supabase_save_records(processed_df, region, ddate)
        except Exception as e:
            print(f"[Load] Supabase save failed: {e} - data saved to SQLite only")
        
        # Save to local SQLite (CACHE)
        db_save_records(processed_df, region, ddate)
        
        return row_count
    except Exception as e:
        print(f"[Load] Error loading Excel for {region}: {e}")
        raise e


# ===== Startup =====
@app.on_event("startup")
async def startup_event():
    # Initialize local SQLite cache
    init_db()
    
    # Check Supabase connectivity and data
    try:
        capital_count = supabase_count("capital")
        province_count = supabase_count("province")
        print(f"[Startup] Supabase data - Capital: {capital_count} rows, Province: {province_count} rows")
        
        # If Supabase has data but SQLite doesn't, restore to local cache
        capital_local = db_get_region_info("capital")
        province_local = db_get_region_info("province")
        
        if capital_count > 0 and capital_local["rows"] == 0:
            print("[Startup] Restoring capital data from Supabase to local cache...")
            _restore_from_supabase("capital")
        
        if province_count > 0 and province_local["rows"] == 0:
            print("[Startup] Restoring province data from Supabase to local cache...")
            _restore_from_supabase("province")
            
    except Exception as e:
        print(f"[Startup] Supabase check failed: {e} - using SQLite fallback")
        capital_local = db_get_region_info("capital")
        province_local = db_get_region_info("province")
        print(f"[Startup] SQLite data - Capital: {capital_local['rows']} rows, Province: {province_local['rows']} rows")
    
    # Load from Excel if no data anywhere
    capital_info = safe_get_region_info("capital")
    if capital_info["rows"] == 0 and os.path.exists(EXCEL_CAPITAL):
        print("[Startup] No data found, loading from Excel (capital)...")
        load_excel_data("capital")
    
    province_info = safe_get_region_info("province")
    if province_info["rows"] == 0 and os.path.exists(EXCEL_PROVINCE):
        print("[Startup] No data found, loading from Excel (province)...")
        load_excel_data("province")

def _restore_from_supabase(region):
    """Fetch all records from Supabase and cache to local SQLite."""
    try:
        all_records = []
        offset = 0
        limit = 1000
        while True:
            url = f"{SUPABASE_URL}/rest/v1/picking_records?region=eq.{region}&select=store,product,qty,vehicle,delivery_date&limit={limit}&offset={offset}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
                all_records.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
        
        if not all_records:
            return
        
        ddate = all_records[0].get("delivery_date", "N/A")
        df = pd.DataFrame(all_records)
        db_save_records(df, region, ddate)
        print(f"[Restore] Cached {len(all_records)} records from Supabase to SQLite for {region}")
    except Exception as e:
        print(f"[Restore] Failed to restore {region}: {e}")


# ===== API Endpoints =====
@app.post("/api/upload")
async def upload_file(region: str = "capital", file: UploadFile = File(...)):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="엑셀 파일(.xlsx, .xls)만 업로드 가능합니다.")
    
    if region not in ["capital", "province"]:
        raise HTTPException(status_code=400, detail="알 수 없는 지역입니다.")
        
    file_path = EXCEL_CAPITAL if region == "capital" else EXCEL_PROVINCE
    
    try:
        print(f"[Upload] Receiving file for {region}: {file.filename}")
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        rows_loaded = load_excel_data(region)
        print(f"[Upload] Success. {rows_loaded} rows saved for {region}.")
        return {"filename": file.filename, "rows": rows_loaded, "region": region}
    except PermissionError:
        raise HTTPException(status_code=500, detail="파일이 다른 프로그램(엑셀 등)에서 열려 있습니다. 닫고 다시 시도해주세요.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"업로드 중 오류 발생: {str(e)}")

@app.post("/api/delete")
async def delete_records(region: str = "capital"):
    if region not in ["capital", "province"]:
        raise HTTPException(status_code=400, detail="알 수 없는 지역입니다.")
    
    # Delete from Supabase (primary)
    try:
        supabase_delete_records(region)
    except Exception as e:
        print(f"[Delete] Supabase delete error: {e}")
    
    # Delete from local SQLite (cache)
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM picking_records WHERE region = ?", (region,))
        c.execute("DELETE FROM region_meta WHERE region = ?", (region,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Delete] SQLite delete error: {e}")

    return {"success": True, "message": f"{'수도권' if region == 'capital' else '지방'} 기록이 삭제되었습니다."}

@app.get("/api/products")
async def get_products(region: str = "capital", sub_region: str = ""):
    """Get product list — Supabase first, SQLite fallback."""
    return safe_get_products(region, sub_region)

@app.get("/api/details")
async def get_details(name: str, region: str = "capital", sub_region: str = ""):
    """Get detail records — Supabase first, SQLite fallback."""
    return safe_get_details(name, region, sub_region)

@app.get("/api/info")
async def get_info(region: str = "capital"):
    if region not in ["capital", "province"]:
        region = "capital"
    
    info = safe_get_region_info(region)
    
    return {
        "ip": get_ip(),
        "rows": info["rows"],
        "date": info["delivery_date"],
        "mtime": info.get("updated_at", "N/A"),
        "cwd": os.getcwd(),
        "script_dir": BASE_DIR
    }

@app.get("/api/debug")
async def debug():
    import glob
    
    # Supabase status
    try:
        sb_capital = supabase_count("capital")
        sb_province = supabase_count("province")
        sb_status = "connected"
    except Exception as e:
        sb_capital = sb_province = 0
        sb_status = f"error: {e}"
    
    # Local SQLite status
    local_capital = db_get_region_info("capital")
    local_province = db_get_region_info("province")
    
    return {
        "cwd": os.getcwd(),
        "base_dir": BASE_DIR,
        "supabase_status": sb_status,
        "supabase_capital_rows": sb_capital,
        "supabase_province_rows": sb_province,
        "sqlite_capital_rows": local_capital["rows"],
        "sqlite_province_rows": local_province["rows"],
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
        "capital_path": EXCEL_CAPITAL,
        "province_path": EXCEL_PROVINCE,
        "capital_exists": os.path.exists(EXCEL_CAPITAL),
        "province_exists": os.path.exists(EXCEL_PROVINCE),
        "files_in_script_dir": glob.glob(os.path.join(BASE_DIR, "*")),
    }

# Serve static files
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    ip = get_ip()
    print(f"\n--- Mis-Picking Finder Server ---")
    print(f"Primary DB: Supabase ({SUPABASE_URL})")
    print(f"Fallback DB: SQLite ({DB_PATH})")
    print(f"Access on your phone: http://{ip}:8000")
    print(f"----------------------------------\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
