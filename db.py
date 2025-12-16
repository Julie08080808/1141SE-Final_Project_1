#建立「非同步資料庫連線池」，並提供一個 getDB() 函式，讓 FastAPI 其他模組可以透過 Depends(getDB) 取得連線、執行查詢，
#而且能自動管理連線開啟與釋放，提升效能與穩定性。


from psycopg_pool import AsyncConnectionPool #使用connection pool  非同步連線池（Async = 不阻塞）
from psycopg.rows import dict_row   #讓查詢結果變成「字典格式」，方便以欄位名稱取值（而不是用索引位置）。
# db.py
defaultDB="114SE1"
dbUser="postgres"
dbPassword="jay940101"# 延伸三測試
dbHost="localhost"
dbPort=5432   # PostgreSQL 預設埠號

DATABASE_URL = f"dbname={defaultDB} user={dbUser} password={dbPassword} host={dbHost} port={dbPort}"
#DATABASE_URL = f"postgresql://{dbUser}:{dbPassword}@{dbHost}:{dbPort}/{defaultDB}"
#我要進行連線

#宣告變數，預設為None，等待第一次呼叫
_pool: AsyncConnectionPool | None = None

#取得DB連線物件
#提供一個 getDB() 函式，讓 FastAPI 其他模組可以透過 Depends(getDB) 取得連線、執行查詢
async def getDB():
	global _pool   #使用 global _pool 讓函式可以操作外部變數 _pool
	if _pool is None:
		#lazy create, 等到main.py來呼叫時再啟用 _pool
		_pool = AsyncConnectionPool(
			conninfo=DATABASE_URL,
			kwargs={"row_factory": dict_row}, #設定查詢結果以dictionary方式回傳
			open=False #不直接開啟
		)
		await _pool.open() #等待開啟完成
	#使用with context manager，當結束時自動關閉連線
	async with _pool.connection() as conn:
		#使用yeild generator傳回連線物件
		yield conn

#如果 _pool 尚未建立，就初始化一個新的連線池。
#conninfo=DATABASE_URL → 使用前面組好的連線資訊。
#每次查詢回傳的資料會是字典格式。
#open=False → 先建立連線池物件，不馬上啟用。
#await _pool.open() → 等待池子開啟（正式連線資料庫）。
#async with _pool.connection()  → 從連線池借出一個連線（conn）。
#當 with 區塊結束時會自動歸還連線（不需手動關閉）。
#yield conn  使用「生成器（generator）」的方式回傳連線物件。這樣 FastAPI 在執行完查詢後會自動釋放連線，避免記憶體洩漏。
