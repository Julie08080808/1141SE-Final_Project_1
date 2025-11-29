# --- [ routers/contractor.py ] ---
# 📘 功能說明：
# 這支程式控制「接案人（Contractor）」能做的事，例如：
# - 瀏覽所有可投標專案
# - 查看自己的投標紀錄
# - 更新報價
# - 上傳專案交付檔案 (已加入防覆蓋機制)
# --------------------------------------------------------

from fastapi import APIRouter, Depends, Form, Request, HTTPException, status, File, UploadFile
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from psycopg import Connection
from typing import Optional
import crud
import shutil
import os
from db import getDB
from auth import get_current_user
from datetime import datetime  # 🎯 [新增] 用於產生時間戳記
from pathlib import Path       # 🎯 [新增] 用於路徑處理
import re                      # 🎯 [新增] 用於清理檔名

# --------------------------------------------------------
# 🔧 基本設定區段
# --------------------------------------------------------

# 統一定義上傳資料夾 (使用 Path 物件較為穩健)
UPLOAD_DIR = Path("uploads")

# 建立接案人路由物件
router = APIRouter(
    prefix="/contractor",          # 所有路由網址開頭為 /contractor
    tags=["Contractor"],           # Swagger 顯示分類
    dependencies=[Depends(get_current_user)]  # 所有功能都必須登入才能使用
)

templates = Jinja2Templates(directory="templates")


# --------------------------------------------------------
# 🏠 路由 1: 接案人儀表板
# --------------------------------------------------------
@router.get("/dashboard", response_class=HTMLResponse)
async def get_contractor_dashboard(
    request: Request, 
    conn: Connection = Depends(getDB),      
    user: dict = Depends(get_current_user)  
):
    if user["user_type"].strip() != 'contractor':
        return RedirectResponse(url="/client/dashboard", status_code=status.HTTP_302_FOUND)

    open_projects = await crud.get_all_open_projects_with_bid_count(conn)

    return templates.TemplateResponse("contractor_dashboard.html", {
        "request": request,
        "user_name": user["name"].strip(),
        "projects": open_projects
    })


# --------------------------------------------------------
# 📋 路由 2: 查看我的投標紀錄
# --------------------------------------------------------
@router.get("/my-bids", response_class=HTMLResponse)
async def get_my_bids(
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 這裡維持傳送 'bids'，因為您的 my_bids.html 會自己使用 Jinja2 過濾器來分類
    my_bids = await crud.get_bids_by_contractor_id(conn, user["uid"])
    
    return templates.TemplateResponse("my_bids.html", {
        "request": request,
        "user_name": user["name"].strip(),
        "bids": my_bids   
    })


# --------------------------------------------------------
# 💰 路由 3: 更新投標價格
# --------------------------------------------------------
@router.post("/bid/{bid_id}/update", response_class=RedirectResponse)
async def update_bid(
    bid_id: int,                    
    new_price: float = Form(...),   
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    rows_updated = await crud.update_bid_price(
        conn=conn,
        bid_id=bid_id,
        contractor_id=user["uid"],       
        new_price=new_price
    )
    
    if rows_updated == 0:
        raise HTTPException(status_code=403, detail="無法更新此報價 (可能已結案)")

    return RedirectResponse(url="/contractor/my-bids", status_code=status.HTTP_302_FOUND)


# --------------------------------------------------------
# 📤 路由 4: 顯示上傳交付檔案表單
# --------------------------------------------------------
@router.get("/project/{project_id}/deliver", response_class=HTMLResponse)
async def deliver_form(
    project_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")
    
    project_status = project["status"].strip()
    if project_status not in ['in_progress', 'review']:
        # 這裡使用 HTMLResponse 返回錯誤訊息，保持您原本的設計
        return HTMLResponse(
            f"<h2>此專案目前狀態為「{project_status}」,無法上傳檔案</h2>"
            f"<p>只有「執行中」或「已退件」的專案可以上傳檔案。</p>"
            f'<a href="/contractor/my-bids">返回我的專案</a>',
            status_code=400
        )

    return templates.TemplateResponse("deliver_form.html", {
        "request": request,
        "project": project,
        "user_name": user["name"].strip()
    })


# --------------------------------------------------------
# 📦 路由 5: 處理上傳的交付檔案 (🎯 修正檔名覆蓋問題)
# --------------------------------------------------------
@router.post("/project/{project_id}/deliver", response_class=RedirectResponse)
async def process_deliverable(
    project_id: int,                     
    note: str = Form(""),                
    file: UploadFile = File(...),        
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 1. 檢查專案是否存在
    project = await crud.get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")
    
    # 2. 檢查專案狀態
    project_status = project["status"].strip()
    if project_status not in ['in_progress', 'review']:
        raise HTTPException(
            status_code=400, 
            detail=f"專案狀態「{project_status}」無法上傳檔案"
        )

    # 3. 處理檔案儲存 (加入時間戳記防止覆蓋)
    file_url = None

    if file and file.filename:
        # A. 設定資料夾路徑： uploads/project_{id}/deliverable
        project_folder = UPLOAD_DIR / f"project_{project_id}" / "deliverable"
        project_folder.mkdir(parents=True, exist_ok=True) # 自動建立資料夾

        # B. 處理檔名：接案人帳號 + 原始檔名 + 時間戳記
        contractor_name = user["name"].strip()
        safe_username = re.sub(r'[^\w\-]', '', contractor_name) # 清除特殊符號
        
        original_filename = Path(file.filename).name
        file_extension = Path(original_filename).suffix
        stem = original_filename[:-len(file_extension)] if file_extension else original_filename
        safe_stem = re.sub(r'[^\w\-]', '_', stem) # 檔名中的特殊符號轉底線
        
        # 🎯 [關鍵修改] 加入時間戳記 (YYYYMMDD_HHMMSS)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_filename = f"{safe_username}_{safe_stem}_{timestamp}{file_extension}"

        # C. 完整路徑
        file_path = project_folder / final_filename
        
        try:
            # D. 寫入檔案
            with open(file_path, "wb") as buffer:
                # 使用 copyfileobj 或 await file.read() 均可，這裡配合 UploadFile
                shutil.copyfileobj(file.file, buffer)
            
            # E. 設定資料庫存取的 URL (注意：URL 必須使用正斜線 /)
            file_url = f"/uploads/project_{project_id}/deliverable/{final_filename}"
            
        finally:
            file.file.close() # 關閉暫存檔

    if file_url is None:
        raise HTTPException(status_code=400, detail="檔案上傳失敗")

    # 4. 寫入資料庫
    await crud.create_deliverable(
        conn=conn,
        project_id=project_id,
        contractor_id=user["uid"],
        file_url=file_url,
        note=note
    )
    
    return RedirectResponse(url="/contractor/my-bids", status_code=status.HTTP_302_FOUND)