# --- [ routers/client.py (v3.2 UX優化版：錯誤回填表單) ] ---
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
from datetime import datetime, timedelta
import ai_service  # 🎯 [新增] 導入 AI 服務
import mimetypes   # 🎯 [新增] 用來判斷檔案類型

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
    all_projects = await crud.get_projects_by_client_id_with_bid_count(conn, user["uid"]) 
    
    given_reviews = await crud.get_my_given_reviews(conn, user["uid"])

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
        "completed_projects": completed_projects,
        "given_reviews": given_reviews,
        # 輔助變數 (讓前端知道現在在哪一頁，選填)
        "active_tab": "projects"
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
# 路由 3: 處理建立專案 POST /client/project/new (v3.2 UX優化)
@router.post("/project/new", response_class=HTMLResponse) # 注意：這裡回傳型態改為 HTMLResponse 以便渲染錯誤頁面
async def create_new_project(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    budget: float = Form(...),
    deadline: date = Form(...),
    attachment: Optional[UploadFile] = File(None), 
    conn: Connection = Depends(getDB), #用 getDB 函式取得資料庫連線
    user: dict = Depends(get_current_user) #取得使用者身分
):
    # 只允許委託人建立專案 移除空白符號確認身分(怕使用者多打但這邊是db，怕有些情況db自動補滿空白)
    if user["user_type"].strip() != 'client':
        raise HTTPException(status_code=403, detail="Only clients can create projects")

    # 🔥 [UX優化] 截止日期檢查：若日期錯誤，不跳轉，直接回傳原頁面 + 錯誤訊息 + 保留填寫資料
    if deadline < date.today():
        return templates.TemplateResponse("project_new.html", {
            "request": request,
            "error_message": "截止日期無效：不能選擇過去的日期！",  # 👈 傳給前端顯示
            # 👇 把使用者剛填的資料傳回去，前端可以用 value="{{ title }}" 接住
            "title": title,
            "description": description,
            "budget": budget,
            "deadline": deadline 
        }, status_code=400)

    # 先建立專案，取得 project_id，以 ID 作為資料夾名稱 左邊是crud中定義的，右邊是前端表單取得的
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

    new_project_id = new_project["id"] #拿到DB的ID編號

    # 處理檔案上傳
    attachment_url = None
    ai_result = None  # 🎯 [新增] 用來存 AI 結果的變數

    if attachment and attachment.filename: #檢查檔案存在/檔名是否為空
        project_folder = os.path.join(UPLOAD_DIR, f"project_{new_project_id}", "attachment")# 委託人專案檔案上傳，建立專案資料夾uploads>project_id>attachment
        os.makedirs(project_folder, exist_ok=True) # 確保資料夾存在

        file_path = os.path.join(project_folder, attachment.filename)# 剛才建好的資料夾路徑和原始檔名組合成完整路徑
        
        try:
            with open(file_path, "wb") as buffer:# 開啟檔案準備寫入buffer
                shutil.copyfileobj(attachment.file, buffer)# 把上傳的檔案內容寫入暫存區
            
            # --- 🤖 這裡開始 AI 介入 (同步版本) ---
            # 判斷一下是否為 PDF 或純文字 (圖片也可以，Gemini 支援)
            mime_type, _ = mimetypes.guess_type(file_path)# 用副檔名猜測類型
            if not mime_type: # 如果猜得出，if not true 就是false，就不會執行下面;程式沒猜到，if not false 就是true，預設為 PDF
                mime_type = "application/pdf" # 預設pdf

            print(f"🤖 AI 正在分析檔案: {attachment.filename} ...")
            
            # 呼叫 ai_service 分析
            # 如果 ai_service 失敗會回傳 None，這裡就直接接住 None
            ai_result = await ai_service.analyze_attachment(file_path, mime_type)
            
            if ai_result:# 有結果才印成功 none就印失敗
                print("✅ AI 分析完成！")
            else:
                print("⚠️ AI 分析未產生結果或失敗 (將不顯示於前台)")
            # ---------------------------

        finally:
            attachment.file.close() 
        
        attachment_url = f"/uploads/project_{new_project_id}/attachment/{attachment.filename}"# 組合成可從網頁存取的路徑

        # 更新資料庫：現在多傳入 ai_summary
        await crud.update_project(
            conn=conn, 
            project_id=new_project_id, 
            client_id=user["uid"],
            title=title, 
            description=description, 
            budget=budget, 
            deadline=deadline,
            attachment_url=attachment_url,
            ai_summary=ai_result  # 🎯 [修改] 把結果傳進去 (如果是 None 就會存 NULL)
        )
    
    return RedirectResponse(url="/client/dashboard", status_code=status.HTTP_302_FOUND)



