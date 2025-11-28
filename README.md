# 政治資金収支報告書 PDF to JSON Converter

Cloud Run上で動作するPDF解析サービス。政治資金収支報告書PDFを画像化し、Gemini APIで構造化JSONに変換します。

## 機能

- PDFページ数取得 (`/page-count`)
- PDF→画像変換 (`/convert`)
- PDF解析＋JSON変換 (`/analyze`) ★推奨
- 全ページ一括解析 (`/analyze-full`)

## 技術仕様

| 項目 | 値 |
|------|---|
| Gemini Model | `gemini-3-pro-preview` |
| DPI | 300 |
| Max Output Tokens | 65,536 |

## Cloud Runへのデプロイ

```bash
gcloud run deploy pdf-converter \
  --source . \
  --region asia-northeast1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --no-allow-unauthenticated
```

## APIエンドポイント

### GET /health

ヘルスチェック

```json
{"status": "healthy", "gemini_model": "gemini-3-pro-preview", "dpi": 300}
```

### POST /analyze

PDFの指定ページを解析してJSON変換

**Request:**
```json
{
  "fileId": "Google Drive File ID",
  "pageNumber": 1,
  "geminiApiKey": "YOUR_GEMINI_API_KEY"
}
```

**Response:**
```json
{
  "success": true,
  "metadata": {
    "source_file": "report.pdf",
    "page_number": 1,
    "total_pages": 20,
    "page_type": "その1",
    "gemini_model": "gemini-3-pro-preview",
    "dpi": 300
  },
  "structured_data": {...},
  "tables": [...]
}
```

## ライセンス

Private
