from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
import pandas as pd
import os
import uvicorn
import socket
import shutil

app = FastAPI()

# Configuration
EXCEL_CAPITAL = os.getenv("EXCEL_CAPITAL", "사용자정의주문현황_수도권.xlsx")
EXCEL_PROVINCE = os.getenv("EXCEL_PROVINCE", "사용자정의주문현황_지방.xlsx")
STATIC_DIR = "static"

# Global data storage
df_data: dict = {"capital": None, "province": None}
delivery_date: dict = {"capital": "N/A", "province": "N/A"}

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

def load_data(region="capital"):
    global df_data, delivery_date
    file_path = EXCEL_CAPITAL if region == "capital" else EXCEL_PROVINCE
    
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found")
        df_data[region] = None
        delivery_date[region] = "N/A"
        return
    
    try:
        # Load with header at row 1
        df = pd.read_excel(file_path, header=1)
        
        # Extract delivery date from index 25
        if len(df) > 0:
            raw_date = str(df.iloc[0, 25])
            # Handle float strings like '20260321.0'
            if "." in raw_date: raw_date = raw_date.split(".")[0]
            if len(raw_date) == 8:
                delivery_date[region] = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            else:
                delivery_date[region] = raw_date

        # Create a simplified dataframe
        processed_df = pd.DataFrame({
            'store': df.iloc[:, 1],
            'product': df.iloc[:, 12],
            'qty': df.iloc[:, 30],  
            'vehicle': df.iloc[:, 44]  # Column AS: Vehicle or Province Region
        })
        
        # Clean data
        processed_df = processed_df.dropna(subset=['product', 'store'])
        processed_df['vehicle'] = processed_df['vehicle'].fillna('')
        
        # Ensure qty is numeric before doing math
        processed_df['qty'] = pd.to_numeric(processed_df['qty'], errors='coerce').fillna(0)
        
        # Convert quantity to Box (divide by 20) for specific meat products
        mask = processed_df['product'].str.contains('삼겹양지|목심|설도', na=False, regex=True)
        processed_df.loc[mask, 'qty'] = processed_df.loc[mask, 'qty'] / 20
        
        # Filter out 0 or negative quantities if they exist
        processed_df = processed_df[processed_df['qty'] > 0]
        
        df_data[region] = processed_df
        print(f"Loaded {len(df_data[region])} rows of data for {region}.")
    except Exception as e:
        print(f"Error loading Excel for {region}: {e}")

@app.on_event("startup")
async def startup_event():
    load_data("capital")
    load_data("province")

@app.post("/api/upload")
async def upload_file(region: str = "capital", file: UploadFile = File(...)):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="엑셀 파일(.xlsx, .xls)만 업로드 가능합니다.")
    
    if region not in ["capital", "province"]:
        raise HTTPException(status_code=400, detail="알 수 없는 지역입니다.")
        
    file_path = EXCEL_CAPITAL if region == "capital" else EXCEL_PROVINCE
    
    try:
        # Save to the configured path
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Reload data
        load_data(region)
        rows_loaded = len(df_data[region]) if df_data[region] is not None else 0
        return {"filename": file.filename, "rows": rows_loaded, "region": region}
    except PermissionError:
        raise HTTPException(status_code=500, detail="파일이 다른 프로그램(엑셀 등)에서 열려 있습니다. 닫고 다시 시도해주세요.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"업로드 중 오류 발생: {str(e)}")

@app.post("/api/delete")
async def delete_records(region: str = "capital"):
    global df_data, delivery_date
    if region not in ["capital", "province"]:
        raise HTTPException(status_code=400, detail="알 수 없는 지역입니다.")
        
    df_data[region] = None
    delivery_date[region] = "N/A"
    
    file_path = EXCEL_CAPITAL if region == "capital" else EXCEL_PROVINCE
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except PermissionError:
            raise HTTPException(status_code=500, detail="파일이 다른 프로그램에서 열려 있어 삭제할 수 없습니다.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"파일 삭제 오류: {str(e)}")
            
    return {"success": True, "message": f"{'수도권' if region == 'capital' else '지방'} 기록이 삭제되었습니다."}
            
    return {"success": True, "message": "모든 기록이 삭제되었습니다."}

@app.get("/api/products")
async def get_products(region: str = "capital", sub_region: str = ""):
    if region not in ["capital", "province"] or df_data[region] is None:
        return []
        
    df = df_data[region]
    if sub_region and sub_region != "전체":
        df = df[df['vehicle'].astype(str).str.contains(sub_region)]
        if df.empty:
            return []
            
    summary = df.groupby('product')['qty'].sum().reset_index()
    return summary.to_dict(orient='records')

@app.get("/api/details")
async def get_details(name: str, region: str = "capital", sub_region: str = ""):
    if region not in ["capital", "province"] or df_data[region] is None:
        raise HTTPException(status_code=500, detail="Data not loaded")
    
    df = df_data[region]
    if sub_region and sub_region != "전체":
        df = df[df['vehicle'].astype(str).str.contains(sub_region)]
        
    details = df[df['product'] == name].sort_values(by=['qty', 'vehicle'], ascending=True)
    return details.fillna("").to_dict(orient='records')

@app.get("/api/info")
async def get_info(region: str = "capital"):
    if region not in ["capital", "province"]:
        region = "capital"
    return {
        "ip": get_ip(),
        "rows": len(df_data[region]) if df_data[region] is not None else 0,
        "date": delivery_date[region]
    }

# Serve static files
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    ip = get_ip()
    print(f"\n--- Mis-Picking Finder Server ---")
    print(f"Access on your phone: http://{ip}:8000")
    print(f"----------------------------------\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
