import os
import re
import json
import time
import random
import requests
import urllib.parse
import gspread
from google import genai
from fastapi import FastAPI, BackgroundTasks, HTTPException

app = FastAPI()

# ==========================================
# CẤU HÌNH HỆ THỐNG & API
# ==========================================
API_KEY_SHEET_ID = "1wzgeUWKlXe-QU-rDZLaLjIQxeXreNvbm3Fi88UZjXWM"
DATA_SHEET_ID = "1PeJmm8QvG85pAnlw8-NFco7MC-mxIc2wVncY6aCiifo"
CX_LINKEDIN = "a6be6e8ccdb58403b"
SECRET_TOKEN = "MySuperSecretToken123"  # Khóa bảo mật để gọi API

CHECK_DELAY = 0.5
SEARCH_DELAY = 1.5
GEMINI_DELAY_BASE = 6.0
GEMINI_DELAY_JITTER = 2.5

def sleep_with_jitter(base=GEMINI_DELAY_BASE, jitter=GEMINI_DELAY_JITTER):
    time.sleep(base + random.uniform(0, jitter))

# ==========================================
# HÀM KHỞI TẠO GSPREAD TỪ BIẾN MÔI TRƯỜNG
# ==========================================
def get_gspread_client():
    service_account_info = os.getenv("SERVICE_ACCOUNT_JSON")
    
    if service_account_info:
        # Khi chạy trên Render.com (Đọc trực tiếp từ Environment Variable)
        try:
            creds_dict = json.loads(service_account_info)
            return gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            print(f"❌ Lỗi parse SERVICE_ACCOUNT_JSON từ môi trường: {e}")
            raise e
    else:
        # Khi chạy thử ở máy cá nhân (Fallback dùng file local)
        print("⚠️ Không thấy SERVICE_ACCOUNT_JSON trong biến môi trường, sử dụng file service_account.json local...")
        return gspread.service_account(filename="service_account.json")

# ==========================================
# CLASS QUẢN LÝ KEY ROTATION
# ==========================================
class MultiTabKeyManager:
    def __init__(self, sheet, key_type):
        self._sheet = sheet
        self._type = key_type
        self._keys = []
        self._idx = 0
        self._clients = {}

    def load(self):
        rows = self._sheet.get_all_values()
        self._keys = []
        for i, r in enumerate(rows[3:]):
            row_num = i + 4
            if r and r[0].strip():
                status = r[1].strip() if len(r) > 1 else ""
                if status in ["Mã API hết lượt", "API het luot", "Key loi (401)"]:
                    continue
                self._keys.append({"key": r[0].strip(), "row": row_num})
        print(f"📊 [{self._type}] Nạp thành công {len(self._keys)} keys khả dụng.")

    def current(self):
        return self._keys[self._idx] if self._idx < len(self._keys) else None

    def current_key(self):
        item = self.current()
        return item["key"] if item else None

    def get_client(self):
        key = self.current_key()
        if key is None:
            return None
        if key not in self._clients:
            self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    def exhaust(self):
        if self._idx < len(self._keys):
            self._mark(self._keys[self._idx]["row"], "429")
            self._idx += 1

    def invalidate(self):
        if self._idx < len(self._keys):
            self._mark(self._keys[self._idx]["row"], "401")
            self._idx += 1

    def _mark(self, row, kind):
        msg = "Key loi (401)" if kind == "401" else "API het luot"
        color = (
            {"red": 0.97, "green": 0.83, "blue": 0.12} if kind == "401"
            else {"red": 1.0, "green": 0.7, "blue": 0.7}
        )
        self._sheet.update(range_name=f"B{row}", values=[[msg]])
        self._sheet.format(f"A{row}", {"backgroundColor": color})
        print(f"🛑 [{self._type}] Hàng {row} đánh dấu: {msg}")

