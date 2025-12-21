# --- [ routers/public.py ] ---
# 📘 功能說明：
# 這個檔案負責「公開頁面與共用功能」：
# 1️⃣ 查看專案詳情（所有登入使用者可看）
# 2️⃣ 提交報價（接案人專用）
# 3️⃣ 查看歷史紀錄（委託人與接案人共用）
# --------------------------------------------------------

from fastapi import APIRouter, Depends, Form, Request, HTTPException, status, UploadFile, File 
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from psycopg import Connection
from db import getDB
from auth import get_current_user
import crud
from pathlib import Path 
import re # 用於清理檔名
from datetime import date

# --------------------------------------------------------
# 🧩 初始化設定區段
# --------------------------------------------------------
router = APIRouter(
    tags=["Public"],     
    dependencies=[Depends(get_current_user)]   
)

templates = Jinja2Templates(directory="templates") 

# --------------------------------------------------------
# 📄 路由 1: "查看專案詳情" (GET)
# --------------------------------------------------------
@router.get("/project/{project_id}", response_class=HTMLResponse)
async def get_project_details(
    project_id: int,                         
    request: Request,                        
    conn: Connection = Depends(getDB),       
    user: dict = Depends(get_current_user)   
):
    # 1️⃣ 取得專案詳情資料
    project = await crud.get_project_by_id(conn, project_id)
    if not project:                          
        raise HTTPException(status_code=404, detail="Project not found")
    # 如果今天日期 > 截止日期，則視為過期
    is_expired = False
    if project["deadline"] and project["deadline"] < date.today():
        is_expired = True

    # 2️⃣ 如果專案不是「open」狀態，就撈交付檔案（deliverables）
    deliverables = []
    if project["status"].strip() != "open":
        deliverables = await crud.get_deliverables_for_project(conn, project_id)

    # 3️⃣ 若登入者是接案人，查出他是否已對此專案投標
    my_bid = None
    has_bid = False                          
    if user["user_type"].strip() == "contractor":   
        # 查詢時會包含 proposal_url，用於前端顯示
        my_bid = await crud.get_bid_by_project_and_contractor(
            conn, project_id, user["uid"]
        )
        has_bid = (my_bid is not None)       


    client_id = project['client_id']
    client_stats = await crud.get_user_reputation_stats(conn, client_id)
    client_reviews = await crud.get_user_received_reviews_public(conn, client_id)

    # 4️⃣ 回傳模板，顯示專案詳情頁面

    return templates.TemplateResponse(
        "project_detail.html",               
        {
            "request": request,              # 傳入請求物件（Jinja2 需要）
            "user": user,                    # 登入者資料（顯示名稱或角色）
            "project": project,              # 專案詳細資訊
            "deliverables": deliverables,    # 專案交付檔案列表
            "my_bid": my_bid,                # 該接案人投標內容（若有）
            "has_bid": has_bid,              # 是否已投標的布林值
            "is_expired": is_expired,  # 🎯 補上這一行，前端才抓得到
            "client_stats": client_stats,
            "client_reviews": client_reviews
        },
    )

# --------------------------------------------------------
# 💰 路由 2: "提交該專案報價" (POST)
# --------------------------------------------------------
@router.post("/project/{project_id}/bid", response_class=RedirectResponse)
async def submit_bid(
    project_id: int,                         
    request: Request,
    price: float = Form(...),                
    message: str = Form(""),                 
    proposal_file: UploadFile = File(None),  # 接收檔案 (PDF)
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user),
):
    # 限制只有接案人可以投標
    if user["user_type"].strip() != "contractor":
        raise HTTPException(status_code=403, detail="只有接案人可以投標")
    
    # 🎯 [新增] 提交時的最後防線：再次檢查日期
    project = await crud.get_project_by_id(conn, project_id)
    if project and project["deadline"] and project["deadline"] < date.today():
        raise HTTPException(status_code=400, detail="此專案已超過報價截止日期，無法再投標。")

    proposal_url = None
    
    # 1. 處理檔案上傳
    if proposal_file and proposal_file.filename:
        # 檢查檔案類型是否為 PDF
        if proposal_file.content_type != "application/pdf":
            raise HTTPException(
                status_code=400, 
                detail="上傳的檔案格式錯誤：請確保您上傳的是 PDF 檔案 (.pdf)。"
            )
        
        # 🎯 [路徑邏輯] 專案ID資料夾 -> bids
        UPLOAD_DIR = Path("uploads") / f"project_{project_id}" / "bids"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True) 

        # 🎯 [檔名邏輯] 使用者帳號 + 原始檔名 (清理特殊字元)
        contractor_name = user["name"].strip()
        
        # 清理使用者名稱 (只保留英數、下底線、連字號)
        safe_username = re.sub(r'[^\w\-]', '', contractor_name)
        
        # 清理原始檔名
        original_filename = Path(proposal_file.filename).name
        file_extension = Path(original_filename).suffix
        # 取得主檔名，並替換特殊字元為 _
        stem = original_filename[:-len(file_extension)] if file_extension else original_filename
        safe_stem = re.sub(r'[^\w\-]', '_', stem)
        
        # 組合: 帳號_檔名.pdf
        final_filename = f"{safe_username}_{safe_stem}{file_extension}"
        
        file_path = UPLOAD_DIR / final_filename

        try:
            with open(file_path, "wb") as buffer:
                buffer.write(await proposal_file.read()) 
            
            # 產生 URL (對應 main.py 的 StaticFiles 掛載點)
            proposal_url = f"/uploads/project_{project_id}/bids/{final_filename}" 

        except Exception as e:
            print(f"File upload error: {e}")
            raise HTTPException(status_code=500, detail="檔案儲存失敗。")

    # 2. 建立投標紀錄
    try:
        await crud.create_bid(
            conn=conn,
            project_id=project_id,
            contractor_id=user["uid"],
            price=price,
            message=message,
            proposal_url=proposal_url,  # 寫入資料庫
        )
        return RedirectResponse(url="/contractor/my-bids", status_code=status.HTTP_302_FOUND)
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Database error on create_bid: {e}")
        raise HTTPException(status_code=500, detail="提交報價時發生資料庫錯誤。")
    
    