# ------------------------------------------------------------
# 📦 路由 4: 專案管理頁面 (查看報價、選擇接案人、核准交付、退件)
# ------------------------------------------------------------
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
    
    bids = await crud.get_bids_for_project(conn, project_id)
    deliverables = await crud.get_deliverables_for_project(conn, project_id)
    
    # 👇 [新增] 撈取該專案的討論串 (Issues)
    threads = await crud.get_issues_by_project_id(conn, project_id)

    return templates.TemplateResponse("bid_list.html", {  
        "request": request,
        "project": project,
        "bids": bids,
        "deliverables": deliverables, 
        "threads": threads,          # 👈 [新增] 傳遞給模板
        "user_name": user["name"].strip()
    })


# --------------------------------------------------------
# ✅ 路由 5: 委託人選擇得標者
# --------------------------------------------------------
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
    
    # 這樣使用者進入討論列表或聊天室時，會直接看到「已解決」的綠色狀態
    await crud.resolve_all_issues_by_project(conn, project_id)
    
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
@router.post("/project/{project_id}/edit", response_class=HTMLResponse) # 注意：這裡也改為 HTMLResponse
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
    # 🔥 [UX優化] 編輯時的日期檢查
    if deadline < date.today():
        # 為了讓前端能正常顯示，我們需要模擬一個 project 物件傳回去
        # 這樣 HTML 中的 {{ project.title }} 才能讀到資料
        mock_project = {
            "id": project_id,
            "title": title,
            "description": description,
            "budget": budget,
            "deadline": deadline,
            "attachment_url": None # 暫時不回填檔案路徑，太複雜
        }
        return templates.TemplateResponse("project_edit.html", {
            "request": request,
            "error_message": "截止日期無效：不能修改為過去的日期！",
            "project": mock_project # 👈 這裡用 mock_project 騙過前端模板
        }, status_code=400)

    attachment_url = None
    if attachment and attachment.filename:
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
        # 如果沒有上傳新檔案，嘗試去 DB 撈舊的路徑保留
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
    
    open_projects = await crud.get_all_open_projects_with_bid_count(conn)
    
    return templates.TemplateResponse("client_browse_projects.html", {
        "request": request,
        "user_name": user["name"].strip(),
        "projects": open_projects
    })

# ==========================================
# 💬 聊天室 / 待辦事項路由 (Chat / Issues)
# ==========================================

# 🆕 路由 A: 建立新討論串
# routers/client.py

@router.post("/project/{project_id}/thread/create", response_class=RedirectResponse)
async def create_project_thread(
    project_id: int,
    request: Request,
    title: str = Form(...),
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 檢查權限
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="權限不足")

    # 🔥 修改：如果專案已結案 (completed) 或 還在招標中 (open)，都不可新增討論
    status_str = project["status"].strip()
    
    if status_str == 'completed':
        return HTMLResponse("專案已結案，無法新增討論。", status_code=400)
        
    if status_str == 'open':
        return HTMLResponse("專案尚在招標中，無法新增討論。", status_code=400)

    # 建立議題
    await crud.create_issue(conn, project_id, user["uid"], title)
    
    return RedirectResponse(url=f"/client/project/{project_id}/manage", status_code=status.HTTP_302_FOUND)


