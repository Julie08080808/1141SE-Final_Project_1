# --- [ routers/client.py (v3.1 含投標數統計版) ] ---
# 📘 功能說明：
# 委託人（Client）專屬路由，負責：
# - 儀表板顯示（含專案統計）
# - 建立、修改、管理專案
# - 選擇接案人 / 結案 / 駁回
# - 瀏覽公開專案列表

# routers/client.py 
from fastapi import APIRouter, Depends, Form, Request, HTTPException, status, File, UploadFile
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from psycopg import Connection
from datetime import date
from db import getDB
from auth import get_current_user
from typing import Optional
import crud
import os
import shutil

# 統一定義上傳資料夾
UPLOAD_DIR = "uploads" 

# 建立路由物件 (委託人專屬)
router = APIRouter(
    prefix="/client",    # 網址開頭固定為 /client
    tags=["Client"],     # Swagger 標籤 (用來自動生成API文件)
    dependencies=[Depends(get_current_user)]   # 需登入後才能使用
)

# 載入 Jinja2 模板，告訴它去 "templates" 資料夾找 HTML
templates = Jinja2Templates(directory="templates")

# --------------------------------------------------------
# 📊 路由 1: 儀表板 - 顯示委託人專案總覽
# --------------------------------------------------------
@router.get("/dashboard", response_class=HTMLResponse)
async def get_client_dashboard(
    request: Request, 
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user) 
):
    # 驗證登入者是否為委託人
    if user["user_type"].strip() != 'client':
        return RedirectResponse(url="/contractor/dashboard") 

    # 使用新的 CRUD 函數，它會自動取得委託人所有專案 + 投標數統計
    all_projects = await crud.get_projects_by_client_id_with_bid_count(conn, user["uid"]) # 取得委託人的專案，同時統計投標數
    
    # 分類專案
    bidding_projects = []
    pending_projects = []
    completed_projects = []
    
    if all_projects:
        for proj in all_projects:
            status = proj["status"].strip()
            
            if status == 'open':
                bidding_projects.append(proj)
            elif status == 'completed':
                completed_projects.append(proj)
            else:
                # 剩下 (in_progress, submitted, review 等) 都算「待結案」
                pending_projects.append(proj)

    # 傳遞三個列表給模板
    return templates.TemplateResponse("client_dashboard.html", {
        "request": request,
        "user_name": user["name"].strip(),
        "bidding_projects": bidding_projects, 
        "pending_projects": pending_projects,
        "completed_projects": completed_projects
    })

# --------------------------------------------------------
# 📝 路由 2: 顯示「建立專案表單」
# --------------------------------------------------------
# 路由 2: 顯示建立專案表單 GET /client/project/new
@router.get("/project/new", response_class=HTMLResponse)
async def new_project_form(
    request: Request,
    user: dict = Depends(get_current_user) 
):
    if user["user_type"].strip() != 'client':
        return RedirectResponse(url="/contractor/dashboard") 
    return templates.TemplateResponse("project_new.html", {"request": request})

