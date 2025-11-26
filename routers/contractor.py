# --- [ routers/contractor.py ] ---
# 📘 功能說明：
# 這支程式控制「接案人（Contractor）」能做的事，例如：
# - 瀏覽所有可投標專案
# - 查看自己的投標紀錄
# - 更新報價
# - 上傳專案交付檔案
# --------------------------------------------------------

# routers/contractor.py 
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


# --------------------------------------------------------
# 🔧 基本設定區段
# --------------------------------------------------------

# 統一定義上傳資料夾
UPLOAD_DIR = "uploads"    # 上傳資料夾統一放在 uploads 目錄下

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
# 路由 1: "接案人儀表板" (GET) /contractor/dashboard
@router.get("/dashboard", response_class=HTMLResponse)
async def get_contractor_dashboard(
    request: Request, 
    conn: Connection = Depends(getDB),      # 建立資料庫連線
    user: dict = Depends(get_current_user)  # 取得目前登入使用者資訊
):
     # 若登入者不是接案人，導回委託人儀表板
    if user["user_type"].strip() != 'contractor':
        return RedirectResponse(url="/client/dashboard", status_code=status.HTTP_302_FOUND)

    # ✅ 使用 CRUD 函式抓出所有公開專案 + 投標數
    # 這個函數會返回：client_name (委託人) 和 bid_count (競標人數)
    open_projects = await crud.get_all_open_projects_with_bid_count(conn)

    # 將資料傳進模板產生 HTML 頁面
    return templates.TemplateResponse("contractor_dashboard.html", {
        "request": request,
        "user_name": user["name"].strip(),  # 顯示登入者名稱
        "projects": open_projects           # 所有可投標專案
    })



# --------------------------------------------------------
# 📋 路由 2: 查看我的投標紀錄
# --------------------------------------------------------
# 路由 2: "顯示我所有的投標" (GET) /contractor/my-bids
@router.get("/my-bids", response_class=HTMLResponse)
async def get_my_bids(
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 從資料庫撈取此接案人所有投標紀錄
    my_bids = await crud.get_bids_by_contractor_id(conn, user["uid"])
    
    # 回傳模板顯示投標清單
    return templates.TemplateResponse("my_bids.html", {
        "request": request,
        "user_name": user["name"].strip(),
        "bids": my_bids   # 投標紀錄資料列表
    })


# --------------------------------------------------------
# 💰 路由 3: 更新投標價格
# --------------------------------------------------------
# 路由 3: "更新我的投標價格" (POST) /contractor/bid/{bid_id}/update
@router.post("/bid/{bid_id}/update", response_class=RedirectResponse)
async def update_bid(
    bid_id: int,                    # 投標 ID
    new_price: float = Form(...),   # 從表單中取得新價格
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    
    # 呼叫 CRUD 函式更新該接案人的投標價格
    rows_updated = await crud.update_bid_price(
        conn=conn,
        bid_id=bid_id,
        contractor_id=user["uid"],       # 確保只能改自己的投標
        new_price=new_price
    )
    
    # 若沒有資料被更新（例如專案已結案），丟出錯誤
    if rows_updated == 0:
        raise HTTPException(status_code=403, detail="無法更新此報價 (可能已結案)")

    # 更新成功 → 回到「我的投標」頁面
    return RedirectResponse(url="/contractor/my-bids", status_code=status.HTTP_302_FOUND)


# --------------------------------------------------------
# 📤 路由 4: 顯示上傳交付檔案表單
# --------------------------------------------------------
# 路由 4: "顯示上傳檔案的表單" (GET) /contractor/project/{project_id}/deliver
@router.get("/project/{project_id}/deliver", response_class=HTMLResponse)
async def deliver_form(
    project_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    
    # 從資料庫取得專案資料
    project = await crud.get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")
    
    # ✅ 限定狀態：只有 in_progress（進行中）或 review（被退件）能上傳交付
    project_status = project["status"].strip()
    if project_status not in ['in_progress', 'review']:
        return HTMLResponse(
            f"<h2>此專案目前狀態為「{project_status}」,無法上傳檔案</h2>"
            f"<p>只有「執行中」或「已退件」的專案可以上傳檔案。</p>"
            f'<a href="/contractor/my-bids">返回我的專案</a>',
            status_code=400
        )

    # 顯示模板 deliver_form.html，讓使用者上傳交付檔案
    return templates.TemplateResponse("deliver_form.html", {
        "request": request,
        "project": project,
        "user_name": user["name"].strip()
    })



# --------------------------------------------------------
# 📦 路由 5: 處理上傳的交付檔案
# --------------------------------------------------------
# 路由 5: "處理檔案上傳 (交付)" (POST)
@router.post("/project/{project_id}/deliver", response_class=RedirectResponse)
async def process_deliverable(
    project_id: int,                     # 專案 ID
    note: str = Form(""),                # 備註說明文字
    file: UploadFile = File(...),        # 上傳檔案
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
      # ✅ 檢查專案是否存在
    project = await crud.get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")
    
     # ✅ 檢查專案狀態（必須為進行中或退件狀態）
    project_status = project["status"].strip()
    if project_status not in ['in_progress', 'review']:
        raise HTTPException(
            status_code=400, 
            detail=f"專案狀態「{project_status}」無法上傳檔案"
        )

    # 處理檔案儲存
    file_url = None    # 先預設 file_url 為 None，代表尚未上傳成功
    #宣告一個變數 file_url，用來儲存檔案的「網址路徑」。

    if file and file.filename:
         # 建立專案子資料夾 uploads/project_xx/deliverable/ ， 建立專屬的資料夾給這個專案存檔案。
        project_folder = os.path.join(UPLOAD_DIR, f"project_{project_id}", "deliverable")
        os.makedirs(project_folder, exist_ok=True) 

        # 組出檔案完整路徑
        file_path = os.path.join(project_folder, file.filename)
        
        try:
            # 將檔案內容寫入伺服器端 (伺服器內處存一份完整的檔案)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        finally:
            file.file.close()     #關閉檔案物件
        
        file_url = f"/uploads/project_{project_id}/deliverable/{file.filename}"

     # 若 file_url 為 None，代表上傳失敗
    if file_url is None:
        raise HTTPException(status_code=400, detail="檔案上傳失敗")

     # ✅ 在資料庫中建立交付紀錄
    await crud.create_deliverable(
        conn=conn,
        project_id=project_id,
        contractor_id=user["uid"],
        file_url=file_url,
        note=note
    )
    
    # 成功後導回「我的投標」頁面
    return RedirectResponse(url="/contractor/my-bids", status_code=status.HTTP_302_FOUND)