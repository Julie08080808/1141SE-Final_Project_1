# main.py (*** 終極完整版 + 註解 ***) *1

# --- [ 1. 導入函式庫 ] ---
from fastapi import FastAPI, Request   #在 FastAPI 中，FastAPI這個類別負責處理 HTTP 請求和回應的所有邏輯。Request 這是處理 HTTP 請求的物件，通常在路由處理函數中作為參數來存取請求的內容，如 URL、標頭、表單數據等。
from fastapi.staticfiles import StaticFiles # 用於提供靜態檔案（如 CSS、JavaScript、圖片等）的服務。可以通過這個類來掛載一個資料夾，使得該資料夾中的文件可以被用戶端直接存取。
from fastapi.responses import HTMLResponse, RedirectResponse # 用於回傳網頁或返回 HTML 格式的回應。將用戶重定向到另一個 URL，實現頁面跳轉 
from fastapi.templating import Jinja2Templates # Jinja2Templates 用來渲染存儲在 templates 資料夾中的 HTML 模板，並可將資料（如字典）插入到模板中
import uvicorn # 用於啟動伺服器

#在FastAPI：用來創建 FastAPI 應用程式的核心類別。將使用 FastAPI() 來創建應用程式實例。
# Request 這是處理 HTTP 請求的物件，通常在路由處理函數中作為參數來存取請求的內容，如 URL、標頭、表單數據等。
# StaticFiles用於提供靜態檔案（如 CSS、JavaScript、圖片等）的服務。
# 可以通過這個類來掛載一個資料夾，使得該資料夾中的文件可以被用戶端直接存取。
# HTMLResponse用於回傳網頁或返回 HTML 格式的回應。
# RedirectResponse將用戶重定向到另一個 URL，實現頁面跳轉 
# Jinja2Templates 用來渲染存儲在 templates 資料夾中的 HTML 模板，並可將資料（如字典）插入到模板中
# uvicorn用於啟動伺服器


# 導入 Session (Starlette 是 FastAPI 的底層)
from starlette.middleware.sessions import SessionMiddleware 

#Session 是一種在用戶與網站互動期間儲存資訊的方式，通常用來儲存用戶的登入狀態、身份認證資料、偏好設置等。
# 它能讓應用程式跨多次請求跟蹤用戶的狀態，而不必每次都重新驗證用戶。
#SessionMiddleware 是 Starlette 的中介曾，會自動處理這些 session，將它們添加到每個請求的上下文中
# 並使得應用程式可以讀寫 session 資料。

# 導入我們所有 "routers" 資料夾中的路由
#Route 是用來「定義當使用者訪問某個網址時，要執行哪個程式（函式）」的規則。
from auth import router as auth_router
from routers.client import router as client_router       
from routers.contractor import router as contractor_router 
from routers.public import router as public_router     

# 這個 router 通常負責 登入、註冊、登出 相關的網址。
# 負責 委託人（client） 專屬的網址，例如 /client/dashboard、/client/history。
# 負責 接案人（contractor） 的功能頁，例如 /contractor/dashboard。
# 負責登入用戶共用的專案相關功能（查看專案詳情、提交報價、歷史紀錄）




# --- [ 2. 應用程式 (App) 初始化 ] ---

# 建立 FastAPI 應用程式實例
app = FastAPI()

# *** 關鍵：加上 Session 中介軟體 ***
# 這行程式碼讓你的 app "有記憶"
# 只要加上它，FastAPI 就會自動處理 request.session
app.add_middleware(
    SessionMiddleware,
    secret_key="your-super-secret-key", # 一個隨機的密鑰，用於加密 session
	max_age=None, # None = 瀏覽器關閉時 session 就過期
    same_site="lax",  # 控制 cookie 的跨站行為，- 允許一般導向（例如點連結）時帶 cookie，這樣使用者從其他網站點進來仍能保持登入狀態。
    https_only=False, # 開發時用 http，所以設為 False
)

# 載入 Jinja2 模板，告訴它去 "templates" 資料夾找 HTML
templates = Jinja2Templates(directory="templates")

