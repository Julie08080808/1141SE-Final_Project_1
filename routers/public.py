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

    # 4️⃣ 回傳模板
    return templates.TemplateResponse(
        "project_detail.html",               
        {
            "request": request,              
            "user": user,                    
            "project": project,              
            "deliverables": deliverables,    
            "my_bid": my_bid,                
            "has_bid": has_bid,              
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