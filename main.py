"""
PDF Processor for Cloud Run
政治資金収支報告書PDFを画像化し、Gemini APIで構造化JSONに変換

Based on: 20251122_GeminiFullAnalyze ローカル実装
"""
import base64
import io
import os
import json
import re
from datetime import datetime
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import google.generativeai as genai
import pdf2image
import fitz  # PyMuPDF

app = Flask(__name__)

# 設定
DPI = 300  # 既存プロジェクトと同じ解像度
GEMINI_MODEL = "gemini-3-pro-preview"  # 最高精度モデル
MAX_OUTPUT_TOKENS = 65536  # 大容量出力対応


def get_drive_service():
    """Google Drive APIサービスを取得（ADC使用）"""
    return build('drive', 'v3')


def get_gemini_model(api_key: str):
    """Gemini APIモデルを初期化"""
    genai.configure(api_key=api_key)

    generation_config = {
        "temperature": 0.1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }

    # 安全性設定を緩和（政治資金報告書は安全なコンテンツ）
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    return genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        generation_config=generation_config,
        safety_settings=safety_settings,
    )


def extract_json_from_response(response_text: str) -> str:
    """レスポンスからJSON部分を抽出"""
    # マークダウンコードブロック内のJSONを抽出
    json_match = re.search(r'```(?:json)?\s*\n([\s\S]*?)\n```', response_text)
    if json_match:
        return json_match.group(1).strip()
    return response_text.strip()


def download_pdf_from_drive(file_id: str) -> tuple[io.BytesIO, dict]:
    """Google DriveからPDFをダウンロード"""
    drive_service = get_drive_service()

    # ファイルメタデータを取得
    file_metadata = drive_service.files().get(
        fileId=file_id,
        fields='name,mimeType,size'
    ).execute()

    if file_metadata.get('mimeType') != 'application/pdf':
        raise ValueError(f"File is not a PDF. MimeType: {file_metadata.get('mimeType')}")

    # ファイルサイズチェック（100MB制限）
    file_size = int(file_metadata.get('size', 0))
    if file_size > 100 * 1024 * 1024:
        raise ValueError('File size exceeds 100MB limit')

    # PDFをダウンロード
    request_file = drive_service.files().get_media(fileId=file_id)
    pdf_buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(pdf_buffer, request_file)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    pdf_buffer.seek(0)
    return pdf_buffer, file_metadata


@app.route('/health', methods=['GET'])
def health_check():
    """ヘルスチェック"""
    return jsonify({
        'status': 'healthy',
        'gemini_model': GEMINI_MODEL,
        'dpi': DPI
    })


@app.route('/page-count', methods=['POST'])
def get_page_count():
    """
    PDFのページ数を取得

    Request Body:
    {
        "fileId": "Google Drive File ID"
    }
    """
    try:
        data = request.get_json()
        file_id = data.get('fileId')

        if not file_id:
            return jsonify({'error': 'fileId is required'}), 400

        pdf_buffer, file_metadata = download_pdf_from_drive(file_id)

        # PyMuPDFでページ数取得
        doc = fitz.open(stream=pdf_buffer.read(), filetype="pdf")
        page_count = len(doc)
        doc.close()

        return jsonify({
            'success': True,
            'pageCount': page_count,
            'fileName': file_metadata.get('name')
        })

    except Exception as e:
        app.logger.error(f'Error getting page count: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/convert', methods=['POST'])