# 🆕 路由 B: 進入聊天室頁面 (chat_room.html)
@router.get("/project/{project_id}/thread/{thread_id}", response_class=HTMLResponse)
async def view_chat_room(
    project_id: int,
    thread_id: int,
    request: Request,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 檢查權限
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        return HTMLResponse("您沒有權限查看此專案的討論。", status_code=403)

    # 取得議題詳情
    thread = await crud.get_issue_by_id(conn, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="討論串不存在")

    # 取得歷史留言
    messages = await crud.get_comments_by_issue_id(conn, thread_id)

    # 渲染 chat_room.html
    return templates.TemplateResponse("chat_room.html", {
        "request": request,
        "project": project,
        "thread": thread,
        "messages": messages,
        "current_user": user,  # 傳入 current_user 供模板判斷是左邊還是右邊
    })


# 🆕 路由 C: 發送訊息
@router.post("/project/{project_id}/thread/{thread_id}/send", response_class=RedirectResponse)
async def send_chat_message(
    project_id: int,
    thread_id: int,
    content: str = Form(...),
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="權限不足")

    # 🔥 新增檢查：若專案已結案，強制禁止留言 (無論議題是否 open)
    if project["status"].strip() == 'completed':
        return HTMLResponse("專案已結案，無法再傳送訊息。", status_code=400)

    thread = await crud.get_issue_by_id(conn, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="討論串不存在")
        
    if thread["status"] == 'resolved':
         return HTMLResponse("此議題已解決，無法繼續留言。", status_code=400)

    if content.strip():
        await crud.create_issue_comment(conn, thread_id, user["uid"], content)

    return RedirectResponse(
        url=f"/client/project/{project_id}/thread/{thread_id}", 
        status_code=status.HTTP_302_FOUND
    )

# 👇 (新增這個路由) 路由 D: 將議題設為已解決
@router.post("/project/{project_id}/thread/{thread_id}/resolve", response_class=RedirectResponse)
async def resolve_thread_route(
    project_id: int,
    thread_id: int,
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 檢查權限
    project = await crud.get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["uid"]:
        raise HTTPException(status_code=403, detail="權限不足")

    # 執行更新
    await crud.resolve_issue(conn, thread_id)
    
    # 導回聊天室 (讓使用者看到介面變化)
    return RedirectResponse(
        url=f"/client/project/{project_id}/thread/{thread_id}", 
        status_code=status.HTTP_302_FOUND
    )


# ⭐ 處理委託人送出的評價 (POST)
@router.post("/project/{project_id}/review")
async def submit_client_review(
    project_id: int,
    score_1: int = Form(...), 
    score_2: int = Form(...), 
    score_3: int = Form(...), 
    comment: str = Form(""),
    conn: Connection = Depends(getDB),
    user: dict = Depends(get_current_user)
):
    # 1. 抓取專案資料
    project = await crud.get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")

    # 安全檢查：確認權限
    if project['client_id'] != user['uid']:
         raise HTTPException(status_code=403, detail="您沒有權限評價此專案")

    # 檢查專案是否已結案
    if project['status'] != 'completed':
        raise HTTPException(status_code=400, detail="專案尚未結案，無法評價")

    # 找出接案人 ID 
    contractor_id = project.get('accepted_contractor_id')
    
    if not contractor_id:
        raise HTTPException(status_code=400, detail="此專案沒有得標者，無法進行評價")
    
    # 期限檢查 (7天)
    if project['completed_at']:
        deadline = project['completed_at'] + timedelta(days=7)
        if datetime.now() > deadline:
            raise HTTPException(status_code=400, detail="已超過評價期限 (7天)，無法進行評價。")
    else:
        # 如果狀態是 completed 但沒有時間，代表資料庫資料有異常
        raise HTTPException(status_code=400, detail="專案結案時間資料異常")

    # 檢查是否重複評價
    if await crud.check_if_reviewed(conn, project_id, user['uid']):
        return RedirectResponse(url="/client/dashboard", status_code=303)

    # 4. 寫入評價
    await crud.create_review(
        conn=conn,
        project_id=project_id,
        reviewer_id=user['uid'],
        reviewee_id=contractor_id,
        role_type='client_to_contractor',
        s1=score_1, s2=score_2, s3=score_3,
        comment=comment
    )

    return RedirectResponse(url="/client/dashboard", status_code=303)


# 🆕 API: 取得某位使用者的評價資料 (供前端 Modal 使用)
@router.get("/api/user/{user_id}/reviews")
async def get_user_reviews_api(
    user_id: int, 
    conn: Connection = Depends(getDB)
):
    # 1. 取得統計
    stats = await crud.get_user_reputation_stats(conn, user_id)
    # 2. 取得詳細列表
    reviews = await crud.get_user_received_reviews_public(conn, user_id)
    
    return {
        "stats": stats,
        "reviews": reviews
    }