# --------------------------------------------------------
# 🕓 路由 3: "歷史紀錄" (GET)
# --------------------------------------------------------
@router.get("/history", response_class=HTMLResponse)
async def get_history_page(
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user),
):
    user_type = user["user_type"].strip()
    projects = []

    if user_type == "client":
        projects = await crud.get_client_history(conn, user["uid"])
    else:
        projects = await crud.get_contractor_history(conn, user["uid"])

    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "user_name": user["name"].strip(),
            "user_type": user_type,
            "projects": projects,
        },
    )


# 👤 路由: 查看使用者評價
@router.get("/profile/{target_user_id}", response_class=HTMLResponse)
async def view_user_profile(
    target_user_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 1. 取得該使用者基本資料 (名字、角色)
    target_user = await crud.get_user_by_id(conn, target_user_id)
    if not target_user:
        return HTMLResponse("User not found", status_code=404)

    # 2. 取得統計分數 (3維度)
    stats = await crud.get_user_reputation_stats(conn, target_user_id)
    
    # 3. 取得詳細評論列表
    reviews = await crud.get_user_received_reviews_public(conn, target_user_id)

    # 4. 🆕 取得排名資料
    ranking = await crud.get_user_ranking(
        conn, 
        target_user_id, 
        target_user['user_type'].strip()
    )
    
    # 5. 🆕 取得活躍度
    activity = await crud.get_user_activity_score(conn, target_user_id)

    return templates.TemplateResponse("review.html", {
        "request": request,
        "user": user,           # 當前登入者 (為了顯示 Header)
        "target_user": target_user, # 被查看的人
        "stats": stats,
        "reviews": reviews,
        "ranking": ranking,      # 🆕 新增
        "activity": activity,    # 🆕 新增
        "is_self": False
    })

# 🏆 路由: 排行榜頁面（支援角色切換）
@router.get("/leaderboard", response_class=HTMLResponse)
async def view_leaderboard(
    request: Request,
    role: str = None,  # 新增：可選的角色參數
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 如果沒有指定 role，預設顯示使用者自己的角色
    if role not in ['client', 'contractor']:
        role = user["user_type"].strip()
    
    # 取得指定角色的排行榜資料（前50名）
    leaderboard = await crud.get_leaderboard(conn, role, limit=50)
    
    # 找出當前使用者的排名（如果查看的是自己的角色）
    current_user_rank = None
    if role == user["user_type"].strip():
        for item in leaderboard:
            if item['uid'] == user['uid']:
                current_user_rank = item
                break
        
        # 如果使用者不在前50名，單獨查詢他的排名
        if not current_user_rank:
            ranking = await crud.get_user_ranking(conn, user['uid'], role)
            if ranking and ranking['rank']:
                current_user_rank = {
                    'uid': user['uid'],
                    'name': user['name'],
                    'avg_score': ranking['avg_score'],
                    'review_count': ranking['review_count'],
                    'rank': ranking['rank']
                }
    
    return templates.TemplateResponse("leaderboard.html", {
        "request": request,
        "user": user,
        "leaderboard": leaderboard,
        "current_user_rank": current_user_rank,
        "current_role": role,  # 傳入當前顯示的角色
    })