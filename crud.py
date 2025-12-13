# --- [ crud.py：資料庫操作層 (Data Access Layer) ] ---
# 📘 功能說明：
# 這個檔案負責與 PostgreSQL 資料庫溝通，
# 提供各模組 (auth.py, routers/client.py, routers/contractor.py...) 呼叫的資料處理函式。
# 使用 psycopg + async cursor 執行 SQL 查詢。
# crud.py (*** 真正終極完整版 v3.3 ***)

from psycopg import Connection
from datetime import date   #讓你處理「日期」相關的資料
from psycopg.rows import dict_row #讓查詢結果變成「字典格式」，方便以欄位名稱取值（而不是用索引位置）。

# --- Auth (身份驗證) ---
# 透過使用者名稱查詢使用者（登入時使用）
async def get_user_by_name(conn: Connection, name: str):
    async with conn.cursor() as cur:    # 建立游標物件
        await cur.execute("SELECT * FROM users WHERE name = %s", (name,))
        user = await cur.fetchone()
        # print("[DEBUG]", user)
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
# 接案人儀表板：取得所有公開中的專案(只抓 'open')
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
            b.contractor_id as accepted_contractor_id
        FROM projects p
        JOIN users u ON p.client_id = u.uid
        LEFT JOIN bids b ON p.accepted_bid_id = b.id 
        WHERE p.id = %s
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id,))
        project = await cur.fetchone()
        return project
    


# --- Bids (報價) ---

# 建立投標 (含重複檢查)
# 🎯 [修改] 新增 proposal_url 參數，並寫入資料庫
async def create_bid(conn: Connection, project_id: int, contractor_id: int, price: float, message: str, proposal_url: str | None = None):
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
        INSERT INTO bids (project_id, contractor_id, price, message, status, proposal_url)
        VALUES (%s, %s, %s, %s, 'pending', %s)
        RETURNING id
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id, contractor_id, price, message, proposal_url))
        await conn.commit()
        return await cur.fetchone()

# 取得專案所有投標紀錄（含接案人名稱）
# 🎯 [注意] 委託人在管理頁面需要看到 proposal_url
async def get_bids_for_project(conn: Connection, project_id: int):
    sql = """
        SELECT b.*, u.name as contractor_name,
        b.contractor_id,
            COALESCE(
                (SELECT AVG((score_1 + score_2 + score_3) / 3.0)
                 FROM reviews
                 WHERE reviewee_id = b.contractor_id
                ), 0
            ) as contractor_avg_score
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
            p.id as project_id, p.title as project_title, p.status as project_status,
            EXISTS(
                SELECT 1 FROM reviews r 
                WHERE r.project_id = p.id 
                AND r.reviewer_id = b.contractor_id
            ) as has_reviewed,
            CASE 
                WHEN p.completed_at IS NOT NULL 
                     AND (p.completed_at + INTERVAL '7 DAY' < NOW()) 
                THEN TRUE 
                ELSE FALSE 
            END as is_review_expired
        FROM bids b
        JOIN projects p ON b.project_id = p.id
        WHERE b.contractor_id = %s
        ORDER BY b.created_at DESC
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (contractor_id,))
        bids = await cur.fetchall()
        return bids

# 查詢某個承包商對某個專案的投標紀錄
# 🎯 [修改] 必須撈出 proposal_url 欄位
async def get_bid_by_project_and_contractor(conn, project_id: int, contractor_id: int):
    sql = """
        SELECT id, price, status, message, proposal_url
        FROM bids
        WHERE project_id = %s AND contractor_id = %s
        LIMIT 1
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id, contractor_id))
        row = await cur.fetchone()
        return dict(row) if row else None

