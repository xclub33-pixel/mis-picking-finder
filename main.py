from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
import pandas as pd
import os
import uvicorn
import socket
import shutil

app = FastAPI()

# Configuration
EXCEL_FILE = os.getenv("EXCEL_FILE", "사용자정의주문현황.xlsx")
STATIC_DIR = "static"

# Global data storage
df_data = None
delivery_date = "N/A"

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

def load_data():
    global df_data, delivery_date
    if not os.path.exists(EXCEL_FILE):
        print(f"Error: {EXCEL_FILE} not found")
        return
    
    try:
        # Load with header at row 1
        df = pd.read_excel(EXCEL_FILE, header=1)
        
        # Column mapping (adjusting based on analysis)
        # 1: 거래처, 12: 품명, 30: 주문수량, 44: 차량, 25: 배송일
        
        # Extract delivery date from index 25
        if len(df) > 0:
            raw_date = str(df.iloc[0, 25])
            # Handle float strings like '20260321.0'
            if "." in raw_date: raw_date = raw_date.split(".")[0]
            if len(raw_date) == 8:
                delivery_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            else:
                delivery_date = raw_date

        # Create a simplified dataframe
        processed_df = pd.DataFrame({
            'store': df.iloc[:, 1],
            'product': df.iloc[:, 12],
            'qty': df.iloc[:, 30],  # Changed from 29 to 30 (Order Quantity)
            'vehicle': df.iloc[:, 44]
        })
        
        # Clean data
        processed_df = processed_df.dropna(subset=['product', 'store'])
        
        # Ensure qty is numeric before doing math
        processed_df['qty'] = pd.to_numeric(processed_df['qty'], errors='coerce').fillna(0)
        
        # Convert quantity to Box (divide by 20) for specific meat products
        mask = processed_df['product'].str.contains('삼겹양지|목심|설도', na=False, regex=True)
        processed_df.loc[mask, 'qty'] = processed_df.loc[mask, 'qty'] / 20
        
        # Filter out 0 or negative quantities if they exist
        processed_df = processed_df[processed_df['qty'] > 0]
        
        df_data = processed_df
        print(f"Loaded {len(df_data)} rows of data.")
    except Exception as e:
        print(f"Error loading Excel: {e}")

@app.on_event("startup")
async def startup_event():
    load_data()

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="엑셀 파일(.xlsx, .xls)만 업로드 가능합니다.")
    
    try:
        # Save to the configured path
        with open(EXCEL_FILE, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Reload data
        load_data()
        rows_loaded = len(df_data) if df_data is not None else 0
        return {"filename": file.filename, "rows": rows_loaded}
    except PermissionError:
        raise HTTPException(status_code=500, detail="파일이 다른 프로그램(엑셀 등)에서 열려 있습니다. 닫고 다시 시도해주세요.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"업로드 중 오류 발생: {str(e)}")

@app.post("/api/delete")
async def delete_records():
    global df_data, delivery_date
    df_data = None
    delivery_date = "N/A"
    
    if os.path.exists(EXCEL_FILE):
        try:
            os.remove(EXCEL_FILE)
        except PermissionError:
            raise HTTPException(status_code=500, detail="파일이 다른 프로그램에서 열려 있어 삭제할 수 없습니다.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"파일 삭제 오류: {str(e)}")
            
    return {"success": True, "message": "모든 기록이 삭제되었습니다."}

@app.get("/api/products")
async def get_products():
    if df_data is None:
        return []
    # Get unique products and their total quantities
    summary = df_data.groupby('product')['qty'].sum().reset_index()
    return summary.to_dict(orient='records')

@app.get("/api/details")
async def get_details(name: str):
    if df_data is None:
        raise HTTPException(status_code=500, detail="Data not loaded")
    
    details = df_data[df_data['product'] == name].sort_values(by=['qty', 'vehicle'], ascending=True)
    # Fill NaNs to avoid JSON serialization issues
    return details.fillna("").to_dict(orient='records')

@app.get("/api/info")
async def get_info():
    return {
        "ip": get_ip(),
        "rows": len(df_data) if df_data is not None else 0,
        "date": delivery_date
    }

# Serve static files
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    ip = get_ip()
    print(f"\n--- Mis-Picking Finder Server ---")
    print(f"Access on your phone: http://{ip}:8000")
    print(f"----------------------------------\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