# --------------------------------------------------------
# 📤 路由 3: 處理建立專案的表單資料 (含附件)
# --------------------------------------------------------
# 路由 3: 處理建立專案 POST /client/project/new (v3.0)
@router.post("/project/new", response_class=RedirectResponse)
async def create_new_project(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    budget: float = Form(...),
    deadline: date = Form(...),
    attachment: Optional[UploadFile] = File(None), 
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 只允許委託人建立專案
    if user["user_type"].strip() != 'client':
        raise HTTPException(status_code=403, detail="Only clients can create projects")

    # 先建立專案，取得 project_id
    new_project = await crud.create_project(
        conn=conn,
        client_id=user["uid"],
        title=title,
        description=description,
        budget=budget,
        deadline=deadline
    )
    
    if not new_project:
        raise HTTPException(status_code=500, detail="Create project failed")

    new_project_id = new_project["id"]

    # 處理檔案上傳 ， 若有上傳附件 → 儲存檔案並更新資料庫
    attachment_url = None
    if attachment and attachment.filename:
        # 建立專屬子資料夾
        project_folder = os.path.join(UPLOAD_DIR, f"project_{new_project_id}", "attachment")
        os.makedirs(project_folder, exist_ok=True) 

        file_path = os.path.join(project_folder, attachment.filename)
        
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(attachment.file, buffer)
        finally:
            attachment.file.close()
        
        attachment_url = f"/uploads/project_{new_project_id}/attachment/{attachment.filename}"

        # 回頭更新 project，把 URL 補上
        await crud.update_project(
            conn=conn, project_id=new_project_id, client_id=user["uid"],
            title=title, description=description, budget=budget, deadline=deadline,
            attachment_url=attachment_url
        )
    
    return RedirectResponse(url="/client/dashboard", status_code=status.HTTP_302_FOUND)



# ------------------------------------------------------------
# 📦 路由 4: 專案管理頁面 (查看報價、選擇接案人、核准交付、退件)
# ------------------------------------------------------------
# --- [ 報價 / 結案 / 編輯 管理區 ] ---

# 路由 4: "專案管理總頁" GET /client/project/{project_id}/manage
@router.get("/project/{project_id}/manage", response_class=HTMLResponse)
async def get_project_management_page(
    project_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id) 
    if not project or project["client_id"] != user["uid"]:
        return HTMLResponse("專案不存在或您沒有權限。", status_code=403)
    
    bids = await crud.get_bids_for_project(conn, project_id)     # 取得專案所有投標紀錄（含接案人名稱）
    deliverables = await crud.get_deliverables_for_project(conn, project_id)

    return templates.TemplateResponse("bid_list.html", {  
        #它的作用是：將資料傳入 bid_list.html 模板，然後產生一個完整的 HTML 回應給使用者
        "request": request,
        "project": project,
        "bids": bids,
        "deliverables": deliverables, 
        "user_name": user["name"].strip()
    })


# --------------------------------------------------------
# ✅ 路由 5: 委託人選擇得標者
# --------------------------------------------------------
# 路由 5: "選擇接案人" POST /client/project/{project_id}/select/{bid_id}
@router.post("/project/{project_id}/select/{bid_id}", response_class=RedirectResponse)
async def select_bid(
    project_id: int,
    bid_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        return HTMLResponse("專案不存在或您沒有權限。", status_code=403)
    
    if project["status"].strip() != 'open':
        return HTMLResponse("這個專案已經不在開放狀態，無法選擇報價。", status_code=400)
    
    await crud.select_bid_for_project(conn, project_id, bid_id)
    
    return RedirectResponse(url=f"/client/project/{project_id}/manage", status_code=status.HTTP_302_FOUND)



# --------------------------------------------------------
# ✅ 路由 6: 結案 (通過交付)
# --------------------------------------------------------
# 路由 6: "結案 (通過)" POST /client/.../approve
@router.post("/project/{project_id}/deliverable/{deliverable_id}/approve", response_class=RedirectResponse)
async def approve_deliverable(
    project_id: int,
    deliverable_id: int,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    await crud.approve_deliverable_and_complete_project(conn, project_id, deliverable_id, user["uid"])
    
    return RedirectResponse(url=f"/client/project/{project_id}/manage", status_code=status.HTTP_302_FOUND)

# --------------------------------------------------------
# ❌ 路由 7: 退件 (駁回交付)
# --------------------------------------------------------
@router.post("/project/{project_id}/deliverable/{deliverable_id}/reject", response_class=RedirectResponse)
async def reject_deliverable_route(
    project_id: int,
    deliverable_id: int,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    await crud.reject_deliverable(conn, project_id, deliverable_id, user["uid"])
    
    return RedirectResponse(url=f"/client/project/{project_id}/manage", status_code=status.HTTP_302_FOUND)

# --------------------------------------------------------
# 🧾 路由 8: 顯示編輯專案表單
# --------------------------------------------------------
@router.get("/project/{project_id}/edit", response_class=HTMLResponse)
async def edit_project_form(
    project_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id)

    if not project or project["client_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if project["status"].strip() != 'open':
        return HTMLResponse("專案已鎖定，無法編輯。")

    return templates.TemplateResponse("project_edit.html", {
        "request": request,
        "project": project
    })

# --------------------------------------------------------
# 🧩 路由 9: 處理編輯專案表單 (含附件更新)
# --------------------------------------------------------
@router.post("/project/{project_id}/edit", response_class=RedirectResponse)
async def process_edit_project(
    project_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    budget: float = Form(...),
    deadline: date = Form(...),
    attachment: Optional[UploadFile] = File(None), 
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    attachment_url = None
    # ✅ 若有上傳新附件 → 取代舊檔案
    if attachment and attachment.filename:
        # 儲存到專屬子資料夾
        project_folder = os.path.join(UPLOAD_DIR, f"project_{project_id}", "attachment")
        os.makedirs(project_folder, exist_ok=True) 

        file_path = os.path.join(project_folder, attachment.filename)
        
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(attachment.file, buffer)
        finally:
            attachment.file.close()
        
        attachment_url = f"/uploads/project_{project_id}/attachment/{attachment.filename}"
    else:
        # 如果沒有上傳新檔案，就保留舊的
        project = await crud.get_project_by_id(conn, project_id)
        if project:
            attachment_url = project.get("attachment_url")

    rows_updated = await crud.update_project(
        conn=conn, project_id=project_id, client_id=user["uid"],
        title=title, description=description, budget=budget, deadline=deadline,
        attachment_url=attachment_url
    )
    
    if rows_updated == 0:
        raise HTTPException(status_code=403, detail="Cannot edit this project")

    return RedirectResponse(url=f"/client/project/{project_id}/manage", status_code=status.HTTP_302_FOUND)


# --------------------------------------------------------
# 🔍 路由 10: 瀏覽所有公開專案 (for Client)
# --------------------------------------------------------
# 路由 10: "瀏覽公開招標專案" GET /client/browse
@router.get("/browse", response_class=HTMLResponse)
async def browse_open_projects(
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    if user["user_type"].strip() != 'client':
        return RedirectResponse(url="/contractor/dashboard")
    
    # 取得所有公開招標的專案（包含投標數） # 從 crud 取得所有 open 專案（含投標數）
    open_projects = await crud.get_all_open_projects_with_bid_count(conn)
    
    return templates.TemplateResponse("client_browse_projects.html", {
        "request": request,
        "user_name": user["name"].strip(),
        "projects": open_projects
    })