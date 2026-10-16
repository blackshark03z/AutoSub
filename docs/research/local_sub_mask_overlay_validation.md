# AutoSub Cloud V2 — Local Subtitle Mask & Vietnamese Overlay Validation (`LOCAL_SUB_MASK_OVERLAY`)

**Document Version:** 1.0.0  
**Date:** 2026-10-16  
**Status:** ARCHITECTURAL CLOSURE & OFFLINE VERIFIED  
**Work Package:** WP-PILOT-001-FINAL  
**Project:** AutoSub Cloud V2  
**Branch:** `feat/pilot-e2e-local-sub-overlay`  
**Worker:** Gemini  
**Tech Lead / Auditor:** ChatGPT  
**Priority:** P0  
**Workflow Standard:** Native Workflow Kit v1.3  

---

## 1. Quyết Định Kiến Trúc & Ranh Giới Bắt Buộc

Kiến trúc **`LOCAL_SUB_MASK_OVERLAY`** là phương án render phụ đề đã được Owner phê duyệt chính thức nhằm thay thế hoàn toàn cơ chế render cũ (dùng FFmpeg `delogo` kết hợp lower-third plate độc lập).

### 1.1 Nguyên Tắc Cốt Lõi
> **"Chỉ che đúng vùng có phụ đề Trung cũ, bằng nền đen vừa đủ, sau đó đặt phụ đề Việt ngay tại vị trí đó với cỡ chữ tương đương."**

### 1.2 Các Hành Vi Bị Cấm Tuyệt Đối (Strict Constraints)
- **CẤM** dải đen toàn chiều ngang video (Full-width black bar).
- **CẤM** lower-third subtitle plate độc lập ở đáy màn hình.
- **CẤM** AI Inpainting để xóa phụ đề (tránh tốn tài nguyên và sinh ảo giác hình ảnh).
- **CẤM** sử dụng FFmpeg `delogo` trong chế độ mới (tránh vết mờ loang lổ).
- **CẤM** cơ chế crop/zoom để cắt bỏ phụ đề.
- **CẤM** mặc định che 12–18% chiều cao màn hình.
- **CẤM** để hộp đen tồn tại trong các khoảng lặng giữa các câu phụ đề.

---

## 2. Đặc Tả Kỹ Thuật 5 Bước (The 5-Step Pipeline)

```mermaid
flowchart TD
    Frame["Input Frame"] --> Step1["Bước 1: DETECT<br/>- Bbox gốc: x, y, w, h<br/>- Chiều cao chữ gốc: char_h<br/>- Hỗ trợ 16:9 & 9:16<br/>- Gán Confidence & Status"]
    Step1 --> Gate{"Confidence >= 0.60<br/>& Status == CONFIRMED?"}
    Gate -- NO --> FailSafe["Bước 5: DETECTION FAILURE<br/>- Trả SUBTITLE_REGION_UNCONFIRMED<br/>- KHÔNG che bừa màn hình<br/>- Lưu evidence kiểm tra"]
    Gate -- YES --> Step2["Bước 2: MASK<br/>- FFmpeg drawbox màu đen đặc @1.0<br/>- Padding 2–4px tại 720p<br/>- KHÔNG mở rộng toàn chiều ngang<br/>- Chỉ bật trong thời gian cue"]
    Step2 --> Step3["Bước 3: OVERLAY<br/>- Phụ đề Việt đặt tại baseline sub Trung<br/>- Căn giữa theo vùng chữ gốc<br/>- Font size = char_h (sai lệch <= 10%)<br/>- Font hỗ trợ Unicode tiếng Việt"]
    Step3 --> Step4{"Bước 4: CÂU DỊCH DÀI?<br/>(text_w > orig_w * 1.15)"}
    Step4 -- "Vừa vặn" --> Render["Tạo ASS với \an5\pos<br/>& Filter: drawbox + subtitles"]
    Step4 -- "Dài" --> Wrap["1. Thử ngắt 2 dòng<br/>2. Giảm nhẹ font <= 10%<br/>3. Nếu vượt 88% w -> Gắn NEEDS_REVIEW"]
    Wrap --> Render
```