# *** 關鍵：包含路由，把各個功能模組的「路由」整合進來 ***
# 把 auth.py, client.py 等檔案裡的路由"插"到主程式上
# 這樣 app 才知道 /login, /client/dashboard 這些網址
app.include_router(auth_router)
app.include_router(client_router)
app.include_router(contractor_router) 
app.include_router(public_router)




# --- [ 3. 基本頁面路由 (App-Level) ] ---

# 路由 A: 根目錄 ("/")
# 這是使用者一進網站 (http://localhost:8000/) 會到的地方
@app.get("/")
async def root(request: Request):     #async 讓你的程式能同時處理多個請求而不會卡住
    #把使用者送來的請求資料（像誰打開了網站、用什麼方法、帶了什麼 cookie ）傳進這個函式裡，讓你可以在程式裡讀取或使用它。
    # 檢查 session 裡有沒有 "user_uid"
    if request.session.get("user_uid"):
        # 如果有登入，就檢查角色
        # (我們用 .strip() 來避免 'client ' 這種空格 bug)
        if request.session.get("user_type").strip() == 'client':
            # 委託人 -> 導向到委託人儀表板
            return RedirectResponse(url="/client/dashboard")
        else:
            # 接案人 -> 導向到接案人儀表板
            return RedirectResponse(url="/contractor/dashboard") 
    
    # 沒登入 -> 導向到登入頁
    return RedirectResponse(url="/loginForm.html")  #一開始甚麼都沒有，會跳轉到登入頁面

# 路由 B: 登入頁面
# (這個路由只負責 "顯示" 登入表單，"處理" 表單的 POST 在 auth.py 裡)
@app.get("/loginForm.html", response_class=HTMLResponse)    #告訴 FastAPI「這個路由要回傳 HTML 頁面」
async def login_form(request: Request):
    # 渲染 "templates/loginForm.html"
    return templates.TemplateResponse("loginForm.html", {"request": request})

#這段程式定義了「登入頁」的路由，
#當使用者訪問 /loginForm.html 時，
#FastAPI 執行 login_form() 這個函式
#系統會從 templates/loginForm.html 讀取網頁模板，
#並用 Jinja2 渲染後回傳給瀏覽器顯示。


# 路由 C: 註冊頁面
@app.get("/register.html", response_class=HTMLResponse)
async def register_form(request: Request):
    # 渲染 "templates/register.html"
    return templates.TemplateResponse("register.html", {"request": request})

#使用者打開網址 /register.html
#FastAPI 執行 register_form() 函式
#從 templates/register.html 讀取 HTML 檔案
#把 request 傳進模板引擎渲染
#回傳完整的 HTML 給瀏覽器顯示註冊畫面



# --- [ 4. 靜態檔案掛載  ] ---

# 順序很重要！必須先掛載 /uploads，再掛載 /

# 讓 "uploads" 資料夾可以被網址 /uploads 存取
# 這樣 <a href="/uploads/my_file.pdf"> 才能運作
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

#app.mount() 是用來「掛載靜態檔案資料夾」——讓網站可以直接顯示裡面的檔案。
#表示網址開頭是 /uploads 的，都會對應到你專案裡的 uploads/ 資料夾。
#如果你有一個檔案：uploads/my_file.pdf那使用者就可以用瀏覽器直接打開。


app.mount("/", StaticFiles(directory="www"), name="static")

# 表示「根目錄」掛載到 www/ 資料夾上
# 讓 "www" (CSS, JS, 圖片) 可以被根目錄 / 存取
# 例如 /css/style.css -> 會去抓 www/css/style.css




# --- [ 5. 伺服器啟動 ] ---

# *** 關鍵：用這個方式啟動 ***
# 讓我們可以直接用 "python main.py" 執行
# 這能 100% 避免 Windows 上的 multiprocessing 崩潰
if __name__ == "__main__":
    uvicorn.run(
        "main:app", # 告訴 uvicorn 去找 "main.py" 裡的 "app" 物件
        host="127.0.0.1", 
        port=8000, 
        reload=True # 當你修改程式並存檔時，伺服器會自動重啟（開發超方便）。
    )