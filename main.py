import os
import re
import json
import time
import random
import requests
import urllib.parse
import gspread
from google import genai
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException

app = FastAPI()

# ==========================================
# CẤU HÌNH & HÀM XỬ LÝ (Giữ nguyên toàn bộ code logic của bạn)
# ==========================================
SERVICE_ACCOUNT_FILE = "service_account.json"
API_KEY_SHEET_ID = "1wzgeUWKlXe-QU-rDZLaLjIQxeXreNvbm3Fi88UZjXWM"
DATA_SHEET_ID = "1PeJmm8QvG85pAnlw8-NFco7MC-mxIc2wVncY6aCiifo"
CX_LINKEDIN = "a6be6e8ccdb58403b"
SECRET_TOKEN = "MySuperSecretToken123"  # Đặt 1 token bảo mật để tránh người ngoài gọi API vô tội vạ

def run_automation_logic():
    print("🚀 Cronjob triggered: Bắt đầu kiểm tra Google Sheet...")
    # ---> ĐẶT TOÀN BỘ LOGIC HÀM main_job() Ở ĐÂY <---
    # (Kết nối Service Account -> Lọc cột B còn trống -> Gọi API Custom Search & Gemini -> Cập nhật Sheet)
    print("🏁 Cronjob completed.")

# ==========================================
# ROUTE DÀNH CHO CRON-JOB.ORG GỌI VÀO
# ==========================================
@app.get("/")
def home():
    return {"status": "Service is running!"}

@app.get("/run-job")
def trigger_job(background_tasks: BackgroundTasks, token: str = ""):
    # Kiểm tra Token bảo mật
    if token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Chạy tác vụ dưới background để không làm timeout kết nối HTTP của cron-job.org
    background_tasks.add_task(run_automation_logic)
    return {"message": "Job successfully triggered in background!"}