# ==========================================
# CÁC HÀM XỬ LÝ AI & SEARCH
# ==========================================
def verify_ceo_with_ai(company_name, name, job, url, gemini_mgr):
    prompt = f"""Nhiệm vụ: Xác minh xem người này có phải là CEO hoặc Founder của công ty không, đồng thời chuẩn hóa lại chức vụ.

Công ty cần tìm: {company_name}
Người tìm thấy: {name}
Chức vụ theo Google: {job}
LinkedIn URL: {url}

Trả lời ĐÚNG format JSON sau, không giải thích thêm:
{{"verified": true/false, "confidence": "cao/trung bình/thấp", "reason": "lý do ngắn gọn trong 1 câu", "job_title": "chức vụ đã được chuẩn hóa"}}"""

    attempt = 0
    while attempt < 5:
        attempt += 1
        if not gemini_mgr.current_key():
            return {"verified": None, "confidence": "không xác định", "reason": "Hết key AI", "job_title": job}

        try:
            client = gemini_mgr.get_client()
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
            )
            text = response.text.strip()
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                if not result.get("job_title"):
                    result["job_title"] = job
                return result
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                gemini_mgr.exhaust()
                continue
            if "401" in err_str or "API_KEY_INVALID" in err_str:
                gemini_mgr.invalidate()
                continue
            time.sleep(5)
    return {"verified": None, "confidence": "không xác định", "reason": "Lỗi AI", "job_title": job}

def get_location_gemini(ceo_name, company, linkedin_url, gemini_mgr):
    prompt = (
        f"What city and state/country does '{ceo_name}', "
        f"the CEO/Founder of '{company}' (LinkedIn: {linkedin_url}), "
        f"currently live in or work from? "
        f"Reply with ONLY city and state/country, example: 'San Francisco, CA'. "
        f"If unknown, reply '-'."
    )
    attempt = 0
    while attempt < 5:
        attempt += 1
        if not gemini_mgr.current_key():
            return "Hết key AI"

        try:
            client = gemini_mgr.get_client()
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt
            )
            return response.text.strip() if response.text else "-"
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                gemini_mgr.exhaust()
                continue
            if "401" in err_str or "API_KEY_INVALID" in err_str:
                gemini_mgr.invalidate()
                continue
            time.sleep(5)
    return "Error"

