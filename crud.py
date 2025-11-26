# --- [ crud.py：資料庫操作層 (Data Access Layer) ] ---
# 📘 功能說明：
# 這個檔案負責與 PostgreSQL 資料庫溝通，
# 提供各模組 (auth.py, routers/client.py, routers/contractor.py...) 呼叫的資料處理函式。
# 使用 psycopg + async cursor 執行 SQL 查詢。
# crud.py (*** 真正終極完整版 v3.2 ***)
from psycopg import Connection
from datetime import date   #讓你處理「日期」相關的資料

# --- Auth (身份驗證) ---
# 透過使用者名稱查詢使用者（登入時使用）
async def get_user_by_name(conn: Connection, name: str):
    async with conn.cursor() as cur:    # 建立游標物件
        await cur.execute("SELECT * FROM users WHERE name = %s", (name,))
        user = await cur.fetchone()
        print("[DEBUG]", user)
        print("[DEBUG]", type(user))
        return user

# 透過使用者 ID 查詢（Session 驗證時使用）
async def get_user_by_id(conn: Connection, user_uid: int):
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM users WHERE uid = %s", (user_uid,)) 
        user = await cur.fetchone()
        return user

# 建立新使用者（註冊）
async def create_user(conn: Connection, name: str, password: str, user_type: str):
    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO users (name, password, user_type) VALUES (%s, %s, %s) RETURNING uid",
            (name, password, user_type) 
        )
        await conn.commit()
        new_user = await cur.fetchone()
        return new_user

  
# --- Client (委託人) ---
# 委託人建立新專案
async def create_project(
    conn: Connection, 
    client_id: int, 
    title: str, 
    description: str, 
    budget: float, 
    deadline: date
):
    sql = """
        INSERT INTO projects (client_id, title, description, budget, deadline, status)
        VALUES (%s, %s, %s, %s, %s, 'open')
        RETURNING id, title, description, budget, deadline, status, client_id 
        """
    async with conn.cursor() as cur:
        await cur.execute(sql, (client_id, title, description, budget, deadline))
        await conn.commit()
        new_project = await cur.fetchone()
        return new_project

# 更新委託人專案內容
async def update_project(
    conn: Connection, 
    project_id: int, 
    client_id: int, 
    title: str, 
    description: str, 
    budget: float, 
    deadline: date,
    attachment_url: str | None = None
):
    sql = """
        UPDATE projects
        SET title = %s, description = %s, budget = %s, deadline = %s, attachment_url = %s
        WHERE id = %s 
          AND client_id = %s 
          AND status = 'open'
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (title, description, budget, deadline, attachment_url, project_id, client_id))
        await conn.commit()
        return cur.rowcount


# 委託人：查看自己所有專案（含得標者與成交價）
# 新版 (v3.0): "同時" 抓取 "得標者" 的名字 和 "成交價格"
async def get_projects_by_client_id(conn: Connection, client_id: int):
    sql = """
        SELECT 
            p.*, 
            u.name as contractor_name,
            b.price as final_price 
        FROM projects p
        LEFT JOIN bids b ON p.accepted_bid_id = b.id
        LEFT JOIN users u ON b.contractor_id = u.uid
        WHERE p.client_id = %s
          AND p.status IN ('open', 'in_progress', 'submitted', 'completed')
        ORDER BY p.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (client_id,))
        projects = await cur.fetchall()
        return projects


# --- Contractor (接案人) ---
# # 接案人儀表板：取得所有公開中的專案(只抓 'open')
async def get_open_projects(conn: Connection):
    sql = """
        SELECT p.*, u.name as client_name
        FROM projects p
        JOIN users u ON p.client_id = u.uid
        WHERE p.status = 'open'
        ORDER BY p.deadline ASC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql)
        projects = await cur.fetchall()
        return projects
    

# 取得單一專案詳情（含附件與接案者資訊）
async def get_project_by_id(conn: Connection, project_id: int):
    sql = """
        SELECT 
            p.*, 
            u.name as client_name,
            b.contractor_id as accepted_contractor_id  -- <-- [ 新增 ]
        FROM projects p
        
        JOIN users u ON p.client_id = u.uid
        
        -- [ 新增 ] 我們用 LEFT JOIN，因為 'open' 專案還沒有 accepted_bid_id
        LEFT JOIN bids b ON p.accepted_bid_id = b.id 
        
        WHERE p.id = %s
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id,))
        project = await cur.fetchone()
        return project
    


# --- Bids (報價) ---