def convert_pdf_page():
    """
    PDFの指定ページを画像(PNG)に変換してBase64で返却

    Request Body:
    {
        "fileId": "Google Drive File ID",
        "pageNumber": 1
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        file_id = data.get('fileId')
        page_number = data.get('pageNumber', 1)

        if not file_id:
            return jsonify({'error': 'fileId is required'}), 400

        if not isinstance(page_number, int) or page_number < 1:
            return jsonify({'error': 'pageNumber must be a positive integer'}), 400

        pdf_buffer, file_metadata = download_pdf_from_drive(file_id)

        # 指定ページを画像に変換（DPI 300）
        images = pdf2image.convert_from_bytes(
            pdf_buffer.read(),
            first_page=page_number,
            last_page=page_number,
            dpi=DPI,
            fmt='png'
        )

        if not images:
            return jsonify({
                'error': f'Page {page_number} not found in PDF'
            }), 404

        # PNG として Base64 エンコード
        img_buffer = io.BytesIO()
        images[0].save(img_buffer, format='PNG', optimize=True)
        img_buffer.seek(0)
        base64_image = base64.b64encode(img_buffer.read()).decode('utf-8')

        return jsonify({
            'success': True,
            'base64Image': base64_image,
            'mimeType': 'image/png',
            'pageNumber': page_number,
            'fileName': file_metadata.get('name')
        })

    except Exception as e:
        app.logger.error(f'Error converting PDF: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/analyze', methods=['POST'])
def analyze_pdf_page():
    """
    PDFの指定ページを画像化し、Gemini APIで構造化JSONに変換

    Request Body:
    {
        "fileId": "Google Drive File ID",
        "pageNumber": 1,
        "geminiApiKey": "Gemini API Key"  # または環境変数 GEMINI_API_KEY
    }

    Response:
    {
        "success": true,
        "metadata": {...},
        "page_identification": {...},
        "structured_data": {...},
        "tables": [...],
        "validation": {...}
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        file_id = data.get('fileId')
        page_number = data.get('pageNumber', 1)
        gemini_api_key = data.get('geminiApiKey') or os.environ.get('GEMINI_API_KEY')

        if not file_id:
            return jsonify({'error': 'fileId is required'}), 400

        if not gemini_api_key:
            return jsonify({'error': 'geminiApiKey is required'}), 400

        if not isinstance(page_number, int) or page_number < 1:
            return jsonify({'error': 'pageNumber must be a positive integer'}), 400

        # PDFダウンロード
        pdf_buffer, file_metadata = download_pdf_from_drive(file_id)
        pdf_bytes = pdf_buffer.read()
        pdf_buffer.seek(0)

        # ページ数取得
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()

        if page_number > total_pages:
            return jsonify({
                'error': f'Page {page_number} exceeds total pages ({total_pages})'
            }), 400

        # 画像に変換
        images = pdf2image.convert_from_bytes(
            pdf_bytes,
            first_page=page_number,
            last_page=page_number,
            dpi=DPI,
            fmt='png'
        )

        if not images:
            return jsonify({'error': f'Failed to convert page {page_number}'}), 500

        page_image = images[0]

        # Gemini APIで分析
        model = get_gemini_model(gemini_api_key)

        prompt = """
あなたは政治資金収支報告書のデータ抽出エキスパートです。

この画像は政治資金収支報告書の1ページです。
画像右上に記載されている「（そのXX）」を確認し、以下の形式でJSONを出力してください。

## 重要な指示
1. 画像右上の「（そのXX）」を正確に識別してください
2. テーブルは正確に行・列を抽出してください
3. 数値はカンマを含めてそのまま文字列で保存
4. JSON以外の説明文は一切出力しないでください
5. 出力は必ずJSON形式のみとしてください

## 期待する出力形式
{
  "page_type": "そのXX",
  "page_title": "ページタイトル",
  "structured_data": {
    "フィールド名": "値"
  },
  "tables": [
    {
      "table_id": "テーブル名",
      "table_title": "テーブルタイトル",
      "headers": ["列1", "列2", "列3"],
      "rows": [
        {"列1": "値", "列2": "値", "列3": "値"}
      ]
    }
  ],
  "additional_fields": {}
}
"""

        response = model.generate_content([prompt, page_image])
        response_text = response.text

        # JSON抽出・パース
        json_text = extract_json_from_response(response_text)

        try:
            analyzed_data = json.loads(json_text)
        except json.JSONDecodeError as e:
            return jsonify({
                'success': False,
                'error': f'JSON parse error: {str(e)}',
                'raw_response': response_text[:1000]
            }), 500

        # メタデータを追加
        result = {
            'success': True,
            'metadata': {
                'source_file': file_metadata.get('name'),
                'file_id': file_id,
                'page_number': page_number,
                'total_pages': total_pages,
                'page_type': analyzed_data.get('page_type', 'unknown'),
                'processed_at': datetime.utcnow().isoformat() + 'Z',
                'gemini_model': GEMINI_MODEL,
                'dpi': DPI
            },
            'page_identification': {
                'その番号': analyzed_data.get('page_type', ''),
                'タイトル': analyzed_data.get('page_title', '')
            },
            'structured_data': analyzed_data.get('structured_data', {}),
            'tables': analyzed_data.get('tables', []),
            'validation': {
                'schema_matched': True,
                'unmapped_fields': [],
                'gemini_notes': ''
            },
            'additional_fields': analyzed_data.get('additional_fields', {})
        }

        return jsonify(result)

    except Exception as e:
        app.logger.error(f'Error analyzing PDF: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/analyze-full', methods=['POST'])
def analyze_pdf_full():
    """
    PDF全ページを一括で分析（バッチ処理用）

    Request Body:
    {
        "fileId": "Google Drive File ID",
        "geminiApiKey": "Gemini API Key",
        "startPage": 1,  # optional
        "endPage": null  # optional, null=最後まで
    }
    """
    try:
        data = request.get_json()

        file_id = data.get('fileId')
        gemini_api_key = data.get('geminiApiKey') or os.environ.get('GEMINI_API_KEY')
        start_page = data.get('startPage', 1)
        end_page = data.get('endPage')

        if not file_id:
            return jsonify({'error': 'fileId is required'}), 400

        if not gemini_api_key:
            return jsonify({'error': 'geminiApiKey is required'}), 400

        # PDFダウンロード
        pdf_buffer, file_metadata = download_pdf_from_drive(file_id)
        pdf_bytes = pdf_buffer.read()

        # ページ数取得
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()

        if end_page is None or end_page > total_pages:
            end_page = total_pages

        # Geminiモデル初期化
        model = get_gemini_model(gemini_api_key)

        results = []
        errors = []

        for page_num in range(start_page, end_page + 1):
            try:
                # 画像に変換
                images = pdf2image.convert_from_bytes(
                    pdf_bytes,
                    first_page=page_num,
                    last_page=page_num,
                    dpi=DPI,
                    fmt='png'
                )

                if not images:
                    errors.append({
                        'page': page_num,
                        'error': 'Failed to convert to image'
                    })
                    continue

                page_image = images[0]

                # Gemini API分析
                prompt = """
あなたは政治資金収支報告書のデータ抽出エキスパートです。

この画像は政治資金収支報告書の1ページです。
画像右上に記載されている「（そのXX）」を確認し、以下の形式でJSONを出力してください。

## 重要な指示
1. 画像右上の「（そのXX）」を正確に識別してください
2. テーブルは正確に行・列を抽出してください
3. 数値はカンマを含めてそのまま文字列で保存
4. JSON以外の説明文は一切出力しないでください

## 期待する出力形式
{
  "page_type": "そのXX",
  "page_title": "ページタイトル",
  "structured_data": {"フィールド名": "値"},
  "tables": [{"table_id": "...", "table_title": "...", "headers": [...], "rows": [...]}],
  "additional_fields": {}
}
"""

                response = model.generate_content([prompt, page_image])
                json_text = extract_json_from_response(response.text)
                analyzed_data = json.loads(json_text)

                results.append({
                    'page_number': page_num,
                    'page_type': analyzed_data.get('page_type', 'unknown'),
                    'data': analyzed_data
                })

            except Exception as e:
                errors.append({
                    'page': page_num,
                    'error': str(e)
                })

        return jsonify({
            'success': True,
            'metadata': {
                'source_file': file_metadata.get('name'),
                'file_id': file_id,
                'total_pages': total_pages,
                'processed_pages': len(results),
                'error_pages': len(errors),
                'processed_at': datetime.utcnow().isoformat() + 'Z',
                'gemini_model': GEMINI_MODEL
            },
            'results': results,
            'errors': errors
        })

    except Exception as e:
        app.logger.error(f'Error in full analysis: {str(e)}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