# 檢查是否已投標
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
    sql = """
        SELECT 
            p.*, 
            u.name as contractor_name,
            b.price as final_price,
            (SELECT COUNT(*) FROM bids WHERE project_id = p.id) as bid_count,

            EXISTS(
                SELECT 1 FROM reviews r 
                WHERE r.project_id = p.id 
                AND r.reviewer_id = p.client_id
            ) as has_reviewed,

            -- 檢查是否超過 7 天評價期限
            CASE 
                WHEN p.completed_at IS NOT NULL 
                     AND (p.completed_at + INTERVAL '7 DAY' < NOW()) 
                THEN TRUE 
                ELSE FALSE 
            END as is_review_expired

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

# 1. 取得"委託人"的歷史紀錄
async def get_client_history(conn: Connection, client_id: int):
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

# 2. 取得"接案人"的歷史紀錄
async def get_contractor_history(conn: Connection, contractor_id: int):
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


# 1. 建立評價 (寫入資料庫)
async def create_review(conn: Connection, project_id: int, reviewer_id: int, reviewee_id: int, role_type: str, s1: int, s2: int, s3: int, comment: str):
    """
    建立一筆新的評價
    role_type: 'contractor_to_client' (接案評委託) 或 'client_to_contractor' (委託評接案)
    """
    sql = """
        INSERT INTO reviews 
        (project_id, reviewer_id, reviewee_id, role_type, score_1, score_2, score_3, comment, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        RETURNING id
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (
            project_id, reviewer_id, reviewee_id, role_type, s1, s2, s3, comment
        ))
        await conn.commit()
        return await cur.fetchone()

# 2. 檢查是否評價過 (避免重複評價)
async def check_if_reviewed(conn: Connection, project_id: int, reviewer_id: int):
    """
    檢查這個人 (reviewer_id) 是否已經對這個專案 (project_id) 評價過了
    """
    sql = "SELECT id FROM reviews WHERE project_id = %s AND reviewer_id = %s"
    async with conn.cursor() as cur:
        await cur.execute(sql, (project_id, reviewer_id))
        return await cur.fetchone()


# --- [ 取得我給出的所有評價 ] ---
async def get_my_given_reviews(conn: Connection, user_id: int):
    sql = """
        SELECT 
            r.id, r.project_id, p.title as project_title,
            r.role_type, r.score_1, r.score_2, r.score_3,
            r.comment, r.created_at, u.name as reviewee_name
        FROM reviews r
        JOIN projects p ON r.project_id = p.id
        JOIN users u ON r.reviewee_id = u.uid
        WHERE r.reviewer_id = %s
        ORDER BY r.created_at DESC
    """
    
    # ⭐ 關鍵改變：加上 row_factory=dict_row
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, (user_id,))
        
        # 直接回傳！它已經自動變成字典列表了，不用自己轉
        return await cur.fetchall()
    
# 1. 📊 新增：取得某使用者的「評價統計」 (平均分、總評數)
async def get_user_reputation_stats(conn: Connection, user_id: int):
    """
    回傳：總平均、總評數、以及三個維度的各自平均分
    """
    sql = """
        SELECT 
            COUNT(*) as total_count,
            AVG((score_1 + score_2 + score_3) / 3.0) as avg_score,
            AVG(score_1) as avg_score_1, -- 維度1平均
            AVG(score_2) as avg_score_2, -- 維度2平均
            AVG(score_3) as avg_score_3  -- 維度3平均
        FROM reviews
        WHERE reviewee_id = %s
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, (user_id,))
        stats = await cur.fetchone()
        
        # 數值處理：None 轉為 0.0，否則取小數點第 1 位
        keys = ['avg_score', 'avg_score_1', 'avg_score_2', 'avg_score_3']
        for k in keys:
            if stats[k] is None:
                stats[k] = 0.0
            else:
                stats[k] = round(stats[k], 1)
            
        return stats

# 2. 📝 新增：取得某使用者的「詳細評價列表」 (顯示給對方看)
async def get_user_received_reviews_public(conn: Connection, user_id: int):
    """
    取得該使用者收到的所有評價 (含評價者名稱、專案標題)
    """
    sql = """
        SELECT 
            r.score_1, r.score_2, r.score_3, r.comment, r.created_at,
            p.title as project_title,
            u.name as reviewer_name
        FROM reviews r
        JOIN projects p ON r.project_id = p.id
        JOIN users u ON r.reviewer_id = u.uid
        WHERE r.reviewee_id = %s
        ORDER BY r.created_at DESC
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, (user_id,))
        return await cur.fetchall()