# 建立投標 (含重複檢查)
async def create_bid(conn: Connection, project_id: int, contractor_id: int, price: float, message: str):
    """建立投標 - 加入重複投標檢查"""
    # ✅ 先檢查是否已經投過標
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT COUNT(*) as count FROM bids WHERE project_id = %s AND contractor_id = %s",
            (project_id, contractor_id)
        )
        result = await cur.fetchone()
        if result['count'] > 0:
            raise ValueError("你已經對此專案投過標了,無法重複投標!")
    
    # 若無重複 → 寫入資料
    sql = """
        INSERT INTO bids (project_id, contractor_id, price, message, status)
        VALUES (%s, %s, %s, %s, 'pending')
        RETURNING id
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id, contractor_id, price, message))
        await conn.commit()
        return await cur.fetchone()

# 取得專案所有投標紀錄（含接案人名稱）
async def get_bids_for_project(conn: Connection, project_id: int):
    sql = """
        SELECT b.*, u.name as contractor_name
        FROM bids b
        JOIN users u ON b.contractor_id = u.uid
        WHERE b.project_id = %s
        ORDER BY b.created_at ASC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id,))
        bids = await cur.fetchall()
        return bids

# 委託人選擇得標投標
async def select_bid_for_project(conn: Connection, project_id: int, bid_id: int):
    async with conn.cursor() as cur:
        # 更新專案狀態
        await cur.execute(
            "UPDATE projects SET status = 'in_progress', accepted_bid_id = %s WHERE id = %s",
            (bid_id, project_id)
        )
        # 設為中標
        await cur.execute(
            "UPDATE bids SET status = 'accepted' WHERE id = %s",
            (bid_id,)
        )
        # 其他全部設為落選
        await cur.execute(
            "UPDATE bids SET status = 'rejected' WHERE project_id = %s AND id != %s",
            (project_id, bid_id)
        )
        await conn.commit()
        return True



# 接案人查看自己所有投標紀錄
async def get_bids_by_contractor_id(conn: Connection, contractor_id: int):
    sql = """
        SELECT 
            b.id as bid_id, b.price, b.status as bid_status,
            p.id as project_id, p.title as project_title, p.status as project_status
        FROM bids b
        JOIN projects p ON b.project_id = p.id
        WHERE b.contractor_id = %s
        ORDER BY b.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (contractor_id,))
        bids = await cur.fetchall()
        return bids

#  查詢某個承包商對某個專案的投標紀錄
async def get_bid_by_project_and_contractor(conn, project_id: int, contractor_id: int):
    sql = """
        SELECT id, price, status, message
        FROM bids
        WHERE project_id = %s AND contractor_id = %s
        LIMIT 1
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id, contractor_id))
        row = await cur.fetchone()
        return dict(row) if row else None

#   檢查是否已投標  (確認某個承包商是否已對某個專案投標) 
async def check_existing_bid(conn: Connection, project_id: int, contractor_id: int):
    """檢查是否已投標"""
    sql = "SELECT COUNT(*) as count FROM bids WHERE project_id = %s AND contractor_id = %s"
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id, contractor_id))
        result = await cur.fetchone()
        return result['count'] > 0


# 更新投標價格（限 pending 狀態）
async def update_bid_price(conn: Connection, bid_id: int, contractor_id: int, new_price: float):
    sql = """
        UPDATE bids
        SET price = %s
        WHERE id = %s 
          AND contractor_id = %s 
          AND status = 'pending'
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (new_price, bid_id, contractor_id))
        await conn.commit()
        return cur.rowcount

# --- Client (委託人) ---
# 新版：取得委託人的專案，同時統計投標數
async def get_projects_by_client_id_with_bid_count(conn: Connection, client_id: int):
    """
    取得委託人的所有專案，包含：
    - 基本專案資訊
    - 得標者名稱 (contractor_name)
    - 成交價格 (final_price)
    - 投標數量 (bid_count) ← 新增
    """
    sql = """
        SELECT 
            p.*, 
            u.name as contractor_name,
            b.price as final_price,
            (SELECT COUNT(*) FROM bids WHERE project_id = p.id) as bid_count
        FROM projects p
        LEFT JOIN bids b ON p.accepted_bid_id = b.id
        LEFT JOIN users u ON b.contractor_id = u.uid
        WHERE p.client_id = %s
          AND p.status IN ('open', 'in_progress', 'submitted', 'completed')
        ORDER BY p.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (client_id,))
        projects = await cur.fetchall()
        return projects

# --- Deliverables (交付) ---
# 接案人提交成果
async def create_deliverable(conn: Connection, project_id: int, contractor_id: int, file_url: str, note: str):
    async with conn.cursor() as cur:
        # 插入交付紀錄
        sql_insert = """
            INSERT INTO deliverables (project_id, contractor_id, file_url, note, status)
            VALUES (%s, %s, %s, %s, 'submitted')
            RETURNING id
        """
        await cur.execute(sql_insert, (project_id, contractor_id, file_url, note))
        new_deliverable = await cur.fetchone()
        
        # 更新專案狀態
        sql_update = """
            UPDATE projects
            SET status = 'submitted'
            WHERE id = %s AND status = 'in_progress'
        """
        await cur.execute(sql_update, (project_id,))
        
        await conn.commit()
        return new_deliverable

