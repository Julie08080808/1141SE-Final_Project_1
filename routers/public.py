# --- [ routers/public.py ] ---
# 📘 功能說明：
# 這個檔案負責「公開頁面與共用功能」：
# 1️⃣ 查看專案詳情（所有登入使用者可看）
# 2️⃣ 提交報價（接案人專用）
# 3️⃣ 查看歷史紀錄（委託人與接案人共用）
# --------------------------------------------------------


# routers/public.py 
from fastapi import APIRouter, Depends, Form, Request, HTTPException, status
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from psycopg import Connection
from db import getDB
from auth import get_current_user
import crud



# --------------------------------------------------------
# 🧩 初始化設定區段
# --------------------------------------------------------
router = APIRouter(
    tags=["Public"],     # Swagger 分類標籤 
    dependencies=[Depends(get_current_user)]   # ✅ 所有路由需登入後才能使用
)

templates = Jinja2Templates(directory="templates")   # 設定 HTML 模板資料夾


# --------------------------------------------------------
# 📄 路由 1: "查看專案詳情" (GET)
# --------------------------------------------------------
# 路由 1: "查看專案詳情" (GET)
@router.get("/project/{project_id}", response_class=HTMLResponse)
async def get_project_details(
    project_id: int,                         # 從網址取得專案 ID
    request: Request,                        # 當前請求物件 (含 session)
    conn: Connection = Depends(getDB),       # 自動取得資料庫連線
    user: dict = Depends(get_current_user)   # 目前登入使用者資料
):
    # 1️⃣ 取得專案詳情資料
    project = await crud.get_project_by_id(conn, project_id)
    if not project:                          # 如果查不到資料
        raise HTTPException(status_code=404, detail="Project not found")

    # 2️⃣ 如果專案不是「open」狀態，就撈交付檔案（deliverables）
    deliverables = []
    if project["status"].strip() != "open":
        deliverables = await crud.get_deliverables_for_project(conn, project_id)

    # 3️⃣ 若登入者是接案人，查出他是否已對此專案投標
    my_bid = None
    has_bid = False                          # 預設尚未投標
    if user["user_type"].strip() == "contractor":   # 檢查角色
        my_bid = await crud.get_bid_by_project_and_contractor(
            conn, project_id, user["uid"]
        )
        has_bid = (my_bid is not None)       # True 表示已投標

    # 4️⃣ 回傳模板，顯示專案詳情頁面
    return templates.TemplateResponse(
        "project_detail.html",               # 對應的 HTML 模板
        {
            "request": request,              # 傳入請求物件（Jinja2 需要）
            "user": user,                    # 登入者資料（顯示名稱或角色）
            "project": project,              # 專案詳細資訊
            "deliverables": deliverables,    # 專案交付檔案列表
            "my_bid": my_bid,                # 該接案人投標內容（若有）
            "has_bid": has_bid,              # 是否已投標的布林值
        },
    )



# --------------------------------------------------------
# 💰 路由 2: "提交該專案報價" (POST)
# --------------------------------------------------------
@router.post("/project/{project_id}/bid", response_class=RedirectResponse)
async def submit_bid(
    project_id: int,                         # 專案 ID
    request: Request,
    price: float = Form(...),                # 投標價格
    message: str = Form(""),                 # 投標留言（可空）
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user),
):
    # 限制只有接案人可以投標
    if user["user_type"].strip() != "contractor":
        raise HTTPException(status_code=403, detail="只有接案人可以投標")
    
    try:
        # 呼叫 CRUD 函式，建立投標紀錄
        await crud.create_bid(
            conn=conn,
            project_id=project_id,
            contractor_id=user["uid"],        # 登入者的 ID
            price=price,
            message=message,
        )
        # 投標成功 → 導回我的投標列表
        return RedirectResponse(url="/contractor/my-bids", status_code=status.HTTP_302_FOUND)
    
    except ValueError as e:
        # ✅ 若重複投標（create_bid 內部檢查），丟出錯誤訊息
        raise HTTPException(status_code=400, detail=str(e))



# --------------------------------------------------------
# 🕓 路由 3: "歷史紀錄" (GET)
# --------------------------------------------------------
@router.get("/history", response_class=HTMLResponse)
async def get_history_page(
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user),
):
    user_type = user["user_type"].strip()    # 判斷角色 (client / contractor)
    projects = []                            # 存放歷史專案紀錄

    # 根據角色查詢不同的歷史紀錄
    if user_type == "client":
        projects = await crud.get_client_history(conn, user["uid"])
    else:
        projects = await crud.get_contractor_history(conn, user["uid"])

    # 回傳模板，顯示歷史頁面
    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,              # 給模板用的 request
            "user_name": user["name"].strip(),
            "user_type": user_type,          # 用於模板顯示角色名稱
            "projects": projects,            # 歷史專案清單
        },
    )