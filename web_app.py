import pandas as pd
import os
import sys
import shutil
from datetime import datetime
import re
import glob
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
import threading 
import urllib.parse 

# --- 필요한 라이브러리 자동 설치 ---
def install_package(package):
    print(f"필수 라이브러리 '{package}'를 설치합니다...")
    import subprocess
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"'{package}' 설치 완료.")
    except Exception as e:
        print(f"'{package}' 설치 실패: {e}")
        sys.exit(1)

try:
    import pandas
    import openpyxl
except ImportError:
    install_package("pandas openpyxl")
    import pandas
    import openpyxl

try:
    from google.cloud import vision
except ImportError:
    install_package("google-cloud-vision")
    from google.cloud import vision

try:
    from flask import Flask
except ImportError:
    install_package("Flask")
    from flask import Flask

# --- Google Cloud 인증 설정 ---
SERVICE_ACCOUNT_FILE = os.path.join("CLOUD VISION API", "API.json")
script_dir = os.path.dirname(os.path.abspath(__file__))
full_path_to_key = os.path.join(script_dir, SERVICE_ACCOUNT_FILE)

if not os.path.exists(full_path_to_key):
    print(f"[!!! 치명적 오류] 서비스 계정 키 파일이 없습니다: '{full_path_to_key}'")
    sys.exit(1)
    
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = full_path_to_key
# [수정 1] 전역 vision_client 삭제 (토큰 만료 방지)
print("--- Google Vision API 설정 완료 (호출 시 연결됨) ---")

# --- 기본 설정 ---
LOCATIONS = [
    "1동", "2동", "3동", "4동", "5동",
    "6동", "7동", "8동", "9동", "10동",
    "11동", "12동", "13동", "14동", "15동", 
    "중앙동", "민원동", "2청사"
]
REASONS = [
    "주차선 외 위반", "경차 구역 위반", "임산부 구역 위반",
    "방문객 전용 구역 위반", "전기차 구역 위반",
    "지하주차장 통로, 통행, 방해주차 위반",
    "장애인 구역 위반, 지정주차 구역(업무용포함)",
    "소방차 전용구역 위반", "주차금지구역위반 (필로티 등)"
]

UPLOAD_FOLDER_BASE = os.path.join(script_dir, 'uploads')
BACKUP_FOLDER = os.path.join(script_dir, 'backup')

if not os.path.exists(UPLOAD_FOLDER_BASE):
    os.makedirs(UPLOAD_FOLDER_BASE)
if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

app = Flask(__name__)
app.config['UPLOAD_FOLDER_BASE'] = UPLOAD_FOLDER_BASE
excel_lock = threading.Lock()

# --- 도우미 함수들 ---

def backup_old_files():
    """오늘 날짜가 아닌 엑셀 파일은 백업 폴더로 이동"""
    # print("--- 백업 확인 중 ---") # 로그 너무 많이 쌓임 방지
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    for file in os.listdir(script_dir):
        if file.startswith("주차단속내역_") and file.endswith(".xlsx"):
            if today_str not in file:
                try:
                    src = os.path.join(script_dir, file)
                    dst = os.path.join(BACKUP_FOLDER, file)
                    shutil.move(src, dst)
                    print(f"[자동 백업] {file} -> backup 폴더로 이동됨")
                except Exception as e:
                    print(f"[백업 실패] {file}: {e}")

