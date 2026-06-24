---
name: vision
description: "Image analysis via the Qwen3-VL vision model — OCR, document reading, chart/diagram/photo analysis. Auto-used on incoming images."
version: 1.0.0
metadata:
  hermes:
    tags: [vision, ocr, image-analysis, qwen3-vl, multimodal]
    category: core
---

<!--
Skill: vision
Created: 2026-05-15T15:43:45.342081Z
Created by: agent (self-created)
-->

# Vision — Image Analysis via Qwen3-VL

> Use the P40's Qwen3-VL model for image recognition, OCR, and visual analysis.

## Endpoint

- **URL:** `http://192.168.14.4:8081/v1/chat/completions`
- **Model:** `qwen3-vl` (actual file: Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf)
- **Capabilities:** Image description, OCR, document reading, diagram analysis, chart reading, photo analysis

## When to Use

Use vision AUTOMATICALLY (no need to ask) when:
- User sends a **photo via Telegram** — always analyze it
- User sends an **image file** (png, jpg, gif, webp, bmp, tiff)
- User sends a **PDF** — extract pages as images and OCR them
- Browsing a web page and an **image needs clarification** (e.g., chart, diagram, screenshot)
- User asks about a **screenshot** or **visual content**
- User explicitly asks "what's in this image?" or similar

## How to Call

### From base64 (photos, images, screenshots)

```bash
curl -s http://192.168.14.4:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-vl",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "<YOUR_PROMPT>"},
        {"type": "image_url", "image_url": {"url": "data:<MIME_TYPE>;base64,<BASE64_DATA>"}}
      ]
    }],
    "max_tokens": 1024
  }'
```

### From URL (web images)

```bash
curl -s http://192.168.14.4:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-vl",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "<YOUR_PROMPT>"},
        {"type": "image_url", "image_url": {"url": "<IMAGE_URL>"}}
      ]
    }],
    "max_tokens": 1024
  }'
```

### For OCR / Document Reading

Use prompt: "Read all the text in this document/image. Reproduce it exactly, preserving structure and formatting."

### For PDFs

1. Convert PDF pages to images first:
```bash
# Install if needed: sudo apt install poppler-utils
pdftoppm -png -r 200 <file.pdf> /tmp/pdf_page
```
2. Send each page image to the vision endpoint with OCR prompt.

## Prompting Tips

- **For OCR:** "Read all text. Preserve layout, tables, and formatting."
- **For charts:** "Describe this chart in detail: axes, values, trends, title, legend."
- **For diagrams:** "Describe this diagram. Explain the flow/relationships shown."
- **For screenshots:** "Describe this screenshot in detail. What application/interface is shown? What are the key elements?"
- **For general analysis:** "Describe this image in detail. What do you see?"
- **For comparison:** Send multiple images and ask "Compare these images. What are the differences?"

## Performance Notes

- MoE model: 30B params, only 3B active per token → fast on P40
- Typical response: 1-3 seconds for description, 3-5 for OCR
- Max tokens: use 1024 for descriptions, 2048+ for full document OCR
- If the image is very large, consider resizing first (ImageMagick: `convert input.jpg -resize 1920x1080\> output.jpg`)

## Integration Points

- **Telegram photos:** Base64 encode the photo file and send to vision endpoint
- **Web browsing:** Pass image URLs directly
- **File analysis:** Read local files, base64 encode, send to vision
- **PDF processing:** Convert to images first, then OCR each page

## Error Handling

- If endpoint is unreachable → report P40 may be down, suggest checking `192.168.14.4:8081/health`
- If response is empty/garbled → try with simpler prompt or smaller image
- If timeout (>30s) → image may be too large, suggest resizing

## Direct Access

Always call directly at `192.168.14.4:8081` — do NOT go through the proxy. The proxy is for text model routing only. Vision calls bypass it.