# ==========================================
# LOGIC CHẠY CHÍNH (LOGIC AUTOMATION)
# ==========================================
def run_automation_logic():
    print("🚀 Cronjob triggered: Bắt đầu kiểm tra Google Sheet...")
    try:
        gc = get_gspread_client()
        gemini_sheet = gc.open_by_key(API_KEY_SHEET_ID).worksheet("Gemini API")
        api_sheet = gc.open_by_key(API_KEY_SHEET_ID).worksheet("Custom Search API")
        data_sheet = gc.open_by_key(DATA_SHEET_ID).worksheet("search example")

        search_key_mgr = MultiTabKeyManager(api_sheet, "SEARCH")
        search_key_mgr.load()

        gemini_key_mgr = MultiTabKeyManager(gemini_sheet, "GEMINI")
        gemini_key_mgr.load()

        all_rows = data_sheet.get_all_values()

        # 1. BƯỚC 1: LỌC CÁC DÒNG CÓ CỘT B TRỐNG
        todo_search = []
        for i, row in enumerate(all_rows[1:]):  # Bỏ qua dòng tiêu đề
            row_idx = i + 2
            company = row[0].strip() if len(row) > 0 else ""
            col_b = row[1].strip() if len(row) > 1 else ""

            if company and col_b == "":
                todo_search.append({"idx": row_idx, "company": company})

        if not todo_search:
            print("✅ Tất cả cột B đã được xử lý, không có dòng mới.")
        else:
            print(f"📌 Tìm thấy {len(todo_search)} dòng chưa xử lý ở Cột B.")
            for task in todo_search:
                row_idx = task["idx"]
                company_query = task["company"]

                while True:
                    api_obj = search_key_mgr.current()
                    if not api_obj:
                        print("🛑 HẾT KEY SEARCH KHẢ DỤNG!")
                        break

                    api_key = api_obj['key']
                    query = f'"{company_query}" (CEO OR Founder) site:linkedin.com/in/'
                    url = f"https://www.googleapis.com/customsearch/v1?q={urllib.parse.quote(query)}&key={api_key}&cx={CX_LINKEDIN}"

                    try:
                        res = requests.get(url, timeout=10)
                        data = res.json()

                        if res.status_code in [403, 429] or "dailyLimitExceeded" in str(data):
                            search_key_mgr.exhaust()
                            continue
                        if res.status_code == 401:
                            search_key_mgr.invalidate()
                            continue

                        items = data.get("items", [])
                        if items:
                            item = items[0]
                            link = item.get("link", "-")
                            full_title = item.get("title", "")
                            clean_title = full_title.split("|")[0].split("...")[0].strip()
                            parts = [p.strip() for p in clean_title.split("-")]
                            name = parts[0] if len(parts) > 0 else "-"
                            job_raw = " - ".join(parts[1:]) if len(parts) > 1 else "-"

                            ai_result = verify_ceo_with_ai(company_query, name, job_raw, link, gemini_key_mgr)
                            
                            ai_status = "✅ Xác nhận" if ai_result.get("verified") is True else ("❌ Không xác nhận" if ai_result.get("verified") is False else "⚠️ Không rõ")
                            ai_confidence = ai_result.get("confidence", "-")
                            ai_reason = ai_result.get("reason", "-")

                            job_final = ai_result.get("job_title", job_raw) if (ai_result.get("verified") is True and ai_confidence.strip().lower() == "cao") else job_raw

                            payload = [link, name, job_final, f"{ai_status} ({ai_confidence})", ai_reason]
                            data_sheet.update(range_name=f"B{row_idx}:F{row_idx}", values=[payload])
                            print(f" ✅ [{row_idx}] Updated B:F cho {company_query}")

                        else:
                            data_sheet.update(range_name=f"B{row_idx}", values=[["- Không tìm thấy"]])
                            print(f" ➖ [{row_idx}] Không có kết quả cho {company_query}.")
                        
                        sleep_with_jitter()
                        break

                    except Exception as e:
                        print(f"❌ Lỗi xử lý dòng {row_idx}: {e}")
                        break

        # 2. BƯỚC 2: QUÉT VÀ CẬP NHẬT LOCATION (CỘT G)
        print("\n⏳ Quét kiểm tra Cột G (Location)...")
        all_rows_updated = data_sheet.get_all_values()
        
        for i, row in enumerate(all_rows_updated[1:]):
            row_idx = i + 2
            company = row[0].strip() if len(row) > 0 else ""
            linkedin_url = row[1].strip() if len(row) > 1 else ""
            ceo_name = row[2].strip() if len(row) > 2 else ""
            confidence_status = row[4].strip() if len(row) > 4 else ""
            location_filled = len(row) > 6 and row[6].strip()

            if ("xác nhận" in confidence_status.lower() and "(cao)" in confidence_status.lower()) and not location_filled and ceo_name:
                location = get_location_gemini(ceo_name, company, linkedin_url, gemini_key_mgr)
                data_sheet.update(range_name=f"G{row_idx}", values=[[location]])
                print(f" 📍 [{row_idx}] Updated Location Cột G: {location}")
                sleep_with_jitter()

        print("🏁 Cronjob completed.")

    except Exception as general_err:
        print(f"❌ Tiến trình gặp lỗi nghiêm trọng: {general_err}")

# ==========================================
# ENDPOINT CHO FASTAPI
# ==========================================
@app.get("/")
def home():
    return {"status": "Service is running!"}

@app.get("/run-job")
def trigger_job(background_tasks: BackgroundTasks, token: str = ""):
    if token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    background_tasks.add_task(run_automation_logic)
    return {"message": "Job successfully triggered in background!"}
