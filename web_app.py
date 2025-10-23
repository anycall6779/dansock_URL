import pandas as pd
import os
import sys
from datetime import datetime
import re
import glob
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
import threading # 엑셀 파일 동시 접근 방지를 위한 라이브러리
import urllib.parse # URL 한글/공백 처리를 위한 라이브러리

# --- 필요한 라이브러리 자동 설치 ---
def install_package(package):
    """지정된 패키지를 설치합니다."""
    print(f"필수 라이브러리 '{package}'를 설치합니다...")
    import subprocess
    try:
        # pip가 PATH에 없어도 실행되도록 sys.executable 사용
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"'{package}' 설치 완료.")
    except Exception as e:
        print(f"'{package}' 설치 실패: {e}")
        print("스크립트를 중지합니다. 수동으로 설치해주세요. (예: pip install {package})")
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
# ---------------------------------

# --- Google Cloud 인증 설정 ---
SERVICE_ACCOUNT_FILE = os.path.join("CLOUD VISION API", "API.json")
script_dir = os.path.dirname(os.path.abspath(__file__))
full_path_to_key = os.path.join(script_dir, SERVICE_ACCOUNT_FILE)

if not os.path.exists(full_path_to_key):
    print(f"[!!! 치명적 오류] 서비스 계정 키 파일이 없습니다: '{full_path_to_key}'")
    sys.exit(1)
    
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = full_path_to_key
vision_client = vision.ImageAnnotatorClient()
print("--- Google Vision API 클라이언트 로드 완료 ---")
# ---------------------------------

# --- 기본 설정 ---
# (요청: 드롭다운 순서 변경)
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
if not os.path.exists(UPLOAD_FOLDER_BASE):
    os.makedirs(UPLOAD_FOLDER_BASE)

app = Flask(__name__) # Flask 앱 생성
app.config['UPLOAD_FOLDER_BASE'] = UPLOAD_FOLDER_BASE
excel_lock = threading.Lock() # 엑셀 파일 동시 접근 방지를 위한 잠금
# ---------------------------------

# --- 도우미 함수들 ---
def clean_plate_text(text):
    """(요청: 숫자만 추출하는 로직 반영됨)"""
    cleaned_text = re.sub(r'[\s\n\.-]', '', text)
    # 1. 신형 번호판
    match = re.search(r'\d{2,3}[가-힣]{1}\d{4}', cleaned_text)
    if match: return match.group(0)
    # 2. 구형 번호판
    match = re.search(r'[가-힣]{2}\d{2}[가-힣]{1}\d{4}', cleaned_text)
    if match: return match.group(0)
    # 3. 형식 불일치 시 숫자만 추출
    numbers_only = re.sub(r'\D', '', cleaned_text)
    if numbers_only: return numbers_only
    # 4. 숫자도 없으면 빈칸
    return ""

def detect_plate_google_vision(img_path):
    """Google Vision API로 텍스트 감지"""
    try:
        with open(img_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)
        response = vision_client.text_detection(image=image)
        if response.error.message:
            raise Exception(f"API Error: {response.error.message}")
        if response.text_annotations:
            return clean_plate_text(response.text_annotations[0].description)
        return ""
    except Exception as e:
        print(f"[!] OCR 처리 오류 ({os.path.basename(img_path)}): {e}")
        return ""

def save_to_excel(entries_list, file_name):
    """데이터를 엑셀 파일에 저장 (스레드 잠금)"""
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
            print(f"[!!! 오류] '{file_name}' 파일이 엑셀로 열려있습니다!")
            return False
# ---------------------------------

# --- Flask 라우트 (웹페이지 로직) ---

# 1. 메인 페이지 (업로드 폼)
@app.route('/')
def index():
    return render_template('index.html', locations=LOCATIONS, reasons=REASONS)


# 2. 파일 업로드 및 OCR 처리
@app.route('/upload', methods=['POST'])
def upload_files():
    if request.method == 'POST':
        location = request.form['location']
        reason = request.form['reason']
        uploaded_files = request.files.getlist('photos')
        
        # (요청: 'uploads/날짜/동이름/사유' 경로)
        today_str = datetime.now().strftime('%Y.%m.%d')
        safe_location_name = location.replace('/', '-').replace('\\', '-')
        safe_reason_name = reason.replace('/', '-').replace('\\', '-') 
        target_folder = os.path.join(app.config['UPLOAD_FOLDER_BASE'], 
                                     today_str, 
                                     safe_location_name, 
                                     safe_reason_name)
        os.makedirs(target_folder, exist_ok=True)
        
        ocr_results = []
        
        print(f"--- OCR 처리 시작 ---")
        total_files = len(uploaded_files)
        for i, file in enumerate(uploaded_files):
            if file and file.filename:
                filename = file.filename 
                temp_path = os.path.join(target_folder, filename)
                file.save(temp_path)
                
                print(f"  -> ({i+1}/{total_files}) '{filename}' 처리 중... (Google API 호출)")
                detected_plate = detect_plate_google_vision(temp_path)
                
                # (요청: 이미지 미리보기 오류 수정)
                # 1. OS 경로를 웹 경로로 변경
                relative_path = os.path.relpath(temp_path, app.config['UPLOAD_FOLDER_BASE'])
                web_path = relative_path.replace(os.path.sep, '/')
                # 2. 한글/공백을 URL 인코딩
                safe_web_path = urllib.parse.quote(web_path)
                
                ocr_results.append({
                    'filename': filename,
                    'plate': detected_plate,
                    'image_url': f"/uploads/{safe_web_path}" # 인코딩된 경로 사용
                })

        print(f"--- OCR 처리 완료 ---")
        report_text = f"{location} {reason} 입니다."

        # OCR 결과를 result.html로 렌더링
        return render_template('result.html', 
                               location=location,
                               reason=reason,
                               report_text=report_text,
                               results=ocr_results)


