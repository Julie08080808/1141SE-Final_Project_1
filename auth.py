# --- [ auth.py ] ---
# 🔐 登入 / 註冊 / 登出功能模組

# 1️⃣ 匯入 FastAPI 所需模組
from fastapi import APIRouter, Depends, Form, Request, HTTPException, status  
from fastapi.responses import RedirectResponse, HTMLResponse   # 用於跳轉與回傳
from psycopg import Connection 
from db import getDB          # 引入資料庫連線方法
import crud                   # 引入自訂的資料存取方法 (CRUD：Create, Read, Update, Delete)

# APIRouter 路由管理物件、Depends 依賴注入系統、讀取表單資料


# 2️⃣ 建立路由物件 (router)
#    用來將此模組的網址路由與主程式 (main.py) 整合
router = APIRouter() 


# =====================================================
# --- [ 登入狀態驗證功能 - Dependency 用法 ] ---
# =====================================================
# ✅ 函式：get_current_user()
# 這是一個「共用登入檢查工具」，可讓其他頁面用 Depends() 引用。
# 它會檢查 Session 裡是否有 user_uid（代表已登入）。
# 如果沒登入 → 會自動導向登入頁面。
async def get_current_user(request: Request, conn: Connection = Depends(getDB)):
    # 檢查 session 裡有沒有 user_uid
    #自動幫你建立資料庫連線。Depends(getDB) 會呼叫 db.py 的連線池函式。
    user_uid = request.session.get("user_uid") 
    if not user_uid:
        # 沒登入 → 導向登入頁面
        return RedirectResponse(url="/loginForm.html", status_code=status.HTTP_302_FOUND)
    
    # 從資料庫抓取該使用者資料
    user = await crud.get_user_by_id(conn, user_uid) 
    if not user:
        # 找不到此使用者 → 清除 Session 並導回登入頁
        request.session.clear()
        return RedirectResponse(url="/loginForm.html", status_code=status.HTTP_302_FOUND)
    
    # 驗證成功 → 回傳 user 資料 (讓其他函式使用)
    return user



# =====================================================
# --- [ 路由 A：登入處理 ] ---
# =====================================================
# 📍 路徑：/login   方法：POST
# 📄 說明：
#    當使用者在「登入表單」輸入帳號密碼並送出時，
#    就會由這個函式處理登入邏輯。
@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),   # 從表單中取得使用者輸入的帳號
    password: str = Form(...),   # 從表單中取得使用者輸入的密碼
    conn: Connection = Depends(getDB)  # 自動建立資料庫連線
):
    # 從資料庫找該帳號
    user = await crud.get_user_by_name(conn, username)
    

    # 檢查帳號密碼是否正確
    if not user or user["password"].strip() != password:
        # ❌ 帳號或密碼錯誤 → 回傳錯誤頁面
        return HTMLResponse("帳號或密碼錯誤 <a href='/loginForm.html'>重新登入</a>", status_code=401)
    
    # ✅ 登入成功 → 寫入 Session (讓系統記得誰登入)
    request.session["user_uid"] = user["uid"] 
    request.session["user_name"] = user["name"].strip()        # 使用者名稱
    request.session["user_type"] = user["user_type"].strip()   # 使用者角色 (client / contractor)
    
    # 根據角色導向不同儀表板
    if user["user_type"].strip() == 'client':
        # 委託人 → 導向到委託人儀表板
        return RedirectResponse(url="/client/dashboard", status_code=302) 
    else:
        # 接案人 → 導向到接案人儀表板
        return RedirectResponse(url="/contractor/dashboard", status_code=status.HTTP_302_FOUND)



# =====================================================
# --- [ 路由 B：註冊處理 ] ---
# =====================================================
# 📍 路徑：/register   方法：POST
# 📄 說明：
#    當使用者在「註冊頁面」輸入帳號密碼與身分後，
#    這裡會檢查帳號是否重複，若沒重複就建立新使用者。
@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),          # 註冊的帳號
    password: str = Form(...),          # 註冊的密碼
    user_type: str = Form(...),         # 註冊角色：'client' 或 'contractor'
    conn: Connection = Depends(getDB)
):
    # 檢查帳號是否已存在
    existing_user = await crud.get_user_by_name(conn, username)
    if existing_user:
        # ❌ 帳號重複 → 提示重新註冊
        return HTMLResponse("帳號已存在 <a href='/register.html'>重新註冊</a>", status_code=400)

    # ✅ 建立新使用者資料
    await crud.create_user(conn, name=username, password=password, user_type=user_type)

    # 註冊成功 → 導回登入頁
    return RedirectResponse(url="/loginForm.html", status_code=302)



# =====================================================
# --- [ 路由 C：登出功能 ] ---
# =====================================================
# 📍 路徑：/logout   方法：GET
# 📄 說明：
#    使用者點選「登出」時，清除 Session，
#    並導回登入頁面。
@router.get("/logout")
async def logout(request: Request):
    # 清除使用者的 session 登入資料
    request.session.clear()
    # 導回登入頁面
    return RedirectResponse(url="/loginForm.html")