def get_current_excel_filename():
    """예: 주차단속내역_2024-05-20_오전.xlsx"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ampm = "오전" if now.hour < 12 else "오후"
    return f"주차단속내역_{date_str}_{ampm}.xlsx"

def clean_plate_text(text):
    cleaned_text = re.sub(r'[\s\n\.-]', '', text)
    match = re.search(r'\d{2,3}[가-힣]{1}\d{4}', cleaned_text)
    if match: return match.group(0)
    match = re.search(r'[가-힣]{2}\d{2}[가-힣]{1}\d{4}', cleaned_text)
    if match: return match.group(0)
    numbers_only = re.sub(r'\D', '', cleaned_text)
    if numbers_only: return numbers_only
    return ""

# [수정 2] 장시간 실행 시 토큰 만료 방지 (함수 안에서 연결)
def detect_plate_google_vision(img_path):
    try:
        with open(img_path, 'rb') as image_file:
            content = image_file.read()
            
        # 여기서 매번 새로 연결 (서버 재시작 필요 없음)
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=content)
        
        response = client.text_detection(image=image)
        if response.error.message:
            raise Exception(f"API Error: {response.error.message}")
        if response.text_annotations:
            return clean_plate_text(response.text_annotations[0].description)
        return ""
    except Exception as e:
        print(f"[!] OCR 오류: {e}")
        return ""

def save_to_excel(entries_list, file_name):
    with excel_lock:
        try:
            df = pd.read_excel(file_name)
        except FileNotFoundError:
            df = pd.DataFrame(columns=["날짜", "단속위치", "사유", "차량번호"])
        
        new_df = pd.DataFrame(entries_list)
        df = pd.concat([df, new_df], ignore_index=True)
        
        try:
            df.to_excel(file_name, index=False, engine='openpyxl')
            return True
        except PermissionError:
            print(f"[!!! 오류] '{file_name}' 파일이 열려있습니다!")
            return False

# --- Flask 라우트 ---

@app.route('/')
def index():
    # [수정 3] 홈페이지 접속할 때마다 날짜 지난 파일 자동 정리
    backup_old_files()
    return render_template('index.html', locations=LOCATIONS, reasons=REASONS)

@app.route('/help')
def help_page():
    return render_template('help.html')

@app.route('/changelog')
def changelog_page():
    return render_template('changelog.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    if request.method == 'POST':
        location = request.form['location']
        reason = request.form['reason']
        uploaded_files = request.files.getlist('photos')
        
        try:
            ampm = request.form['ampm']
        except KeyError:
            now = datetime.now()
            ampm = "오전" if now.hour < 12 else "오후"
        
        today_str = datetime.now().strftime('%Y.%m.%d')
        safe_location = location.replace('/', '-')
        safe_reason = reason.replace('/', '-')
        
        target_folder = os.path.join(app.config['UPLOAD_FOLDER_BASE'], today_str, safe_location, ampm, safe_reason)
        os.makedirs(target_folder, exist_ok=True)
        
        ocr_results = []
        for file in uploaded_files:
            if file and file.filename:
                temp_path = os.path.join(target_folder, file.filename)
                file.save(temp_path)
                # 함수 내부에서 클라이언트 연결하므로 토큰 오류 없음
                detected_plate = detect_plate_google_vision(temp_path)
                
                rel_path = os.path.relpath(temp_path, app.config['UPLOAD_FOLDER_BASE'])
                web_path = rel_path.replace(os.path.sep, '/')
                safe_web_path = urllib.parse.quote(web_path)
                
                ocr_results.append({
                    'filename': file.filename,
                    'plate': detected_plate,
                    'image_url': f"/uploads/{safe_web_path}"
                })

        report_text = f"{location} {reason} ({ampm}) 입니다."
        return render_template('result.html', location=location, reason=reason, report_text=report_text, results=ocr_results)

@app.route('/save', methods=['POST'])
def save_results():
    if request.method == 'POST':
        location = request.form['location']
        reason = request.form['reason']
        report_text = request.form['report_text']
        today = datetime.now().strftime('%Y-%m-%d')
        
        file_name = get_current_excel_filename()
        full_excel_path = os.path.join(script_dir, file_name)
        
        entries_to_save = []
        for key in request.form.keys():
            if key.startswith('plate_'):
                final_plate = request.form[key]
                if final_plate and final_plate.lower() != 's':
                    new_entry = {
                        "날짜": today, "단속위치": location,
                        "사유": reason, "차량번호": final_plate
                    }
                    entries_to_save.append(new_entry)

        if entries_to_save:
            if not save_to_excel(entries_to_save, full_excel_path):
                return "<h1>[오류] 엑셀 파일이 열려있습니다. 닫고 다시 시도하세요.</h1>"
        
        return render_template('success.html', report_text=report_text, excel_file=file_name, count=len(entries_to_save))

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(directory=script_dir, path=filename, as_attachment=True)

@app.route('/uploads/<path:path>')
def send_upload(path):
    return send_from_directory(app.config['UPLOAD_FOLDER_BASE'], path)

@app.route('/report')
def daily_report():
    try:
        today_str = datetime.now().strftime('%Y-%m-%d')
        target_files = [f"주차단속내역_{today_str}_오전.xlsx", f"주차단속내역_{today_str}_오후.xlsx"]
        combined_df = pd.DataFrame()
        
        for fname in target_files:
            fpath = os.path.join(script_dir, fname)
            if os.path.exists(fpath):
                try:
                    df = pd.read_excel(fpath)
                    combined_df = pd.concat([combined_df, df], ignore_index=True)
                except Exception as e:
                    print(f"파일 읽기 오류 ({fname}): {e}")

        if combined_df.empty:
             return "<h1>오늘 생성된 단속 내역(오전/오후)이 없습니다.</h1> <a href='/'>돌아가기</a>"

        summary = combined_df.groupby(['단속위치', '사유']).size().reset_index(name='count')
        summary_data = summary.to_dict('records')
        report_date_str = datetime.now().strftime('%Y년 %m월 %d일')

        return render_template('report.html', report_date=report_date_str, summary_data=summary_data)
    except Exception as e:
        return f"<h1>오류 발생: {e}</h1> <a href='/'>돌아가기</a>"

# [추가] Cyrene 이미지
@app.route('/cyrene_img')
def serve_cyrene():
    img_folder = os.path.join(script_dir, 'templates', 'assest')
    return send_from_directory(img_folder, 'cyrene.webp')

# [추가] 가이드 다운로드 (조건 없이)
@app.route('/download_guide/<filename>')
def download_guide_file(filename):
    print(f"다운로드 요청: {filename}")
    try:
        return send_from_directory(script_dir, filename, as_attachment=True)
    except Exception as e:
        return f"파일 없음: {filename}", 404

if __name__ == '__main__':
    print(f"--- 서버 시작: http://localhost:5000 ---")
    app.run(host='0.0.0.0', port=5000, debug=False)