# 3. 최종 엑셀 저장
@app.route('/save', methods=['POST'])
def save_results():
    if request.method == 'POST':
        location = request.form['location']
        reason = request.form['reason']
        report_text = request.form['report_text']
        today = datetime.now().strftime('%Y-%m-%d')
        file_name = f"주차단속내역_{today.replace('-', '')}.xlsx"
        
        # (요청: 엑셀 다운로드 404 오류 수정)
        # 엑셀 저장 경로를 스크립트 폴더(script_dir) 기준으로 변경
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
            save_success = save_to_excel(entries_to_save, full_excel_path)
            if not save_success:
                return "<h1>[오류] 엑셀 파일 저장에 실패했습니다. (파일이 열려있는지 확인하세요)</h1>"
        else:
            print("저장할 항목이 없습니다. (모두 's' 또는 빈칸)")

        # 저장 완료 후 success.html을 렌더링
        return render_template('success.html', 
                               report_text=report_text, 
                               excel_file=file_name,
                               count=len(entries_to_save))

# 4. 엑셀 파일 다운로드 기능
@app.route('/download/<filename>')
def download_file(filename):
    # (요청: 엑셀 다운로드 404 오류 수정)
    # 스크립트 폴더(script_dir)에서 엑셀 파일을 찾아 다운로드
    return send_from_directory(directory=script_dir, 
                               path=filename, 
                               as_attachment=True)

# 5. 업로드된 이미지 파일을 서빙 (미리보기용)
@app.route('/uploads/<path:path>')
def send_upload(path):
    """
    (요청: 이미지 미리보기 오류 수정)
    uploads 폴더의 파일을 브라우저로 전송합니다.
    Flask가 'path' 변수에 있는 URL 인코딩(예: %20)을 자동으로 디코딩해줍니다.
    """
    return send_from_directory(app.config['UPLOAD_FOLDER_BASE'], path)

# 6. (요청: '최종 보고서' 기능)
@app.route('/report')
def daily_report():
    """
    오늘 날짜의 엑셀 파일을 읽어, 
    모든 내역을 요약한 report.html 페이지를 반환합니다.
    """
    try:
        # 1. 오늘 날짜의 엑셀 파일 이름 찾기
        today = datetime.now().strftime('%Y-%m-%d')
        file_name = f"주차단속내역_{today.replace('-', '')}.xlsx"
        full_excel_path = os.path.join(script_dir, file_name)

        if not os.path.exists(full_excel_path):
            return "<h1>오류: 오늘 날짜의 엑셀 파일이 없습니다.</h1> <a href='/'>돌아가기</a>"

        # 2. Pandas로 엑셀 파일 읽기
        df = pd.read_excel(full_excel_path)

        if df.empty:
             return "<h1>정보: 엑셀 파일에 아직 데이터가 없습니다.</h1> <a href='/'>돌아가기</a>"

        # 3. '단속위치'와 '사유'로 그룹화하여 차량 대수(size) 계산
        summary = df.groupby(['단속위치', '사유']).size().reset_index(name='count')
        
        # 4. 템플릿에 전달할 리스트로 변환
        summary_data = summary.to_dict('records')
        
        # 5. 오늘 날짜 문자열
        report_date_str = datetime.now().strftime('%Y년 %m월 %d일')

        # 6. report.html 템플릿 렌더링
        return render_template('report.html',
                               report_date=report_date_str,
                               summary_data=summary_data)

    except Exception as e:
        print(f"[!!!] 보고서 생성 오류: {e}")
        return f"<h1>보고서 생성 중 오류 발생: {e}</h1> <a href='/'>돌아가기</a>"

# --- 웹 서버 실행 ---
if __name__ == '__main__':
    print("--- 웹 서버를 시작합니다 ---")
    print(f"--- 접속 주소: http://[서버 IP 주소]:5000 ---")
    # debug=False가 실제 운영 환경에 더 적합합니다.
    app.run(host='0.0.0.0', port=5000, debug=False)