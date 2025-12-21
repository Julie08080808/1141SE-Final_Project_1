# --- [ ai_service.py ] ---
import google.generativeai as genai
import os

# 這個函數接收兩個資料，第一個是檔案路徑（必填），第二個是檔案類型（不填的話預設是 PDF），然後會回傳一段文字或空值。
async def analyze_attachment(file_path: str, mime_type: str = "application/pdf") -> str | None: # 🎯 修改回傳型態提示
    # 1. 從環境變數抓取 Key
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key:
        print("❌ 錯誤: 未偵測到 GEMINI_API_KEY")
        return None # 🎯 沒 Key 也回傳 None，不讓前端顯示錯誤

    # 2. 設定與建立模型
    try:
        genai.configure(api_key=api_key)
        # 🎯 [修改] 依照你的指示，改用 gemini-2.5-flash
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # 3. 上傳檔案
        uploaded_file = genai.upload_file(path=file_path, mime_type=mime_type)

        # 4. 設定 Prompt
        prompt = """
        你是一個專業的軟體專案經理與系統分析師。
        請閱讀這份附件檔案（可能是需求書、架構圖或作業說明），
        並為「接案工程師」生成一份簡短的架構摘要。

        請包含以下內容（若檔案中未提及則跳過）：
        1. 📌 文件核心目標：這份文件要求做什麼？
        2. 🛠 技術需求：有提到特定的語言、框架或資料庫嗎？
        3. 📂 檔案結構/功能模組：文件裡提到的主要功能區塊有哪些？
        4. ⚠️ 注意事項：有無特別的限制？

        請用繁體中文回答，保持簡潔清晰，並且生成的回覆盡量在100字內，不需要使用*來加強表示文件核心目標等事項。
        """

        # 5. 發送請求 提取文字部分
        response = model.generate_content([prompt, uploaded_file])
        return response.text

    except Exception as e:
        # 🎯 [修改] 這裡只印在後台看，回傳 None 給前端
        # 這樣資料庫就會是 NULL，前端網頁就不會顯示任何東西
        print(f"❌ AI 分析失敗 (已隱藏錯誤訊息): {e}")
        return None