# 委託人查看該專案的所有交付紀錄
async def get_deliverables_for_project(conn: Connection, project_id: int):
    sql = """
        SELECT d.id, d.file_url, d.note, d.status, d.created_at, u.name as contractor_name
        FROM deliverables d
        JOIN users u ON d.contractor_id = u.uid
        WHERE d.project_id = %s
        ORDER BY d.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id,))
        deliverables = await cur.fetchall()
        return deliverables

# 委託人核准交付成果 → 專案完成
async def approve_deliverable_and_complete_project(conn: Connection, project_id: int, deliverable_id: int, client_uid: int):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE deliverables
            SET status = 'accepted', reviewed_by = %s, reviewed_at = now()
            WHERE id = %s
            """,
            (client_uid, deliverable_id)
        )
        await cur.execute(
            """
            UPDATE projects
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'submitted'
            """,
            (project_id,)
        )
        await conn.commit()
        return True

# 委託人退回交付成果
async def reject_deliverable(conn: Connection, project_id: int, deliverable_id: int, client_uid: int):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE deliverables
            SET status = 'rejected', reviewed_by = %s, reviewed_at = now()
            WHERE id = %s
            """,
            (client_uid, deliverable_id)
        )
        await cur.execute(
            """
            UPDATE projects
            SET status = 'in_progress'
            WHERE id = %s AND status = 'submitted'
            """,
            (project_id,)
        )
        await conn.commit()
        return True



# --- [ History (歷史紀錄) v3.2 - 完整欄位版本 ] ---

# 1. 取得"委託人"的歷史紀錄 (所有專案 + 得標者 + 完整時間資訊 + 投標數)
async def get_client_history(conn: Connection, client_id: int):
    """
    委託人歷史欄位：
    - 創立時間 (created_at)
    - 專案標題 (title)
    - 接案人 (contractor_name) 或投標數 (bid_count)
    - 專案狀態 (status)
    - 截止日期 (deadline)
    - 實際完成日期 (completed_at)
    """
    sql = """
        SELECT 
            p.id,
            p.title,
            p.description,
            p.budget,
            p.deadline,
            p.status,
            p.created_at,
            p.completed_at,
            p.attachment_url,
            u.name as contractor_name,
            (SELECT COUNT(*) FROM bids WHERE project_id = p.id) as bid_count
        FROM projects p
        LEFT JOIN bids b ON p.accepted_bid_id = b.id
        LEFT JOIN users u ON b.contractor_id = u.uid
        WHERE p.client_id = %s
        ORDER BY p.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (client_id,))
        return await cur.fetchall()


# 2. 取得"接案人"的歷史紀錄 (所有投標 + 委託人 + 完整時間資訊)
async def get_contractor_history(conn: Connection, contractor_id: int):
    """
    接案人歷史欄位：
    - 接案時間 (bid_created_at)
    - 專案標題 (title)
    - 委託人 (client_name)
    - 我的狀態 (my_bid_status + project_status)
    - 委託人預算 (budget)
    - 我的報價 (price)
    - 截止日期 (deadline)
    - 實際完成日期 (completed_at)
    """
    sql = """
        SELECT 
            p.id,
            p.title,
            p.description,
            p.budget,
            p.deadline,
            p.status,
            p.completed_at,
            b.id as bid_id,
            b.price,
            b.status as my_bid_status,
            b.created_at as bid_created_at,
            p.status as project_status,
            u.name as client_name
        FROM bids b
        JOIN projects p ON b.project_id = p.id
        JOIN users u ON p.client_id = u.uid
        WHERE b.contractor_id = %s
        ORDER BY b.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (contractor_id,))
        return await cur.fetchall()
    

# --- [ 取得所有公開招標專案（含投標數）] ---
async def get_all_open_projects_with_bid_count(conn: Connection):
    """
    取得所有公開招標中的專案，並統計投標數
    供委託人瀏覽參考
    """
    sql = """
        SELECT 
            p.id,
            p.title,
            p.description,
            p.budget,
            p.deadline,
            p.status,
            p.created_at,
            u.name as client_name,
            (SELECT COUNT(*) FROM bids WHERE project_id = p.id) as bid_count
        FROM projects p
        JOIN users u ON p.client_id = u.uid
        WHERE p.status = 'open'
        ORDER BY p.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql)
        return await cur.fetchall()
    