### Bước 1 — Detect (Nhận dạng vùng phụ đề gốc)
- **Hỗ trợ đa tỷ lệ khung hình:**  
  - Video ngang 16:9 (`1280x720`): Quét vùng ROI đáy màn hình (`y`: 76%–96%, `x`: 10%–90%).  
  - Video dọc 9:16 (`720x1280`): Tự động chuyển ROI dọc (`y`: 65%–90%, `x`: 8%–92%), không bóp méo khung hình vào kích thước 1280x720.
- **Đo lường hình học:** Xác định `left_x, top_y, right_x, bottom_y` và ước lượng chiều cao ký tự hiển thị thực tế (`text_height`).
- **Chấm điểm độ tin cậy (Confidence):** Dựa trên mật độ điểm ảnh, số lượng thành phần ký tự và độ thẳng hàng theo trục ngang.

### Bước 2 — Mask (Che chữ cũ)
- **Công cụ:** FFmpeg bộ lọc `drawbox` màu đen đặc (`color=black@1.0:t=fill`).
- **Padding:** Tối thiểu 2–4 px quy đổi ở 720p (`pad_px = max(2, min(5, round(3.0 * (height / 720.0))))`), bao phủ trọn vẹn viền và bóng chữ gốc.
- **Thời gian hiệu lực:** Khóa chặt trong khoảng cue: `enable='between(t\,start_time\,end_time)'`. Tự động tắt ngay khi hết cue, không để lại vết đen trong khoảng lặng.

### Bước 3 — Overlay (Đè phụ đề tiếng Việt)
- **Tọa độ & Baseline:** Căn giữa trực tiếp theo tâm của vùng che:  
  `center_x = mask_x + mask_w // 2`  
  `center_y = mask_y + mask_h // 2`
- **Kích thước chữ tương đương:** Font size mục tiêu đặt ở `round(source_char_h * 0.98)`, đảm bảo sai lệch không quá 10% so với chữ Trung gốc.
- **Định dạng hiển thị:** Định dạng ASS override `{\an5\fs{target_font_size}\bord2\shad0\pos(center_x, center_y)}` với màu trắng sáng và viền đen mỏng, dễ đọc trên nền hộp đen đặc.

### Bước 4 — Xử lý câu dịch tiếng Việt dài
Thứ tự ưu tiên xử lý:
1. Giữ nguyên 1 dòng nếu câu dịch tiếng Việt vừa vặn trong phạm vi mở rộng cho phép (`<= orig_w * 1.15`).
2. Nếu câu dài, tự động tách thành **hai dòng cân đối** tại ranh giới từ ngữ (`wrap_subtitle_text`).
3. Nếu hai dòng vẫn hơi dài, cho phép giảm kích thước chữ tối đa 10% (`font_size * 0.90`).
4. Nếu vượt quá giới hạn an toàn (`> 88%` chiều rộng khung hình), đánh dấu trạng thái `NEEDS_REVIEW` kèm lý do kỹ thuật. **Tuyệt đối không tự bung thành một dải đen khổng lồ**.

### Bước 5 — Xử lý trường hợp không nhận dạng được (Detection Failure)
- Nếu độ tin cậy `< 0.60` hoặc không tìm thấy ký tự phụ đề: Trả về trạng thái `SUBTITLE_REGION_UNCONFIRMED`.
- **Fail-safe:** Không tạo hộp đen giả định rộng, không bôi đen đáy video.

---

## 3. Kết Quả Kiểm Thử Nghiệm Thu (Offline Acceptance Tests SUB-01 → SUB-10)

Bộ test tự động `tests/test_local_sub_mask_overlay.py` đã kiểm chứng toàn bộ 10 kịch bản:

| Mã Test | Trường Hợp Thử Nghiệm | Tiêu Chí Bắt Buộc | Kết Quả Thực Tế | Đánh Giá |
| :-: | :--- | :--- | :--- | :---: |
| **SUB-01** | Sub Trung một dòng | Che kín chữ Trung, sub Việt đặt đúng tâm với cỡ chữ tương đương (sai số <= 15%). | Bbox phát hiện `(450, 615, 360, 34)`; mask che trọn vẹn; sub Việt căn giữa `\pos(640, 638)`, font size 33px. | **PASS** |
| **SUB-02** | Sub Trung hai dòng | Mask bao phủ trọn vẹn cả hai dòng chữ gốc. | Bbox bao phủ từ y=585 đến y=662; mask height >= 70px che kín cả hai dòng thoại. | **PASS** |
| **SUB-03** | Sub xuất hiện rồi biến mất | Mask chỉ tồn tại trong thời gian cue; không tồn tại ngoài khoảng thời gian này. | Filter chứa `drawbox=...:enable='between(t\,1.250\,3.750)'`; không dùng `delogo`. | **PASS** |
| **SUB-04** | Câu dịch Việt dài | Ngắt 2 dòng cân đối; nếu vượt biên an toàn thì gắn cờ `NEEDS_REVIEW`, không bung black bar. | Câu dài tự ngắt 2 dòng; câu siêu dài trả `status="NEEDS_REVIEW"`, mask giữ `<= 88%` width. | **PASS** |
| **SUB-05** | Không phát hiện được sub | Cơ chế fail-safe: trả `SUBTITLE_REGION_UNCONFIRMED`, không che bừa bãi. | Frame trống trả về `bbox=None`, filter không sinh drawbox che mù đáy màn hình. | **PASS** |
| **SUB-06** | Video tỉ lệ ngang 16:9 | Bbox ánh xạ chính xác theo tọa độ video ngang 1280x720. | Tọa độ nhận diện đúng ROI đáy ngang: `(440, 620, 400, 35)`. | **PASS** |
| **SUB-07** | Video tỉ lệ dọc 9:16 | Tự động chuyển sang ROI video dọc 720x1280, không dùng ROI video ngang. | Tọa độ nhận diện đúng vùng dọc: `(160, 1050, 400, 40)`. | **PASS** |
| **SUB-08** | Frame có logo/watermark góc trên | Không che nhầm logo ở góc trên hoặc watermark kênh. | Logo góc trên `(40, 40, 150, 40)` bị loại bỏ; chỉ nhận diện đúng sub ở đáy. | **PASS** |
| **SUB-09** | Cues liên tiếp nối đuôi | Chuyển tiếp mượt mà, không nhấp nháy hoặc sót hộp đen giữa hai cue. | 2 mệnh đề drawbox độc lập với timing liên tiếp `1.0s-2.0s` và `2.0s-3.0s`. | **PASS** |
| **SUB-10** | Hồi quy workflow hiện hành | Chế độ legacy vẫn giữ nguyên `delogo` và lower-third plate cho code cũ. | `build_source_replacement_filter(..., mode="legacy")` sinh đủ `delogo` và `drawbox` plate. | **PASS** |

---

## 4. Bằng Chứng Hình Ảnh Trích Xuất (Visual Evidence)

Các khung hình bằng chứng thực tế được trích xuất từ tệp render thật tại `evidence/pilot_runs/run_001/`:

1. **`frame_before_raw_chinese_cue1.png` (t = 2.5s):**  
   Khung hình video gốc chưa xử lý, hiển thị dòng phụ đề tiếng Trung màu trắng viền đen: `陈长安携神兽麒麟重返家族`.
2. **`evidence_cue1_overlay_2_5s.png` (t = 2.5s):**  
   Khung hình sau khi render chế độ `LOCAL_SUB_MASK_OVERLAY`: Chữ tiếng Trung cũ đã được che kín bằng một hộp đen đặc nhỏ gọn vừa khít, đè lên trên là dòng chữ tiếng Việt: `Trần Trường An mang theo thần thú Kỳ Lân trở về gia tộc` cùng cỡ chữ và căn giữa hoàn hảo.
3. **`evidence_gap_no_mask_5_5s.png` (t = 5.5s):**  
   Khung hình trong khoảng lặng giữa Cue 1 và Cue 2: **Hoàn toàn sạch sẽ, không có bất kỳ hộp đen nào tồn tại**.
4. **`evidence_cue2_overlay_8_0s.png` (t = 8.0s):**  
   Khung hình Cue 2: Chữ tiếng Trung `今日便开启逆天修炼之路` được che kín và thay bằng tiếng Việt `Hôm nay mở ra con đường tu luyện nghịch thiên`.

---
*Tài liệu kiểm định kỹ thuật phụ đề theo Work Package WP-PILOT-001-FINAL.*
